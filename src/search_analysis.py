"""Search-rank deterioration (lower number = better position).

A week is flagged when the segment's mean rank is ``RANK_DROP_POSITIONS`` or more
positions worse than its baseline. The episode is then characterised with the two
facts that decide who owns it:

* **Availability context** - if availability also fell, the rank loss is probably a
  *consequence* of stockouts (apps demote out-of-stock items); the issue is handed to
  the supply/availability incident, not marketing.
* **Sales context** - sales per store-day relative to the rest of the platform. A
  visibility problem should show up in sales; if it does not, confidence is lowered.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from src.feature_engineering import SEG_KEYS, Features, runs, safe_div, sequential_baseline


def _is_rank_drop(v: float, base: float) -> bool:
    return (v - base) >= config.RANK_DROP_POSITIONS


def rank_flags(feat: Features) -> pd.DataFrame:
    seg = feat.seg_week.sort_values(SEG_KEYS + ["week"]).copy()
    pw = feat.platform_week.set_index(["platform", "week"])["sales_psd"]
    seg["platform_psd"] = [pw.get((p, w), np.nan) for p, w in zip(seg["platform"], seg["week"])]
    seg["rel_sales"] = safe_div(seg["sales_psd"], seg["platform_psd"])
    parts = []
    for _, g in seg.groupby(SEG_KEYS, sort=False):
        base, flags = sequential_baseline(g["rank"].to_numpy(), _is_rank_drop)
        parts.append(g.assign(rank_base=base, rank_flag=flags))
    return pd.concat(parts) if parts else seg.assign(rank_base=np.nan, rank_flag=False)


def rank_episodes(feat: Features, flagged: pd.DataFrame | None = None) -> pd.DataFrame:
    flagged = rank_flags(feat) if flagged is None else flagged
    rows = []
    for key, g in flagged.groupby(SEG_KEYS, sort=False):
        g = g.reset_index(drop=True)
        for a, b in runs(g["rank_flag"].to_numpy()):
            ep = g.iloc[a:b + 1]
            pre = g.iloc[max(0, a - config.BASELINE_WEEKS):a]
            avail_before = float(pre["avail"].mean()) if len(pre) else np.nan
            avail_during = float(ep["avail"].mean())
            rel_before = float(pre["rel_sales"].mean()) if len(pre) else np.nan
            rel_latest = float(ep["rel_sales"].iloc[-1])
            sales_rel_change = float(safe_div(rel_latest - rel_before, rel_before))
            units_before = float(pre["units_psd"].mean()) if len(pre) else np.nan
            onset = _onset_day(feat, key, ep["week"].min(), float(ep["rank_base"].iloc[0]))
            rows.append({
                "signal_type": "SEARCH_RANK_DROP", **dict(zip(SEG_KEYS, key)),
                "product_name": ep["product_name"].iloc[0], "category": ep["category"].iloc[0],
                "start_week": ep["week"].min(), "end_week": ep["week"].max(), "n_weeks": len(ep),
                "is_active": bool(ep["week"].max() == feat.latest_week),
                "onset_date": onset,
                "baseline_rank": float(ep["rank_base"].iloc[0]), "latest_rank": float(ep["rank"].iloc[-1]),
                "worst_rank": float(ep["rank"].max()),
                "avail_before": avail_before, "avail_during": avail_during,
                "avail_stable": bool(abs(avail_during - avail_before) <= config.AVAIL_STABLE_POINTS)
                if not np.isnan(avail_before) else False,
                "units_psd_before": units_before, "units_psd_latest": float(ep["units_psd"].iloc[-1]),
                "sales_rel_change": sales_rel_change,
                "weekly_rank": ep["rank"].round(1).tolist(),
            })
    return pd.DataFrame(rows)


def _onset_day(feat: Features, key: tuple, week: pd.Timestamp, base: float) -> pd.Timestamp:
    """First day (from the flagged week's Monday, looking back 7 days) the daily mean rank crossed the threshold."""
    _, _, wh, sku = key
    s = feat.sales[(feat.sales["warehouse_id"] == wh) & (feat.sales["sku_code"] == sku)
                   & (feat.sales["date"] >= week - pd.Timedelta(days=7)) & (feat.sales["date"] < week + pd.Timedelta(days=7))]
    daily = s.groupby("date")["search_rank"].mean()
    hit = daily[daily - base >= config.RANK_DROP_POSITIONS]
    return hit.index.min() if len(hit) else week


def best_seller_flags(feat: Features) -> pd.DataFrame:
    """Rank of each SKU's sales within its category for each platform-city (1 = best seller)."""
    s = feat.seg_week.groupby(["platform", "city", "category", "sku_code"])["sales"].sum().reset_index()
    s["category_sales_rank"] = s.groupby(["platform", "city", "category"])["sales"].rank(ascending=False, method="first")
    return s
