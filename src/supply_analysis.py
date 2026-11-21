"""Supply shortfall detection: did Joy deliver what the platform warehouse ordered?

Matching logic
--------------
Each PO line already carries its own ``qty_delivered`` and ``delivery_date`` (2-4
days after the Monday PO). We therefore never match deliveries to orders by date
proximity; fill rate is computed per PO line and summed per warehouse x SKU x PO
week. A delivered quantity is only treated as known once its delivery date has
passed (as-of discipline), otherwise the line is "pending" - never "zero".
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from src.feature_engineering import Features, runs, safe_div


def supply_episodes(feat: Features) -> pd.DataFrame:
    """One row per run of consecutive PO weeks with fill rate below the threshold."""
    rows = []
    po = feat.po_week[feat.po_week["has_po"]].sort_values("week")
    latest_known = po.loc[po["fill_rate"].notna(), "week"].max()
    for (plat, wh, sku), g in po.groupby(["platform", "warehouse_id", "sku_code"]):
        g = g[g["fill_rate"].notna()].reset_index(drop=True)
        if g.empty:
            continue
        flags = (g["fill_rate"] < config.FILL_RATE_THRESHOLD).to_numpy()
        for a, b in runs(flags):
            ep = g.iloc[a:b + 1]
            ordered = float(ep["ordered"].sum())
            delivered = float(ep["delivered"].sum())
            prior = g.iloc[max(0, a - config.BASELINE_WEEKS):a]
            rows.append({
                "signal_type": "SUPPLY_SHORTFALL", "platform": plat, "city": ep["city"].iloc[0],
                "warehouse_id": wh, "sku_code": sku, "product_name": ep["product_name"].iloc[0],
                "start_week": ep["week"].min(), "end_week": ep["week"].max(), "n_weeks": len(ep),
                "is_active": bool(ep["week"].max() == latest_known),
                "ordered_units": ordered, "delivered_units": delivered,
                "shortfall_units": ordered - delivered,
                "fill_rate": float(safe_div(delivered, ordered)),
                "min_fill_rate": float(ep["fill_rate"].min()),
                "latest_fill_rate": float(ep["fill_rate"].iloc[-1]),
                "baseline_fill_rate": float(safe_div(prior["delivered"].sum(), prior["ordered"].sum()))
                if len(prior) else np.nan,
                "weekly_detail": [
                    {"po_week": w, "ordered": o, "delivered": d, "fill_rate": f}
                    for w, o, d, f in ep[["week", "ordered", "delivered", "fill_rate"]].itertuples(index=False)
                ],
            })
    return pd.DataFrame(rows)


def supply_scorecard(feat: Features, weeks: int = 4) -> pd.DataFrame:
    """Warehouse x SKU view of the last ``weeks`` PO cycles for the Supply page."""
    po = feat.po_week
    recent = [w for w in feat.weeks if w > feat.latest_week - pd.Timedelta(weeks=weeks)]
    r = po[po["week"].isin(recent)]
    agg = (r.groupby(["platform", "city", "warehouse_id", "sku_code", "product_name"])
           .agg(ordered=("ordered", "sum"), delivered=("delivered", "sum"),
                po_weeks=("has_po", "sum"))
           .reset_index())
    agg["fill_rate"] = safe_div(agg["delivered"], agg["ordered"])
    agg["shortfall_units"] = agg["ordered"] - agg["delivered"]
    last = po[po["week"] == feat.latest_week][["warehouse_id", "sku_code", "ordered", "delivered", "fill_rate", "has_po"]]
    last = last.rename(columns={"ordered": "last_ordered", "delivered": "last_delivered",
                                "fill_rate": "last_fill_rate", "has_po": "last_has_po"})
    return agg.merge(last, on=["warehouse_id", "sku_code"], how="left")


def fill_rate_matrix(feat: Features, by: str = "warehouse_id") -> pd.DataFrame:
    """Weekly fill rate pivot (rows = warehouse or SKU, columns = PO week)."""
    po = feat.po_week[feat.po_week["has_po"] & feat.po_week["delivered"].notna()]
    g = po.groupby([by, "week"]).agg(o=("ordered", "sum"), d=("delivered", "sum")).reset_index()
    g["fill_rate"] = safe_div(g["d"], g["o"])
    return g.pivot(index=by, columns="week", values="fill_rate")
