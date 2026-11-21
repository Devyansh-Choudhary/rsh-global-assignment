"""Availability deterioration at warehouse x SKU level, with store-level breadth.

A week is flagged when the segment's average availability is both
* at least ``AVAIL_DROP_POINTS`` below its own baseline (previous healthy weeks), and
* below the absolute floor ``AVAIL_FLOOR``.

Requiring both avoids flagging a SKU that drifts from 98% to 82% (still fine) and a
SKU that has always run at 72% (a chronic, already-known state, not a new problem).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from src.feature_engineering import SEG_KEYS, Features, runs, sequential_baseline


def _is_drop(v: float, base: float) -> bool:
    return (v < base - config.AVAIL_DROP_POINTS) and (v < config.AVAIL_FLOOR)


def availability_flags(feat: Features) -> pd.DataFrame:
    """seg_week with availability baseline and flag columns added."""
    seg = feat.seg_week.sort_values(SEG_KEYS + ["week"]).copy()
    parts = []
    for _, g in seg.groupby(SEG_KEYS, sort=False):
        base, flags = sequential_baseline(g["avail"].to_numpy(), _is_drop)
        g = g.assign(avail_base=base, avail_flag=flags)
        parts.append(g)
    return pd.concat(parts) if parts else seg.assign(avail_base=np.nan, avail_flag=False)


def availability_episodes(feat: Features, flagged: pd.DataFrame | None = None) -> pd.DataFrame:
    flagged = availability_flags(feat) if flagged is None else flagged
    last3 = _last_days_availability(feat, days=3)
    rows = []
    for key, g in flagged.groupby(SEG_KEYS, sort=False):
        g = g.reset_index(drop=True)
        for a, b in runs(g["avail_flag"].to_numpy()):
            ep = g.iloc[a:b + 1]
            base = float(ep["avail_base"].iloc[0])
            first_drop = base - float(ep["avail"].iloc[0])
            rows.append({
                "signal_type": "AVAILABILITY_DROP", **dict(zip(SEG_KEYS, key)),
                "product_name": ep["product_name"].iloc[0],
                "start_week": ep["week"].min(), "end_week": ep["week"].max(), "n_weeks": len(ep),
                "is_active": bool(ep["week"].max() == feat.latest_week),
                "baseline_avail": base, "min_avail": float(ep["avail"].min()),
                "latest_avail": float(ep["avail"].iloc[-1]),
                "last3d_avail": last3.get((key[2], key[3]), np.nan),
                "pattern": "Sudden" if first_drop >= 2 * config.AVAIL_DROP_POINTS else "Gradual",
                "max_stores_oos": int(ep["stores_oos"].max()),
                "n_stores": int(ep["n_stores"].iloc[-1]),
                "weekly_avail": ep["avail"].round(1).tolist(),
            })
    return pd.DataFrame(rows)


def _last_days_availability(feat: Features, days: int = 3) -> dict:
    """Average availability per (warehouse, SKU) over the final ``days`` days of data."""
    s = feat.sales
    if s.empty:
        return {}
    cutoff = feat.report_date - pd.Timedelta(days=days)
    r = s[s["date"] >= cutoff].groupby(["warehouse_id", "sku_code"])["availability_pct"].mean()
    return r.to_dict()


def store_stockout_table(feat: Features, week: pd.Timestamp | None = None) -> pd.DataFrame:
    """Dark stores x SKUs that were out of stock (mean availability < threshold) in a week."""
    week = feat.latest_week if week is None else week
    sw = feat.store_week[(feat.store_week["week"] == week) & (feat.store_week["avail"] < config.STORE_OOS_THRESHOLD)]
    return sw.merge(feat.products[["sku_code", "product_name"]], on="sku_code", how="left")
