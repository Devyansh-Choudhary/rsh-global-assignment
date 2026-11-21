"""Sales anomalies, normalised against the rest of the platform.

Why peer-normalise? Between 19 Oct and 8 Nov every platform ran deeper festive
discounts (~14-17% off MRP vs ~7-10% normally) and sales per store rose; in the week of
9 Nov discounts returned to normal and network sales fell ~11%. A naive week-on-week
rule would flag almost every SKU in the country as "declining" on 16 Nov.

We instead track each segment's sales per store-day *relative to its platform's units
per store-day*, and only flag drops that are statistically significant for the volume involved. Platform-wide effects (promotions, festivals, payday, new-store ramp)
cancel out; what is left is the segment's own performance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from src.feature_engineering import SEG_KEYS, Features, runs, safe_div


def sales_flags(feat: Features) -> pd.DataFrame:
    """Add expected units, z-score and flag to every segment-week.

    expected_units = baseline_share x platform_units_per_store_day x store_days
    where baseline_share is the segment's units relative to the platform average over its
    previous healthy weeks. The z-score uses Poisson variance of this week's count plus
    the uncertainty of the baseline itself, so low-volume SKUs need a bigger relative
    drop before they are flagged.
    """
    seg = feat.seg_week.sort_values(SEG_KEYS + ["week"]).copy()
    pw = feat.platform_week.copy()
    pw["units_psd"] = safe_div(pw["units"], pw["store_days"])
    pw = pw.set_index(["platform", "week"])
    seg["platform_upsd"] = [pw["units_psd"].get((p, w), np.nan) for p, w in zip(seg["platform"], seg["week"])]
    seg["platform_psd"] = [pw["sales_psd"].get((p, w), np.nan) for p, w in zip(seg["platform"], seg["week"])]
    seg["exp_factor"] = seg["platform_upsd"] * seg["store_days"]
    seg["rel_sales"] = safe_div(seg["units"], seg["exp_factor"])
    parts = []
    for _, g in seg.groupby(SEG_KEYS, sort=False):
        units = g["units"].to_numpy(dtype=float)
        fac = g["exp_factor"].to_numpy(dtype=float)
        price = safe_div(g["sales"].to_numpy(dtype=float), units)
        n = len(g)
        exp_u, z, base_rel, flag = (np.full(n, np.nan) for _ in range(4))
        flag = np.zeros(n, dtype=bool)
        hist: list[int] = []
        for i in range(n):
            if hist:
                idx = hist[-config.BASELINE_WEEKS:]
                bu, bf = units[idx].sum(), fac[idx].sum()
                if bf > 0 and bu > 0:
                    base_rel[i] = bu / bf
                    exp_u[i] = base_rel[i] * fac[i]
                    var = exp_u[i] + exp_u[i] ** 2 / bu
                    z[i] = (units[i] - exp_u[i]) / np.sqrt(var)
                    rel_drop = 1 - units[i] / exp_u[i]
                    avg_price = np.nanmean(price[idx]) if np.isfinite(price[idx]).any() else 0
                    material = exp_u[i] * avg_price >= config.MIN_WEEKLY_SALES_INR
                    flag[i] = (rel_drop >= config.SALES_DROP_RELATIVE) and (z[i] <= config.SALES_Z_THRESHOLD) and material
            if not flag[i]:
                hist.append(i)
        parts.append(g.assign(expected_units=exp_u, sales_z=z, rel_base=base_rel, sales_flag=flag))
    return pd.concat(parts) if parts else seg.assign(expected_units=np.nan, sales_z=np.nan, rel_base=np.nan,
                                                     sales_flag=False)


def sales_episodes(feat: Features, flagged: pd.DataFrame | None = None) -> pd.DataFrame:
    flagged = sales_flags(feat) if flagged is None else flagged
    rows = []
    for key, g in flagged.groupby(SEG_KEYS, sort=False):
        g = g.reset_index(drop=True)
        for a, b in runs(g["sales_flag"].to_numpy()):
            ep = g.iloc[a:b + 1]
            pre = g.iloc[max(0, a - config.BASELINE_WEEKS):a]
            rows.append({
                "signal_type": "SALES_DECLINE", **dict(zip(SEG_KEYS, key)),
                "product_name": ep["product_name"].iloc[0],
                "start_week": ep["week"].min(), "end_week": ep["week"].max(), "n_weeks": len(ep),
                "is_active": bool(ep["week"].max() == feat.latest_week),
                "rel_change_latest": float(safe_div(ep["units"].iloc[-1] - ep["expected_units"].iloc[-1],
                                                    ep["expected_units"].iloc[-1])),
                "z_latest": float(ep["sales_z"].iloc[-1]),
                "units_latest": float(ep["units"].iloc[-1]),
                "expected_units_latest": float(ep["expected_units"].iloc[-1]),
                "sales_before_weekly": float(pre["sales"].mean()) if len(pre) else np.nan,
                "sales_latest": float(ep["sales"].iloc[-1]),
                "avail_before": float(pre["avail"].mean()) if len(pre) else np.nan,
                "avail_latest": float(ep["avail"].iloc[-1]),
                "rank_before": float(pre["rank"].mean()) if len(pre) else np.nan,
                "rank_latest": float(ep["rank"].iloc[-1]),
            })
    return pd.DataFrame(rows)


def wow_table(feat: Features, by: list[str]) -> pd.DataFrame:
    """Latest week vs previous week, raw and like-for-like (per store-day), for any grouping."""
    lw, pw_ = feat.latest_week, feat.latest_week - pd.Timedelta(days=7)
    s = feat.seg_week[feat.seg_week["week"].isin([lw, pw_])]
    g = s.groupby(by + ["week"]).agg(sales=("sales", "sum"), units=("units", "sum"),
                                     store_days=("store_days", "sum")).reset_index()
    cur = g[g["week"] == lw].set_index(by)
    prev = g[g["week"] == pw_].set_index(by)
    out = pd.DataFrame({
        "sales": cur["sales"], "sales_prev": prev["sales"], "units": cur["units"],
    })
    out["wow_pct"] = safe_div(out["sales"] - out["sales_prev"], out["sales_prev"]) * 100
    psd_c = cur["sales"] / cur["store_days"]
    psd_p = prev["sales"] / prev["store_days"]
    out["lfl_wow_pct"] = safe_div(psd_c - psd_p, psd_p) * 100
    return out.reset_index()
