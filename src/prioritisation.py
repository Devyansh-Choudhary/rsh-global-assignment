"""Decision engine: turn detector signals into a short, ranked list of incidents.

Pipeline
--------
1. **Root-cause linking (per warehouse x SKU).** Detectors fire independently, so one
   underlying problem usually trips several of them (a PO stoppage -> stockouts ->
   lower rank -> lower sales). Signals are linked in precedence order
   ``PO_INACTIVITY > SUPPLY_SHORTFALL > AVAILABILITY_DROP > SEARCH_RANK_DROP > SALES_DECLINE``:
   a downstream signal that overlaps an upstream one in time (allowing
   ``CONSEQUENCE_LAG_WEEKS`` for stock to run down) becomes *evidence* for it rather than
   a separate alert. Revenue is therefore counted once per segment-week.
2. **Grouping.** Segment-level incidents with the same root cause, in the same warehouse,
   starting within a week of each other become one incident ("Blinkit Bengaluru
   under-delivered 6 SKUs"), because one person fixes them with one action.
3. **Impact.** Each segment is costed with ``revenue_impact`` and the costs summed.
4. **Scoring** (0-100, fully decomposed so the UI can explain every rank)::

       score = confidence_multiplier x (
                 40 x sqrt(min(1, weekly_revenue_at_risk / impact_cap))   # impact
               + 25 x urgency(status)                                   # urgency
               + 15 x min(1, weeks_active / 4)                          # persistence
               + 20 x (0.7 x share_of_stores_hit + 0.3 x min(1, skus/5))) # breadth

   impact_cap = 1% of last week's network GMV. Severity: Critical >= 70, High >= 50,
   Medium >= 30, else Low.
"""
from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

import config
from src.feature_engineering import Features
from src.formatting import d, inr, inr_range, num, pct
from src.revenue_impact import build_peer_tables, estimate_segment_impact

SEG = ["platform", "city", "warehouse_id", "sku_code"]
STATUS_ORDER = {"Active - worsening": 0, "Active": 1, "Recovering": 2, "Resolved": 3}
TYPE_CODE = {"PO_INACTIVITY": "PO", "SUPPLY_SHORTFALL": "SUP", "AVAILABILITY_DROP": "AVL",
             "SEARCH_RANK_DROP": "RNK", "SALES_DECLINE": "SAL"}


def _overlaps(a0, a1, b0, b1) -> bool:
    return (a0 <= b1) and (b0 <= a1)


def _week(n: int) -> pd.Timedelta:
    return pd.Timedelta(weeks=n)


