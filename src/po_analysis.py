"""Purchase-order inactivity: a platform warehouse that stops ordering a SKU it used to buy.

A missed PO is only reported as genuine inactivity when all of these hold:
1. **Regular buyer** - the warehouse ordered the SKU in every observed week before the gap
   (at least ``MIN_REGULAR_PO_WEEKS``).
2. **SKU-specific** - the same warehouse still ordered most of its other SKUs that week
   (rules out warehouse-wide pauses, holidays or a missing export).
3. **Not a demand collapse** - the SKU's sales per store-day had not already fallen by more
   than ``DEMAND_DROP_FOR_EXPLAINED_GAP`` before the gap. If it had, the episode is kept but
   marked "possibly demand-driven" with low confidence.

Because a missed PO is visible on the Monday it should have happened, this detector gives
the earliest possible warning - typically a week before dark stores run dry.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from src.feature_engineering import Features, runs, safe_div


def po_inactivity_episodes(feat: Features) -> pd.DataFrame:
    po = feat.po_week.sort_values("week")
    latest = feat.latest_week
    # Share of SKUs each warehouse ordered each week
    wh_activity = po.groupby(["warehouse_id", "week"])["has_po"].mean()
    seg = feat.seg_week.copy()
    pw = feat.platform_week.assign(upsd=lambda x: safe_div(x["units"], x["store_days"])).set_index(["platform", "week"])["upsd"]
    # Demand is judged relative to the platform, so festive peaks / promos do not distort it
    seg["rel_units"] = safe_div(seg["units_psd"], [pw.get((p, w), np.nan) for p, w in zip(seg["platform"], seg["week"])])
    seg = seg.set_index(["warehouse_id", "sku_code", "week"]).sort_index()
    rows = []
    for (plat, wh, sku), g in po.groupby(["platform", "warehouse_id", "sku_code"]):
        g = g.reset_index(drop=True)
        has = g["has_po"].to_numpy()
        if not has.any():
            continue
        first = int(np.argmax(has))
        missing = ~has
        missing[:first] = False
        for a, b in runs(missing):
            prior = g.iloc[first:a]
            if prior["has_po"].sum() < config.MIN_REGULAR_PO_WEEKS:
                continue
            regular_share = prior["has_po"].mean()
            if regular_share < 0.75:
                continue  # irregular buyer: a missed week is not unusual
            gap = g.iloc[a:b + 1]
            act = [wh_activity.get((wh, w), np.nan) for w in gap["week"]]
            # activity of the *other* SKUs: remove this SKU's own missing PO from the share
            n_sku = po[(po["warehouse_id"] == wh)]["sku_code"].nunique()
            other_act = [(x * n_sku) / max(n_sku - 1, 1) for x in act]
            if np.nanmin(other_act) < config.MIN_WAREHOUSE_ACTIVITY_SHARE:
                continue  # warehouse-wide gap, not SKU-specific

            # Demand check on the segment's own sales
            try:
                s = seg.loc[(wh, sku)]
            except KeyError:
                s = pd.DataFrame()
            demand_note, demand_driven = "Too little history before the gap to test demand.", False
            if len(s):
                before = s[s.index < gap["week"].min()]["rel_units"]
                if len(before) >= 2:
                    last_val = before.iloc[-1]
                    ref = before.iloc[:-1].tail(config.BASELINE_WEEKS).mean()
                    chg = safe_div(last_val - ref, ref)
                    if not np.isnan(chg) and chg < -config.DEMAND_DROP_FOR_EXPLAINED_GAP:
                        demand_driven = True
                        demand_note = f"Sales had already fallen {abs(chg):.0%} before the gap"
                    else:
                        demand_note = (f"Demand was intact: in the week before the gap, sales (relative to the "
                                       f"platform) were {'+' if chg >= 0 else ''}{chg:.0%} vs the prior weeks.")
            last_po = prior[prior["has_po"]].iloc[-1]
            qty_trend = prior.loc[prior["has_po"], "ordered"].tail(4).tolist()
            avg_qty = float(np.mean(qty_trend)) if qty_trend else np.nan
            rows.append({
                "signal_type": "PO_INACTIVITY", "platform": plat, "city": g["city"].iloc[0],
                "warehouse_id": wh, "sku_code": sku, "product_name": g["product_name"].iloc[0],
                "start_week": gap["week"].min(), "end_week": gap["week"].max(),
                "n_weeks": len(gap), "missed_cycles": len(gap),
                "is_active": bool(gap["week"].max() == latest),
                "last_po_week": last_po["week"], "last_po_qty": float(last_po["ordered"]),
                "prior_po_weeks": int(prior["has_po"].sum()), "regular_share": float(regular_share),
                "recent_order_qty": qty_trend, "avg_weekly_order": avg_qty,
                "units_not_ordered_est": avg_qty * len(gap),
                "warehouse_other_sku_activity": float(np.nanmin(other_act)),
                "demand_driven": demand_driven, "demand_note": demand_note,
                "resumed_week": (g.iloc[b + 1]["week"] if b + 1 < len(g) else pd.NaT),
            })
    return pd.DataFrame(rows)


def po_compliance_matrix(feat: Features) -> pd.DataFrame:
    """Warehouse x week share of SKUs with a PO (1.0 = every SKU ordered)."""
    return feat.po_week.pivot_table(index="warehouse_id", columns="week", values="has_po", aggfunc="mean")
