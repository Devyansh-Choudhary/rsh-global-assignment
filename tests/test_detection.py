"""Detector tests on synthetic data (known injected incidents) and on the real exports."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.engine import run_engine
from src.feature_engineering import build_features, runs, safe_div, sequential_baseline
from tests.conftest import SYN_REPORT, make_synthetic


# ---------------------------------------------------------------- helpers
def test_safe_div_never_inf():
    out = safe_div([1, 2, 0, 5], [0, 4, 0, np.nan])
    assert np.isnan(out[0]) and out[1] == 0.5 and np.isnan(out[2]) and np.isnan(out[3])


def test_runs_and_sequential_baseline():
    assert runs(np.array([0, 1, 1, 0, 1], dtype=bool)) == [(1, 2), (4, 4)]
    vals = np.array([90, 91, 89, 40, 42, 45, 90])
    base, flags = sequential_baseline(vals, lambda v, b: v < b - 15)
    assert flags.tolist() == [False, False, False, True, True, True, False]
    # the incident never becomes the new normal: baseline stays ~90 throughout
    assert np.all(base[3:6] == 90)


# ---------------------------------------------------------------- synthetic incidents
def test_fill_rate_calculation(synthetic_result):
    po = synthetic_result.features.po_week
    r = po[(po["warehouse_id"] == "BLK-WH-01") & (po["sku_code"] == "JY-A") & (po["week"] == "2026-02-02")].iloc[0]
    assert r["ordered"] == 100 and r["delivered"] == 50 and r["fill_rate"] == 0.5 and r["shortfall_units"] == 50


def test_supply_shortfall_detected(synthetic_result):
    sup = synthetic_result.signals["SUPPLY_SHORTFALL"]
    hit = sup[(sup["warehouse_id"] == "BLK-WH-01") & (sup["sku_code"] == "JY-A")]
    assert len(hit) == 1
    h = hit.iloc[0]
    assert h["start_week"] == pd.Timestamp("2026-02-02") and h["n_weeks"] == 4 and h["is_active"]
    assert h["shortfall_units"] == 200 and abs(h["fill_rate"] - 0.5) < 1e-9
    # nothing else is short
    assert len(sup) == 1


def test_po_inactivity_detected(synthetic_result):
    po = synthetic_result.signals["PO_INACTIVITY"]
    assert len(po) == 1
    r = po.iloc[0]
    assert (r["warehouse_id"], r["sku_code"]) == ("BLK-WH-02", "JY-B")
    assert r["missed_cycles"] == 2 and not r["is_active"] and r["resumed_week"] == pd.Timestamp("2026-02-16")


def test_warehouse_wide_pause_is_not_sku_inactivity(tmp_path):
    d = make_synthetic(tmp_path, shortfall=False, rank_drop=False, po_gap=False)
    po = pd.read_csv(d / "purchase_orders.csv")
    po = po[~((po["warehouse_id"] == "BLK-WH-02") & (po["po_date"] == "2026-02-02"))]  # whole warehouse skips a week
    po.to_csv(d / "purchase_orders.csv", index=False)
    from src.data_cleaning import clean_all
    from src.data_loader import load_raw
    res = run_engine(clean_all(load_raw(d)), SYN_REPORT)
    assert res.signals["PO_INACTIVITY"].empty


def test_active_po_gap_flagged_before_stockouts(tmp_path):
    """Early warning: a missed PO is flagged the Monday after, before availability falls."""
    d = make_synthetic(tmp_path, shortfall=False, rank_drop=False)
    from src.data_cleaning import clean_all
    from src.data_loader import load_raw
    res = run_engine(clean_all(load_raw(d)), "2026-02-09")  # only week 5 (index 4) PO missed so far
    po = res.signals["PO_INACTIVITY"]
    assert len(po) == 1 and po.iloc[0]["is_active"]
    inc = res.incidents.iloc[0]
    assert inc["type"] == "PO_INACTIVITY" and inc["incident_id"] in set(res.priority["incident_id"])


def test_search_rank_drop_detected_with_stable_availability(synthetic_result):
    rk = synthetic_result.signals["SEARCH_RANK_DROP"]
    assert len(rk) == 1
    r = rk.iloc[0]
    assert (r["warehouse_id"], r["sku_code"]) == ("ZEP-WH-01", "JY-C")
    assert r["baseline_rank"] == 3 and r["latest_rank"] == 12 and r["avail_stable"]
    assert r["sales_rel_change"] < -0.3


def test_small_rank_moves_not_flagged(tmp_path):
    d = make_synthetic(tmp_path, shortfall=False, po_gap=False, rank_drop=False)
    s = pd.read_csv(d / "daily_sales.csv")
    s.loc[s["date"] >= "2026-02-02", "search_rank"] += 2  # +2 positions everywhere: below the 3-position bar
    s.to_csv(d / "daily_sales.csv", index=False)
    from src.data_cleaning import clean_all
    from src.data_loader import load_raw
    res = run_engine(clean_all(load_raw(d)), SYN_REPORT)
    assert res.signals["SEARCH_RANK_DROP"].empty


def test_city_normalisation_flows_through(synthetic_result):
    assert set(synthetic_result.features.seg_week["city"]) == {"Bengaluru", "Mumbai"}


def test_no_false_positives_on_clean_synthetic(tmp_path):
    d = make_synthetic(tmp_path, shortfall=False, po_gap=False, rank_drop=False, seed=11)
    from src.data_cleaning import clean_all
    from src.data_loader import load_raw
    res = run_engine(clean_all(load_raw(d)), SYN_REPORT)
    assert res.priority.empty


def test_as_of_discipline_no_lookahead(real_clean):
    f = build_features(real_clean, "2026-10-12")
    assert f.sales["date"].max() == pd.Timestamp("2026-10-11")
    assert f.po_week["week"].max() == pd.Timestamp("2026-10-05")
    # A delivery dated after the report date must be pending, not counted
    f2 = build_features(real_clean, "2026-11-12")  # snaps to Mon 16 Nov
    assert f2.report_date == pd.Timestamp("2026-11-16")


def test_aggregation_preserves_totals(real_result, real_clean):
    f = real_result.features
    assert f.seg_week["sales"].sum() == real_clean.sales["sales_inr"].sum()
    assert f.seg_week["units"].sum() == real_clean.sales["units_sold"].sum()
    assert f.store_week["sales"].sum() == real_clean.sales["sales_inr"].sum()
    po = f.po_week[f.po_week["has_po"]]
    assert po["ordered"].sum() == real_clean.purchase_orders["qty_ordered"].sum()


# ---------------------------------------------------------------- the three historical incidents (real data)
def _priority(real_clean, date):
    return run_engine(real_clean, date).priority


def test_incident1_blinkit_bengaluru_under_delivery(real_clean):
    p = _priority(real_clean, "2026-09-28")
    hit = p[(p["type"] == "SUPPLY_SHORTFALL") & (p["warehouse_id"] == "BLK-WH-01")]
    assert len(hit) == 1
    r = hit.iloc[0]
    assert r["platform"] == "Blinkit" and r["city"] == "Bengaluru" and r["team"] == "Supply"
    assert set(r["sku_codes"]) == {"JY-FW-NEEM-100", "JY-FW-NEEM-50", "JY-FW-PAP-100", "JY-LT-ALM-200",
                                   "JY-LT-ALM-400", "JY-LT-COCO-200"}
    assert 0.4 < r["fill_rate"] < 0.6
    assert r["severity"] in ("Critical", "High")


def test_incident2_zepto_bengaluru_rank_drop(real_clean):
    p = _priority(real_clean, "2026-10-19")
    hit = p[(p["type"] == "SEARCH_RANK_DROP") & (p["warehouse_id"] == "ZEP-WH-07")]
    assert len(hit) == 1
    seg = run_engine(real_clean, "2026-10-19").signals["SEARCH_RANK_DROP"].iloc[0]
    assert seg["sku_code"] == "JY-FW-NEEM-100"
    assert 2 <= seg["baseline_rank"] <= 4 and seg["latest_rank"] >= 10
    assert seg["avail_stable"] and seg["onset_date"] == pd.Timestamp("2026-10-14")
    assert hit.iloc[0]["team"] == "Marketing"


def test_incident3_instamart_hyderabad_sunscreen_po_stop(real_clean):
    p = _priority(real_clean, "2026-09-21")
    hit = p[(p["type"] == "PO_INACTIVITY") & (p["warehouse_id"] == "IM-WH-16")]
    assert len(hit) == 1
    assert set(hit.iloc[0]["sku_codes"]) <= {"JY-SS-SPF50-50", "JY-SS-MATTE-50"}
    assert hit.iloc[0]["team"] == "Platform KAM"
    res = run_engine(real_clean, "2026-11-16")
    inc = res.incidents[res.incidents["incident_id"].str.startswith("PO-IM-WH-16")].iloc[0]
    assert set(inc["sku_codes"]) == {"JY-SS-SPF50-50", "JY-SS-MATTE-50"} and inc["status"] == "Resolved"
    assert "AVAILABILITY_DROP" in inc["linked_signals"]  # the stockouts that followed are linked, not separate


def test_live_issues_on_16_nov(real_result):
    p = real_result.priority
    ids = set(p["incident_id"])
    assert {"PO-ZEP-WH-08-1102", "RNK-ZEP-WH-07-1012", "SUP-BLK-WH-03-1109"} == ids
    resolved = set(real_result.resolved["incident_id"])
    assert "SUP-BLK-WH-01-0921" in resolved and "PO-IM-WH-16-0914" in resolved


def test_festive_discount_end_does_not_flood_alerts(real_result):
    """Network GMV fell ~11% WoW (promo ended) - this must not create a wave of sales alerts."""
    assert real_result.kpis["gmv_wow"] < -0.05
    sales = real_result.signals["SALES_DECLINE"]
    assert (sales["end_week"] == real_result.features.latest_week).sum() <= 5
    assert len(real_result.priority) <= 10