# --------------------------------------------------------------------------------------
# 1. Link signals into segment-level incidents
# --------------------------------------------------------------------------------------
def link_signals(signals: dict[str, pd.DataFrame]) -> list[dict]:
    """Return segment-level incidents with linked evidence. No signal is counted twice."""
    used: dict[str, set[int]] = {k: set() for k in signals}
    seg_incidents: list[dict] = []

    def rows(kind):
        df = signals.get(kind)
        return [] if df is None or df.empty else list(df.iterrows())

    def find(kind, seg_key, w0, w1):
        out = []
        for i, r in rows(kind):
            if i in used[kind]:
                continue
            if tuple(r[k] for k in SEG) == seg_key and _overlaps(r["start_week"], r["end_week"], w0, w1):
                out.append((i, r))
        return out

    for root_kind in config.ROOT_CAUSE_PRECEDENCE:
        for i, r in rows(root_kind):
            if i in used[root_kind]:
                continue
            used[root_kind].add(i)
            seg_key = tuple(r[k] for k in SEG)
            lag = config.CONSEQUENCE_LAG_WEEKS if root_kind in ("PO_INACTIVITY", "SUPPLY_SHORTFALL") else 0
            w0, w1 = r["start_week"], r["end_week"] + _week(lag)
            inc = {"type": root_kind, **dict(zip(SEG, seg_key)), "product_name": r["product_name"],
                   "root": r.to_dict(), "evidence": {}}
            # Upstream causes absorb downstream symptoms
            for ev_kind in config.ROOT_CAUSE_PRECEDENCE[config.ROOT_CAUSE_PRECEDENCE.index(root_kind) + 1:]:
                hits = find(ev_kind, seg_key, w0, w1)
                if ev_kind == "SEARCH_RANK_DROP" and root_kind == "SEARCH_RANK_DROP":
                    hits = []
                for j, h in hits:
                    used[ev_kind].add(j)
                    inc["evidence"].setdefault(ev_kind, []).append(h.to_dict())
                    if ev_kind == "AVAILABILITY_DROP":  # stock consequences extend the window
                        w1 = max(w1, h["end_week"])
            # A rank drop with collapsing availability is a stock problem, not a marketing one
            if root_kind == "SEARCH_RANK_DROP" and not r.get("avail_stable", True):
                inc["rank_with_stockouts"] = True
            starts = [r["start_week"]] + [e["start_week"] for v in inc["evidence"].values() for e in v]
            ends = [r["end_week"]] + [e["end_week"] for k, v in inc["evidence"].items() for e in v
                                      if k == "AVAILABILITY_DROP"]
            inc["start_week"] = min(starts)
            inc["end_week"] = max(ends)
            avail_active = any(e["is_active"] for e in inc["evidence"].get("AVAILABILITY_DROP", []))
            inc["root_active"] = bool(r["is_active"])
            inc["avail_active"] = avail_active
            seg_incidents.append(inc)
    return seg_incidents


# --------------------------------------------------------------------------------------
# 2. Status / confidence per segment incident
# --------------------------------------------------------------------------------------
def _status(inc: dict, feat: Features) -> str:
    if inc["root_active"]:
        return "Active - worsening" if _worsening(inc, feat) else "Active"
    if inc["avail_active"]:
        return "Recovering"
    return "Resolved"


def _seg_series(feat: Features, inc: dict, col: str) -> pd.Series:
    s = feat.seg_week[(feat.seg_week["warehouse_id"] == inc["warehouse_id"])
                      & (feat.seg_week["sku_code"] == inc["sku_code"])].set_index("week")[col]
    return s.sort_index()


def _worsening(inc: dict, feat: Features) -> bool:
    lw, pw = feat.latest_week, feat.latest_week - _week(1)
    t = inc["type"]
    if t in ("PO_INACTIVITY", "SUPPLY_SHORTFALL", "AVAILABILITY_DROP"):
        a = _seg_series(feat, inc, "avail")
        if lw in a and pw in a and a[lw] < a[pw] - 5:
            return True
        if t == "PO_INACTIVITY" and inc["root"].get("missed_cycles", 0) >= 2:
            return True
        return False
    if t == "SEARCH_RANK_DROP":
        rk = _seg_series(feat, inc, "rank")
        return bool(lw in rk and pw in rk and rk[lw] > rk[pw] + 1)
    if t == "SALES_DECLINE":
        return inc["root"].get("n_weeks", 1) >= 2
    return False


