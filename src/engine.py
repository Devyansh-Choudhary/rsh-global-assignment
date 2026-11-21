"""Orchestrates the full pipeline: load -> clean -> features -> detect -> prioritise.

``run_engine`` produces everything the app, PDF and CSV need for one report Monday, so
all three outputs are always consistent with each other. ``run_backtest`` replays the
engine for every past Monday using only the data available on that day.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import config
from src import availability_analysis, po_analysis, sales_analysis, search_analysis, supply_analysis
from src.data_cleaning import CleanData, clean_all
from src.data_loader import load_raw
from src.feature_engineering import Features, build_features, safe_div
from src.prioritisation import build_incidents


@dataclass
class EngineResult:
    features: Features
    signals: dict[str, pd.DataFrame]
    incidents: pd.DataFrame
    segments: pd.DataFrame
    kpis: dict
    flags: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def report_date(self) -> pd.Timestamp:
        return self.features.report_date

    @property
    def priority(self) -> pd.DataFrame:
        """The Monday list: active / recovering issues of Medium severity or above, max N."""
        inc = self.incidents
        if inc.empty:
            return inc
        p = inc[(inc["status"] != "Resolved") & (inc["severity"].isin(["Critical", "High", "Medium"]))]
        return p.head(config.MAX_PRIORITY_ISSUES)

    @property
    def watchlist(self) -> pd.DataFrame:
        inc = self.incidents
        if inc.empty:
            return inc
        return inc[(inc["status"] != "Resolved") & ~inc["incident_id"].isin(self.priority["incident_id"])]

    @property
    def resolved(self) -> pd.DataFrame:
        inc = self.incidents
        return inc if inc.empty else inc[inc["status"] == "Resolved"].sort_values("lost_point", ascending=False)


def load_clean(data_dir: Path | str | None = None) -> CleanData:
    return clean_all(load_raw(data_dir))


def run_engine(clean: CleanData, report_date: str | pd.Timestamp | None = config.DEFAULT_REPORT_DATE) -> EngineResult:
    feat = build_features(clean, report_date)
    av_flags = availability_analysis.availability_flags(feat)
    rk_flags = search_analysis.rank_flags(feat)
    sl_flags = sales_analysis.sales_flags(feat)
    signals = {
        "PO_INACTIVITY": po_analysis.po_inactivity_episodes(feat),
        "SUPPLY_SHORTFALL": supply_analysis.supply_episodes(feat),
        "AVAILABILITY_DROP": availability_analysis.availability_episodes(feat, av_flags),
        "SEARCH_RANK_DROP": search_analysis.rank_episodes(feat, rk_flags),
        "SALES_DECLINE": sales_analysis.sales_episodes(feat, sl_flags),
    }
    incidents, segments = build_incidents(feat, signals)
    kpis = compute_kpis(feat, incidents)
    return EngineResult(features=feat, signals=signals, incidents=incidents, segments=segments, kpis=kpis,
                        flags={"availability": av_flags, "rank": rk_flags, "sales": sl_flags})


def compute_kpis(feat: Features, incidents: pd.DataFrame) -> dict:
    sw = feat.seg_week
    lw, pw = feat.latest_week, feat.latest_week - pd.Timedelta(days=7)
    cur, prev = sw[sw["week"] == lw], sw[sw["week"] == pw]

    def tot(df, c):
        return float(df[c].sum())

    def wavg(df, c):
        return float(np.average(df[c], weights=df["store_days"])) if len(df) and df["store_days"].sum() else np.nan

    po = feat.po_week
    def fill(week):
        x = po[(po["week"] == week) & po["has_po"] & po["delivered"].notna()]
        return float(safe_div(x["delivered"].sum(), x["ordered"].sum())) if len(x) else np.nan

    gmv, gmv_p = tot(cur, "sales"), tot(prev, "sales")
    sd, sd_p = tot(cur, "store_days"), tot(prev, "store_days")
    k = {
        "report_date": feat.report_date, "latest_week": lw, "prev_week": pw,
        "gmv": gmv, "gmv_prev": gmv_p, "gmv_wow": float(safe_div(gmv - gmv_p, gmv_p)),
        "lfl_wow": float(safe_div(gmv / sd - gmv_p / sd_p, gmv_p / sd_p)) if sd and sd_p else np.nan,
        "units": tot(cur, "units"), "units_prev": tot(prev, "units"),
        "avail": wavg(cur, "avail"), "avail_prev": wavg(prev, "avail"),
        "fill_rate": fill(lw), "fill_rate_prev": fill(pw),
        "stores": int(feat.sales.loc[feat.sales["week"] == lw, "dark_store_id"].nunique()),
        "stores_prev": int(feat.sales.loc[feat.sales["week"] == pw, "dark_store_id"].nunique()),
        "discount": wavg(cur, "discount_pct"), "discount_prev": wavg(prev, "discount_pct"),
    }
    active = incidents[incidents["status"] != "Resolved"] if len(incidents) else incidents
    prio = active[active["severity"].isin(["Critical", "High", "Medium"])] if len(active) else active
    k.update({
        "n_critical": int((prio["severity"] == "Critical").sum()) if len(prio) else 0,
        "n_high": int((prio["severity"] == "High").sum()) if len(prio) else 0,
        "n_medium": int((prio["severity"] == "Medium").sum()) if len(prio) else 0,
        "n_active": int(len(active)),
        "risk_point": float(active["risk_point"].sum()) if len(active) else 0.0,
        "risk_low": float(active["risk_low"].sum()) if len(active) else 0.0,
        "risk_high": float(active["risk_high"].sum()) if len(active) else 0.0,
        "recoverable": float(active["recoverable_next_week"].sum()) if len(active) else 0.0,
        "lost_point_all": float(incidents["lost_point"].sum()) if len(incidents) else 0.0,
        "lost_low_all": float(incidents["lost_low"].sum()) if len(incidents) else 0.0,
        "lost_high_all": float(incidents["lost_high"].sum()) if len(incidents) else 0.0,
    })
    k["context_note"] = _context_note(k)
    return k


def _context_note(k: dict) -> str:
    """Explain network-level movement before anyone chases a phantom decline."""
    notes = []
    if np.isfinite(k["discount"]) and np.isfinite(k["discount_prev"]) and abs(k["discount"] - k["discount_prev"]) >= 3:
        direction = "ended" if k["discount"] < k["discount_prev"] else "started"
        notes.append(f"Deeper platform discounts {direction} this week (avg {k['discount_prev']:.0f}% → "
                     f"{k['discount']:.0f}% off MRP), which moves sales across every SKU.")
    if k["stores"] != k["stores_prev"]:
        notes.append(f"{k['stores'] - k['stores_prev']:+d} dark stores vs last week; like-for-like (per store-day) "
                     f"change is {k['lfl_wow'] * 100:+.1f}%.")
    if notes:
        notes.append("Alerts below are measured against each SKU's platform peers, so these network-wide "
                     "shifts do not trigger them.")
    return " ".join(notes)


def run_backtest(clean: CleanData, end_report_date: str | pd.Timestamp = config.DEFAULT_REPORT_DATE,
                 first_report_date: str = "2026-09-21") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay the engine every Monday. Returns (weekly_log, incident_summary).

    weekly_log: one row per (report Monday, incident on that Monday's priority list).
    incident_summary: per incident, the first Monday it appeared on the list, its onset
    week, and how many weeks after the first possible Monday it was flagged (0 = first).
    """
    mondays = pd.date_range(first_report_date, end_report_date, freq="7D")
    rows = []
    for m in mondays:
        res = run_engine(clean, m)
        for r in res.incidents.itertuples():
            rows.append({"report_date": m, "incident_id": r.incident_id, "title": r.title, "type": r.type,
                         "team": r.team, "platform": r.platform, "city": r.city, "severity": r.severity,
                         "score": r.score, "status": r.status, "start_week": r.start_week,
                         "on_priority_list": r.incident_id in set(res.priority["incident_id"]),
                         "risk_point": r.risk_point, "lost_point": r.lost_point, "n_skus": r.n_skus})
    log = pd.DataFrame(rows)
    if log.empty:
        return log, log
    on = log[log["on_priority_list"]]
    summ = (on.sort_values("report_date").groupby("incident_id")
            .agg(first_flagged=("report_date", "min"), last_flagged=("report_date", "max"),
                 weeks_on_list=("report_date", "nunique"), first_severity=("severity", "first"),
                 peak_score=("score", "max"), start_week=("start_week", "first"), type=("type", "first"),
                 team=("team", "first"), platform=("platform", "first"), city=("city", "first"),
                 title=("title", "last"), max_skus=("n_skus", "max"))
            .reset_index())
    sev_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    peak = on.assign(sr=on["severity"].map(sev_rank)).sort_values("sr").groupby("incident_id")["severity"].first()
    summ["peak_severity"] = summ["incident_id"].map(peak)
    first_possible = summ["start_week"] + pd.Timedelta(days=7)
    summ["first_possible"] = first_possible
    summ["detection_lag_weeks"] = ((summ["first_flagged"] - first_possible).dt.days // 7).clip(lower=0)
    return log, summ.sort_values("first_flagged").reset_index(drop=True)
