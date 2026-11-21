"""Revenue impact estimation - transparent, traceable, and labelled as estimates.

Three different numbers are reported, and never mixed up:

1. **Observed decline** (fact) - the segment's average weekly sales in its reference weeks
   vs. its sales in the latest affected week. No adjustment; may include seasonality.
2. **Estimated lost sales to date** (estimate, range) - sum over affected weeks of
   ``expected - actual``, where *expected* is what the segment would have sold at its
   pre-incident rate. Two independent methods give the low/high ends of the range:

   * **Peer-trend counterfactual** - pre-incident sales per store-day x this week's
     store-days x the platform's like-for-like trend (unaffected segments only). This
     removes festive promotions and new-store effects.
   * **Availability gap** (stockout issues) - expected sales x the share of availability
     lost vs. the reference weeks. **SKU-trend** (visibility / sales issues) - same as
     the first method but trended by the same SKU in the platform's other cities.

3. **Weekly revenue at risk** (estimate, range) - what the issue will cost next week if
   nothing is done. For active issues: last week's loss rate, and the rate implied by the
   last three days of availability. PO stoppages also include full exposure (no stock
   arriving at all). Resolved issues have zero forward risk.
4. **Recoverable next week** - weekly risk x the share of the week left after the fix's
   lead time (``config.RECOVERY_FRACTION``).

Reference weeks are the ``BASELINE_WEEKS`` weeks before the issue started. If the issue
starts in the first week of data, the weeks after recovery are used and the estimate is
marked as such.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
from src.feature_engineering import Features, safe_div

STOCK_TYPES = {"PO_INACTIVITY", "SUPPLY_SHORTFALL", "AVAILABILITY_DROP"}


@dataclass
class PeerTables:
    platform_psd: pd.Series   # (platform, week) -> sales per store-day of unaffected segments
    sku_psd: pd.Series        # (platform, sku, week) -> same SKU in unaffected warehouses


def build_peer_tables(feat: Features, affected: set[tuple[str, str]]) -> PeerTables:
    """Peer trends computed only from segments not involved in any incident."""
    seg = feat.seg_week
    mask = ~pd.Series([(w, s) in affected for w, s in zip(seg["warehouse_id"], seg["sku_code"])], index=seg.index)
    clean = seg[mask]
    p = clean.groupby(["platform", "week"])[["sales", "store_days"]].sum()
    k = clean.groupby(["platform", "sku_code", "week"])[["sales", "store_days"]].sum()
    return PeerTables(
        platform_psd=pd.Series(safe_div(p["sales"], p["store_days"]), index=p.index),
        sku_psd=pd.Series(safe_div(k["sales"], k["store_days"]), index=k.index),
    )


def _ratio(series: pd.Series, key_now: tuple, keys_ref: list[tuple]) -> float:
    ref = [series.get(k, np.nan) for k in keys_ref]
    ref = np.nanmean(ref) if np.isfinite(ref).any() else np.nan
    now = series.get(key_now, np.nan)
    r = safe_div(now, ref)
    return float(r) if np.isfinite(r) else 1.0


def estimate_segment_impact(feat: Features, peers: PeerTables, warehouse_id: str, sku_code: str,
                            issue_type: str, start_week: pd.Timestamp, end_week: pd.Timestamp,
                            is_active: bool, last3d_avail: float = np.nan,
                            latest_fill_rate: float = np.nan) -> dict:
    seg = feat.seg_week
    g = seg[(seg["warehouse_id"] == warehouse_id) & (seg["sku_code"] == sku_code)].sort_values("week")
    if g.empty:
        return _empty()
    platform = g["platform"].iloc[0]

    ref = g[g["week"] < start_week].tail(config.BASELINE_WEEKS)
    ref_source = "pre-incident weeks"
    if ref.empty:
        ref = g[g["week"] > end_week].head(config.BASELINE_WEEKS)
        ref_source = "post-recovery weeks (incident started in the first week of data)"
    if ref.empty or ref["store_days"].sum() == 0:
        return _empty()

    base_psd = float(ref["sales"].sum() / ref["store_days"].sum())
    ref_avail = float(np.average(ref["avail"], weights=ref["store_days"]))
    ref_weekly_sales = float(ref["sales"].mean())
    ref_price = float(safe_div(ref["sales"].sum(), ref["units"].sum())) if ref["units"].sum() else np.nan
    ref_keys_p = [(platform, w) for w in ref["week"]]
    ref_keys_k = [(platform, sku_code, w) for w in ref["week"]]

    last_impact_week = feat.latest_week if is_active else end_week
    imp = g[(g["week"] >= start_week) & (g["week"] <= last_impact_week)]
    weekly = []
    for r in imp.itertuples():
        idx_p = _ratio(peers.platform_psd, (platform, r.week), ref_keys_p)
        expected = base_psd * r.store_days * idx_p
        m1 = max(0.0, expected - r.sales)
        if issue_type in STOCK_TYPES:
            gap = max(0.0, (ref_avail - r.avail) / ref_avail) if ref_avail > 0 else 0.0
            m2 = expected * gap
            m2_name = "availability gap"
        else:
            idx_k = _ratio(peers.sku_psd, (platform, sku_code, r.week), ref_keys_k)
            m2 = max(0.0, base_psd * r.store_days * idx_k - r.sales)
            m2_name = "SKU-trend counterfactual"
        weekly.append({"week": r.week, "actual": float(r.sales), "expected": expected,
                       "loss_peer": m1, "loss_alt": m2, "avail": float(r.avail), "store_days": int(r.store_days)})
    wk = pd.DataFrame(weekly)
    if wk.empty:
        return _empty()

    lost_a, lost_b = wk["loss_peer"].sum(), wk["loss_alt"].sum()
    lost_low, lost_high = min(lost_a, lost_b), max(lost_a, lost_b)
    lost_point = (lost_a + lost_b) / 2

    risk_low = risk_high = risk_point = 0.0
    if is_active:
        last = wk.iloc[-1]
        candidates = [last["loss_peer"], last["loss_alt"]]
        if issue_type in STOCK_TYPES and np.isfinite(last3d_avail) and ref_avail > 0:
            candidates.append(last["expected"] * max(0.0, (ref_avail - last3d_avail) / ref_avail))
        if issue_type == "PO_INACTIVITY":
            candidates.append(last["expected"])  # full exposure: nothing is being ordered
        if issue_type == "SUPPLY_SHORTFALL" and np.isfinite(latest_fill_rate):
            candidates.append(last["expected"] * (1 - latest_fill_rate))  # share of next week's stock missing
        # Range = min..max of the methods; point estimate = their simple mean.
        risk_low, risk_high = float(min(candidates)), float(max(candidates))
        risk_point = float(np.mean(candidates))
    recoverable = risk_point * config.RECOVERY_FRACTION.get(issue_type, 0.3)

    latest_row = wk.iloc[-1] if is_active else wk.loc[wk["actual"].idxmin()]
    return {
        "ref_source": ref_source, "ref_weeks": [w.strftime("%d %b") for w in ref["week"]],
        "ref_weekly_sales": ref_weekly_sales, "ref_avail": ref_avail, "ref_sales_psd": base_psd,
        "ref_price": ref_price,
        "observed_week": latest_row["week"], "observed_sales": float(latest_row["actual"]),
        "observed_decline": ref_weekly_sales - float(latest_row["actual"]),
        "observed_decline_pct": float(safe_div(float(latest_row["actual"]) - ref_weekly_sales, ref_weekly_sales)),
        "lost_low": float(lost_low), "lost_high": float(lost_high), "lost_point": float(lost_point),
        "lost_units_est": float(safe_div(lost_point, ref_price)) if np.isfinite(ref_price) else np.nan,
        "risk_low": risk_low, "risk_high": risk_high, "risk_point": risk_point,
        "recoverable_next_week": float(recoverable),
        "expected_weekly_now": float(wk.iloc[-1]["expected"]),
        "alt_method": m2_name, "weekly": wk,
    }


def _empty() -> dict:
    return {"ref_source": "insufficient history", "ref_weeks": [], "ref_weekly_sales": np.nan, "ref_avail": np.nan,
            "ref_sales_psd": np.nan, "ref_price": np.nan, "observed_week": pd.NaT, "observed_sales": np.nan,
            "observed_decline": np.nan, "observed_decline_pct": np.nan, "lost_low": 0.0, "lost_high": 0.0,
            "lost_point": 0.0, "lost_units_est": np.nan, "risk_low": 0.0, "risk_high": 0.0, "risk_point": 0.0,
            "recoverable_next_week": 0.0, "expected_weekly_now": np.nan, "alt_method": "", "weekly": pd.DataFrame()}