def _confidence(inc: dict) -> tuple[str, str]:
    """Return (level, reason)."""
    t, r, ev = inc["type"], inc["root"], inc["evidence"]
    has_stockouts = bool(ev.get("AVAILABILITY_DROP"))
    if t == "PO_INACTIVITY":
        if r.get("demand_driven"):
            return "Low", "sales were already falling before ordering stopped - may be a deliberate de-range"
        if has_stockouts:
            return "High", "orders stopped for a regularly-ordered SKU and dark-store availability then fell"
        return "Medium", "regular orders stopped; stockouts not visible yet"
    if t == "SUPPLY_SHORTFALL":
        if has_stockouts:
            return "High", "fill rate fell below 80% and availability in the city's dark stores dropped"
        if r.get("n_weeks", 1) >= 2 or r.get("min_fill_rate", 1) < config.SEVERE_FILL_RATE:
            return "Medium", "repeated or severe under-delivery; no stockouts visible yet"
        return "Low", "single short delivery with no visible effect on availability"
    if t == "AVAILABILITY_DROP":
        if r.get("n_weeks", 1) >= 2 and r.get("max_stores_oos", 0) >= 2:
            return "Medium", "sustained availability loss across several stores, but no PO or delivery problem explains it"
        return "Low", "availability fell but purchase orders and deliveries look normal - cause unknown"
    if t == "SEARCH_RANK_DROP":
        sales_hit = r.get("sales_rel_change", 0) <= -0.2 or bool(ev.get("SALES_DECLINE"))
        if r.get("avail_stable") and sales_hit:
            return "High", "rank fell while availability stayed normal, and sales fell vs. the rest of the platform"
        if r.get("avail_stable"):
            return "Medium", "rank fell with stable availability; sales impact not yet significant"
        return "Low", "rank fell at the same time as availability - may be a side-effect of stockouts"
    if t == "SALES_DECLINE":
        if r.get("n_weeks", 1) >= 2:
            return "Medium", "statistically significant decline for 2+ weeks with normal availability and rank"
        return "Low", "one significant week; could still be noise or a local event"
    return "Low", ""


# --------------------------------------------------------------------------------------
# 3. Group into incidents
# --------------------------------------------------------------------------------------
def group_incidents(seg_incs: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for inc in sorted(seg_incs, key=lambda x: (x["warehouse_id"], x["type"], x["root"]["start_week"])):
        for g in groups:
            h = g[0]
            if (h["warehouse_id"] == inc["warehouse_id"] and h["type"] == inc["type"]
                    and abs((h["root"]["start_week"] - inc["root"]["start_week"]).days) <= 7
                    and (h["status"] == "Resolved") == (inc["status"] == "Resolved")
                    and (h["confidence"] == "Low") == (inc["confidence"] == "Low")):
                g.append(inc)
                break
        else:
            groups.append([inc])
    return groups


# --------------------------------------------------------------------------------------
# 4. Score
# --------------------------------------------------------------------------------------
def score_incident(weekly_risk: float, status: str, weeks_active: int, store_share: float, n_skus: int,
                   confidence: str, impact_cap: float) -> dict:
    w = config.SCORE_WEIGHTS
    impact = w["impact"] * float(np.sqrt(min(1.0, max(0.0, weekly_risk) / impact_cap))) if impact_cap > 0 else 0.0
    urgency = w["urgency"] * config.URGENCY_POINTS.get(status, 0.0)
    persistence = w["persistence"] * min(1.0, weeks_active / config.PERSISTENCE_FULL_WEEKS)
    breadth = w["breadth"] * (0.7 * min(1.0, store_share) + 0.3 * min(1.0, n_skus / 5))
    mult = config.CONFIDENCE_MULTIPLIER[confidence]
    total = (impact + urgency + persistence + breadth) * mult
    severity = next(label for cut, label in config.SEVERITY_BANDS if total >= cut)
    return {"score": round(total, 1), "severity": severity, "score_impact": round(impact, 1),
            "score_urgency": round(urgency, 1), "score_persistence": round(persistence, 1),
            "score_breadth": round(breadth, 1), "confidence_multiplier": mult}


# --------------------------------------------------------------------------------------
# 5. Narrative (title, what happened, evidence, action)
# --------------------------------------------------------------------------------------
def _products(segs: list[dict]) -> str:
    names = [s["product_name"] for s in segs]
    return names[0] if len(names) == 1 else f"{len(names)} SKUs"


def _narrative(t: str, segs: list[dict], agg: dict, feat: Features) -> dict:
    h = segs[0]
    r = h["root"]
    plat, city, wh = h["platform"], h["city"], h["warehouse_id"]
    names = ", ".join(s["product_name"].replace("Joy ", "") for s in segs)
    stores = agg["stores_hit"]
    n_st = agg["n_stores"]
    next_monday = feat.report_date
    if t == "SUPPLY_SHORTFALL":
        title = f"{plat} {city}: we delivered only {pct(agg['fill_rate'])} of ordered stock on {_products(segs)}"
        what = (f"Since the PO of {d(r['start_week'], '%d %b')}, Joy has delivered {num(agg['delivered'])} of "
                f"{num(agg['ordered'])} units ordered by {wh} ({pct(agg['fill_rate'])} fill rate, normal is "
                f"{pct(agg['baseline_fill'])}), a shortfall of {num(agg['shortfall'])} units across {names}.")
        action = (f"Supply: dispatch an off-cycle top-up of the {num(agg['latest_shortfall'])}-unit shortfall from "
                  f"the latest PO to {wh} today, confirm depot stock for {names}, and commit to ≥95% fill on "
                  f"the PO of {d(next_monday, '%d %b')}. Find out why these SKUs were short-shipped (allocation, "
                  f"production or picking).") if agg["active"] else (
                  f"Supply: post-mortem on why {wh} was short-shipped; keep fill ≥95% on these SKUs.")
    elif t == "PO_INACTIVITY":
        title = f"{plat} {city}: warehouse stopped ordering {_products(segs)}"
        last_qty = sum(s["root"]["last_po_qty"] for s in segs)
        what = (f"{wh} has raised no PO for {names} for {r['missed_cycles']} weekly cycle(s) "
                f"(last PO {d(r['last_po_week'], '%d %b')}, {num(last_qty)} units), while it kept ordering its other SKUs. "
                f"{r.get('demand_note') or ''}".strip())
        action = (f"{plat} KAM: escalate today - ask {wh} to raise an off-cycle PO of ~{num(agg['avg_order'])} units "
                  f"and check whether {names} is blocked, delisted or mis-configured in their auto-replenishment. "
                  f"Supply: hold stock ready to ship within 48h.") if agg["active"] else (
                  f"{plat} KAM: confirm with {wh} why ordering paused and set an alert for missed POs.")
    elif t == "AVAILABILITY_DROP":
        title = f"{plat} {city}: {_products(segs)} out of stock in {stores} of {n_st} dark stores"
        what = (f"Availability fell from {pct(agg['ref_avail']/100)} to {pct(agg['latest_avail']/100)}, but "
                f"{wh}'s purchase orders and our deliveries look normal, so the stock is getting lost between "
                f"the platform warehouse and its dark stores (or the listing is switched off).")
        action = (f"{plat} KAM: ask {plat} to check dark-store replenishment and listing status for {names} "
                  f"in {city} ({stores} stores affected).")
    elif t == "SEARCH_RANK_DROP":
        title = (f"{plat} {city}: {_products(segs)} fell from #{r['baseline_rank']:.0f} to "
                 f"#{r['latest_rank']:.0f} in search")
        what = (f"Since {d(r['onset_date'], '%a %d %b')}, {names} has ranked #{r['latest_rank']:.0f} on average "
                f"(was #{r['baseline_rank']:.0f}) in {plat}'s '{r.get('category', '')}' search in {city}. Availability "
                f"stayed at {pct(r['avail_during']/100)} (was {pct(r['avail_before']/100)}), so this is a visibility "
                f"problem, not a stock problem. Sales per store-day vs. the platform: "
                f"{pct(r['sales_rel_change'], signed=True)}.")
        action = (f"Marketing: launch sponsored-search ads on {plat} for '{r.get('category', '').lower()}' in {city} "
                  f"for {names} this week; audit the listing (title, images, ratings, price vs competitors). "
                  f"{plat} KAM: ask whether a ranking/listing change happened around {d(r['onset_date'], '%d %b')}.")
    else:  # SALES_DECLINE
        title = f"{plat} {city}: {_products(segs)} selling {pct(-r['rel_change_latest'])} below expected"
        what = (f"Sold {num(r['units_latest'])} units last week vs. ~{num(r['expected_units_latest'])} expected "
                f"from its own trend and the platform's (z = {r['z_latest']:.1f}). Availability "
                f"({pct(r['avail_latest']/100)}) and search rank (#{r['rank_latest']:.0f}) look normal.")
        action = (f"QC Manager: check {plat} {city} for a competitor promotion, price change, rating drop or "
                  f"listing content issue on {names}; re-check next Monday before escalating.")
    return {"title": title, "what_happened": what, "recommended_action": action}


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def build_incidents(feat: Features, signals: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (incidents, incident_segments). Incidents are ranked, most important first."""
    seg_incs = link_signals(signals)
    affected = {(s["warehouse_id"], s["sku_code"]) for s in seg_incs}
    peers = build_peer_tables(feat, affected)
    latest_gmv = feat.seg_week.loc[feat.seg_week["week"] == feat.latest_week, "sales"].sum()
    impact_cap = max(config.IMPACT_CAP_SHARE_OF_GMV * latest_gmv, 1.0)

    seg_rows = []
    for inc in seg_incs:
        inc["status"] = _status(inc, feat)
        inc["confidence"], inc["confidence_reason"] = _confidence(inc)
        r = inc["root"]
        avail_ev = inc["evidence"].get("AVAILABILITY_DROP", [])
        last3 = _last3(feat, inc["warehouse_id"], inc["sku_code"])
        is_active = inc["status"] != "Resolved"
        imp = estimate_segment_impact(
            feat, peers, inc["warehouse_id"], inc["sku_code"], inc["type"], inc["start_week"],
            inc["end_week"], is_active, last3d_avail=last3,
            latest_fill_rate=r.get("latest_fill_rate", np.nan))
        inc["impact"] = imp
        inc["last3d_avail"] = last3
        n_st = _n_stores(feat, inc["warehouse_id"], inc["sku_code"])
        if inc["type"] in ("SEARCH_RANK_DROP", "PO_INACTIVITY"):
            hit = n_st  # every store in the city sees the rank / is fed by the warehouse
        elif avail_ev or inc["type"] == "AVAILABILITY_DROP":
            hit = max([e["max_stores_oos"] for e in avail_ev] + [r.get("max_stores_oos", 0)])
            if is_active:  # stores out of stock right now (last 3 days) may exceed the weekly view
                hit = max(hit, _stores_oos_now(feat, inc["warehouse_id"], inc["sku_code"]))
        else:
            hit = 0
        inc["stores_hit"], inc["n_stores"] = int(hit), int(n_st)
        seg_rows.append(inc)

    groups = group_incidents(seg_rows)
    incidents, segments = [], []
    seen_ids: set[str] = set()
    for g in groups:
        h = g[0]
        t = h["type"]
        status = min((s["status"] for s in g), key=lambda x: STATUS_ORDER[x])  # most urgent member
        conf = _group_confidence([s["confidence"] for s in g])
        conf_reason = next(s["confidence_reason"] for s in g if s["confidence"] == conf)
        start = min(s["start_week"] for s in g)
        root_start = min(s["root"]["start_week"] for s in g)
        last = feat.latest_week if status != "Resolved" else max(s["end_week"] for s in g)
        weeks_active = int((last - start).days // 7) + 1
        I = lambda k: float(np.nansum([s["impact"][k] for s in g]))  # noqa: E731
        agg = {
            "active": status != "Resolved",
            "stores_hit": max(s["stores_hit"] for s in g), "n_stores": max(s["n_stores"] for s in g),
            "ordered": sum(s["root"].get("ordered_units", 0) for s in g),
            "delivered": sum(s["root"].get("delivered_units", 0) for s in g),
            "shortfall": sum(s["root"].get("shortfall_units", 0) for s in g),
            "latest_shortfall": sum(_latest_shortfall(s) for s in g),
            "avg_order": sum(s["root"].get("avg_weekly_order", 0) or 0 for s in g),
            "ref_avail": float(np.nanmean([s["impact"]["ref_avail"] for s in g])),
            "latest_avail": float(np.nanmean([_latest(feat, s, "avail") for s in g])),
        }
        agg["fill_rate"] = agg["delivered"] / agg["ordered"] if agg["ordered"] else np.nan
        bf = [s["root"].get("baseline_fill_rate") for s in g if np.isfinite(s["root"].get("baseline_fill_rate", np.nan))]
        agg["baseline_fill"] = float(np.mean(bf)) if bf else np.nan
        store_share = agg["stores_hit"] / agg["n_stores"] if agg["n_stores"] else 0
        risk_point = I("risk_point")
        sc = score_incident(risk_point, status, weeks_active, store_share, len(g), conf, impact_cap)
        narr = _narrative(t, g, agg, feat)
        units_affected = (agg["shortfall"] if t == "SUPPLY_SHORTFALL"
                          else sum(s["root"].get("units_not_ordered_est", 0) for s in g) if t == "PO_INACTIVITY"
                          else I("lost_units_est"))
        inc_id = f"{TYPE_CODE[t]}-{h['warehouse_id']}-{root_start:%m%d}"
        if inc_id in seen_ids:  # two groups can share warehouse/type/start week
            inc_id = f"{inc_id}-{sum(x.startswith(inc_id) for x in seen_ids) + 1}"
        seen_ids.add(inc_id)
        evidence = _evidence_bullets(t, g, agg, feat)
        incidents.append({
            "incident_id": inc_id, "type": t, "issue": config.ISSUE_LABELS[t], "team": config.OWNER_TEAM[t],
            "platform": h["platform"], "city": h["city"], "warehouse_id": h["warehouse_id"],
            "sku_codes": [s["sku_code"] for s in g], "products": [s["product_name"] for s in g], "n_skus": len(g),
            "start_week": root_start, "impact_start_week": start,
            "onset_date": min(pd.Timestamp(s["root"].get("onset_date", s["root"]["start_week"])) for s in g), "end_week": max(s["end_week"] for s in g),
            "weeks_active": weeks_active, "status": status, "confidence": conf, "confidence_reason": conf_reason,
            "stores_hit": agg["stores_hit"], "n_stores": agg["n_stores"], "store_share": store_share,
            "units_affected": units_affected,
            "observed_ref_weekly": I("ref_weekly_sales"), "observed_latest": I("observed_sales"),
            "observed_decline": I("observed_decline"),
            "lost_low": I("lost_low"), "lost_high": I("lost_high"), "lost_point": I("lost_point"),
            "risk_low": I("risk_low"), "risk_high": I("risk_high"), "risk_point": risk_point,
            "recoverable_next_week": I("recoverable_next_week"),
            "ordered_units": agg["ordered"], "delivered_units": agg["delivered"], "shortfall_units": agg["shortfall"],
            "fill_rate": agg["fill_rate"], "ref_avail": agg["ref_avail"], "latest_avail": agg["latest_avail"],
            **sc, **narr, "evidence": evidence,
            "linked_signals": sorted({k for s in g for k in s["evidence"]}),
            "ref_source": h["impact"]["ref_source"], "alt_method": h["impact"]["alt_method"],
            "impact_cap": impact_cap,
        })
        for s in g:
            imp = s["impact"]
            segments.append({
                "incident_id": inc_id, "type": t, **{k: s[k] for k in SEG}, "product_name": s["product_name"],
                "status": s["status"], "confidence": s["confidence"], "start_week": s["start_week"],
                "end_week": s["end_week"], "stores_hit": s["stores_hit"], "n_stores": s["n_stores"],
                "ordered_units": s["root"].get("ordered_units", np.nan),
                "delivered_units": s["root"].get("delivered_units", np.nan),
                "shortfall_units": s["root"].get("shortfall_units", np.nan),
                "fill_rate": s["root"].get("fill_rate", np.nan),
                "latest_fill_rate": s["root"].get("latest_fill_rate", np.nan),
                "latest_shortfall_units": _latest_shortfall(s),
                "missed_cycles": s["root"].get("missed_cycles", np.nan),
                "last_po_week": s["root"].get("last_po_week", pd.NaT),
                "avg_weekly_order": s["root"].get("avg_weekly_order", np.nan),
                "baseline_rank": s["root"].get("baseline_rank", np.nan),
                "latest_rank": s["root"].get("latest_rank", np.nan),
                "ref_avail": imp["ref_avail"], "latest_avail": _latest(feat, s, "avail"),
                "last3d_avail": s["last3d_avail"],
                "ref_weekly_sales": imp["ref_weekly_sales"], "observed_sales": imp["observed_sales"],
                "lost_low": imp["lost_low"], "lost_high": imp["lost_high"], "lost_point": imp["lost_point"],
                "risk_low": imp["risk_low"], "risk_high": imp["risk_high"], "risk_point": imp["risk_point"],
                "recoverable_next_week": imp["recoverable_next_week"], "ref_weeks": ", ".join(imp["ref_weeks"]),
                "ref_source": imp["ref_source"],
            })

    inc_df = pd.DataFrame(incidents)
    seg_df = pd.DataFrame(segments)
    if inc_df.empty:
        return inc_df, seg_df
    inc_df["_active"] = inc_df["status"].map(lambda s: 0 if s != "Resolved" else 1)
    inc_df = inc_df.sort_values(["_active", "score", "risk_point"], ascending=[True, False, False]).drop(columns="_active")
    inc_df["priority"] = range(1, len(inc_df) + 1)
    inc_df["status_order"] = inc_df["status"].map(STATUS_ORDER)
    inc_df["why_ranked"] = _why_ranked(inc_df)
    return inc_df.reset_index(drop=True), seg_df


def _group_confidence(levels: list[str]) -> str:
    order = ["High", "Medium", "Low"]
    c = Counter(levels)
    top = max(c.values())
    return next(lv for lv in reversed(order) if c.get(lv) == top) if list(c.values()).count(top) > 1 \
        else c.most_common(1)[0][0]


def _latest(feat: Features, s: dict, col: str) -> float:
    v = feat.seg_week[(feat.seg_week["warehouse_id"] == s["warehouse_id"]) & (feat.seg_week["sku_code"] == s["sku_code"])
                      & (feat.seg_week["week"] == feat.latest_week)][col]
    return float(v.iloc[0]) if len(v) else np.nan


def _latest_shortfall(s: dict) -> float:
    det = s["root"].get("weekly_detail")
    if isinstance(det, list) and det:
        last = det[-1]
        return float(last["ordered"] - last["delivered"])
    return 0.0


def _last3(feat: Features, wh: str, sku: str) -> float:
    cutoff = feat.report_date - pd.Timedelta(days=3)
    s = feat.sales[(feat.sales["warehouse_id"] == wh) & (feat.sales["sku_code"] == sku) & (feat.sales["date"] >= cutoff)]
    return float(s["availability_pct"].mean()) if len(s) else np.nan


def _stores_oos_now(feat: Features, wh: str, sku: str, days: int = 3) -> int:
    cutoff = feat.report_date - pd.Timedelta(days=days)
    s = feat.sales[(feat.sales["warehouse_id"] == wh) & (feat.sales["sku_code"] == sku) & (feat.sales["date"] >= cutoff)]
    per_store = s.groupby("dark_store_id")["availability_pct"].mean()
    return int((per_store < config.STORE_OOS_THRESHOLD).sum())


def _n_stores(feat: Features, wh: str, sku: str) -> int:
    v = feat.seg_week[(feat.seg_week["warehouse_id"] == wh) & (feat.seg_week["sku_code"] == sku)
                      & (feat.seg_week["week"] == feat.latest_week)]["n_stores"]
    return int(v.iloc[0]) if len(v) else 0


def _evidence_bullets(t: str, segs: list[dict], agg: dict, feat: Features) -> list[str]:
    out = []
    for s in segs:
        r = s["root"]
        name = s["product_name"].replace("Joy ", "")
        if t == "SUPPLY_SHORTFALL":
            det = r.get("weekly_detail", [])
            weeks = "; ".join(f"{d(x['po_week'], '%d %b')}: {num(x['delivered'])}/{num(x['ordered'])} ({pct(x['fill_rate'])})"
                              for x in det)
            out.append(f"{name} - delivered/ordered by PO week: {weeks}")
        elif t == "PO_INACTIVITY":
            q = ", ".join(num(x) for x in r.get("recent_order_qty", []))
            out.append(f"{name} - last PO {d(r['last_po_week'], '%d %b')}; previous weekly orders: {q} units; "
                       f"missed {r['missed_cycles']} cycle(s); other SKUs at {s['warehouse_id']} still ordered "
                       f"({pct(r['warehouse_other_sku_activity'])} of them)")
        elif t == "SEARCH_RANK_DROP":
            out.append(f"{name} - weekly avg rank: {' → '.join(f'{x:.1f}' for x in r.get('weekly_rank', []))} "
                       f"(baseline {r['baseline_rank']:.1f}); units/store-day {r['units_psd_before']:.2f} → "
                       f"{r['units_psd_latest']:.2f}")
        elif t == "AVAILABILITY_DROP":
            out.append(f"{name} - weekly availability {', '.join(f'{x:.0f}%' for x in r.get('weekly_avail', []))} "
                       f"vs baseline {r['baseline_avail']:.0f}%; up to {r['max_stores_oos']} stores below 50%")
        elif t == "SALES_DECLINE":
            out.append(f"{name} - {num(r['units_latest'])} units vs ~{num(r['expected_units_latest'])} expected "
                       f"(z = {r['z_latest']:.1f})")
        for e in s["evidence"].get("AVAILABILITY_DROP", []):
            out.append(f"{name} - availability {', '.join(f'{x:.0f}%' for x in e['weekly_avail'])} "
                       f"(baseline {e['baseline_avail']:.0f}%) from {d(e['start_week'], '%d %b')}; "
                       f"up to {e['max_stores_oos']}/{e['n_stores']} stores below 50%; last 3 days {e['last3d_avail']:.0f}%")
        for e in s["evidence"].get("SEARCH_RANK_DROP", []):
            out.append(f"{name} - search rank also slipped to #{e['latest_rank']:.0f} (from #{e['baseline_rank']:.0f}), "
                       f"consistent with stockouts")
        for e in s["evidence"].get("SALES_DECLINE", []):
            out.append(f"{name} - sales significantly below expected in {e['n_weeks']} week(s) "
                       f"({num(e['units_latest'])} vs ~{num(e['expected_units_latest'])} units in the latest)")
        imp = s["impact"]
        if imp["ref_weeks"]:
            out.append(f"{name} - reference ({imp['ref_source']}: {', '.join(imp['ref_weeks'])}): "
                       f"{inr(imp['ref_weekly_sales'])}/week at {imp['ref_avail']:.0f}% availability")
    return out


def _why_ranked(df: pd.DataFrame) -> list[str]:
    out = []
    for i, r in enumerate(df.itertuples()):
        parts = [f"score {r.score:.0f}/100 = impact {r.score_impact:.0f} + urgency {r.score_urgency:.0f} + "
                 f"persistence {r.score_persistence:.0f} + breadth {r.score_breadth:.0f}, × {r.confidence_multiplier:.2f} "
                 f"({r.confidence.lower()} confidence)"]
        drivers = []
        if r.risk_point > 0:
            drivers.append(f"{inr_range(r.risk_low, r.risk_high)} weekly revenue at risk")
        drivers.append(r.status.lower())
        drivers.append(f"{r.weeks_active} week(s) running")
        if r.n_stores:
            drivers.append(f"{r.stores_hit}/{r.n_stores} stores affected")
        txt = "; ".join(parts) + ". Drivers: " + ", ".join(drivers) + "."
        if i > 0:
            prev = df.iloc[i - 1]
            gaps = {k: prev[f"score_{k}"] - getattr(r, f"score_{k}") for k in ("impact", "urgency", "persistence", "breadth")}
            k = max(gaps, key=gaps.get)
            if gaps[k] > 0:
                txt += f" Ranked below #{i} mainly on {k} ({gaps[k]:.0f} pts lower)."
        out.append(txt)
    return out
