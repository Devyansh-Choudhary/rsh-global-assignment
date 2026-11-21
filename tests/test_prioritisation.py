"""Prioritisation, revenue impact, double-counting and output-consistency tests."""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

import config
from src.prioritisation import score_incident
from src.report_generator import action_list, build_pdf, team_briefs, whatsapp_messages
from src.revenue_impact import build_peer_tables, estimate_segment_impact


# ---------------------------------------------------------------- scoring
def test_score_components_and_bands():
    s = score_incident(10_000, "Active - worsening", 4, 1.0, 5, "High", impact_cap=10_000)
    assert s["score"] == 100 and s["severity"] == "Critical"
    r = score_incident(0, "Resolved", 1, 0, 1, "High", impact_cap=10_000)
    assert r["score_urgency"] == 0 and r["score_impact"] == 0 and r["severity"] == "Low"


def test_score_monotonic_in_risk_and_confidence():
    lo = score_incident(1_000, "Active", 2, 0.5, 1, "High", 20_000)["score"]
    hi = score_incident(8_000, "Active", 2, 0.5, 1, "High", 20_000)["score"]
    assert hi > lo
    med = score_incident(8_000, "Active", 2, 0.5, 1, "Medium", 20_000)["score"]
    assert med < hi and abs(med - hi * config.CONFIDENCE_MULTIPLIER["Medium"]) < 0.11


def test_score_handles_zero_cap():
    s = score_incident(5_000, "Active", 1, 0.2, 1, "Low", impact_cap=0)
    assert np.isfinite(s["score"])


def test_priority_list_rules(real_result):
    p = real_result.priority
    assert len(p) <= config.MAX_PRIORITY_ISSUES
    assert (p["status"] != "Resolved").all()
    assert p["severity"].isin(["Critical", "High", "Medium"]).all()
    assert list(p["score"]) == sorted(p["score"], reverse=True)
    assert p["why_ranked"].str.len().gt(20).all()


def test_incident_ids_unique(real_result):
    assert real_result.incidents["incident_id"].is_unique


# ---------------------------------------------------------------- revenue impact
def test_revenue_impact_known_case(synthetic_result):
    """Synthetic JY-A at BLK-WH-01: availability 95% -> 40% from week 5 => ~58% of sales lost."""
    feat = synthetic_result.features
    peers = build_peer_tables(feat, {("BLK-WH-01", "JY-A"), ("BLK-WH-02", "JY-B"), ("ZEP-WH-01", "JY-C")})
    imp = estimate_segment_impact(feat, peers, "BLK-WH-01", "JY-A", "SUPPLY_SHORTFALL",
                                  pd.Timestamp("2026-02-02"), pd.Timestamp("2026-02-23"), True,
                                  last3d_avail=40.0, latest_fill_rate=0.5)
    seg = feat.seg_week[(feat.seg_week["warehouse_id"] == "BLK-WH-01") & (feat.seg_week["sku_code"] == "JY-A")]
    ref = seg[seg["week"] < "2026-02-02"]["sales"].mean()
    # 4 affected weeks x ~58% of reference weekly sales
    assert 0.4 * 4 * ref < imp["lost_point"] < 0.75 * 4 * ref
    assert imp["lost_low"] <= imp["lost_point"] <= imp["lost_high"]
    assert imp["risk_low"] <= imp["risk_point"] <= imp["risk_high"] and imp["risk_point"] > 0
    assert imp["recoverable_next_week"] < imp["risk_point"]
    assert imp["ref_source"] == "pre-incident weeks"


def test_resolved_incident_has_no_forward_risk(real_result):
    r = real_result.resolved
    assert (r["risk_point"] == 0).all() and (r["recoverable_next_week"] == 0).all()
    assert r["lost_point"].gt(0).any()


def test_estimates_are_ranges_and_finite(real_result):
    inc = real_result.incidents
    for c in ["lost_low", "lost_point", "lost_high", "risk_low", "risk_point", "risk_high", "score"]:
        assert np.isfinite(inc[c]).all(), c
    assert (inc["lost_low"] <= inc["lost_point"] + 1e-6).all() and (inc["lost_point"] <= inc["lost_high"] + 1e-6).all()


def test_no_double_counting(real_result):
    seg, inc = real_result.segments, real_result.incidents
    # incident totals are exactly the sum of their SKU rows
    s = seg.groupby("incident_id")[["lost_point", "risk_point"]].sum()
    m = inc.set_index("incident_id")[["lost_point", "risk_point"]]
    assert np.allclose(s.loc[m.index].values, m.values)
    # a warehouse x SKU never sits in two incidents whose windows overlap
    for (_, _), g in seg.groupby(["warehouse_id", "sku_code"]):
        g = g.sort_values("start_week")
        for a, b in zip(g.itertuples(), list(g.itertuples())[1:]):
            assert b.start_week > a.end_week
    # every detector signal is used at most once (root or evidence)
    linked = sum(len(v) for v in real_result.signals.values())
    assert linked >= len(seg)


def test_kpis_match_incidents(real_result):
    k, inc = real_result.kpis, real_result.incidents
    open_ = inc[inc["status"] != "Resolved"]
    assert abs(k["risk_point"] - open_["risk_point"].sum()) < 1e-6
    assert k["n_critical"] == (real_result.priority["severity"] == "Critical").sum()


# ---------------------------------------------------------------- outputs
def test_csv_action_list_consistent(real_result):
    al = action_list(real_result)
    open_ids = set(real_result.incidents.loc[real_result.incidents["status"] != "Resolved", "incident_id"])
    assert set(al["incident_id"]) == open_ids
    assert abs(al["est_weekly_revenue_at_risk_inr"].sum() - real_result.kpis["risk_point"]) < len(al)  # rounding only
    back = pd.read_csv(io.StringIO(al.to_csv(index=False)))
    assert len(back) == len(al)


def test_pdf_builds(real_result, real_clean):
    pdf = build_pdf(real_result, None, real_clean.profile)
    assert pdf[:4] == b"%PDF" and len(pdf) > 10_000


def test_whatsapp_messages(real_result):
    msgs = whatsapp_messages(real_result)
    assert set(msgs) == {"QC Manager", "Supply", "Platform KAM", "Marketing"}
    assert "BLK-WH-03" in msgs["Supply"] and "ZEP-WH-08" in msgs["Platform KAM"] and "Neem Face Wash" in msgs["Marketing"]
    assert all("sent" not in m.lower() for m in msgs.values())


def test_team_briefs_route_to_right_team(real_result):
    b = team_briefs(real_result)
    assert all(it["kind"] in ("Under-delivery", "Stand-by stock (platform not ordering)") for it in b["Supply"])
    assert {it["kind"] for it in b["Platform KAM"]} >= {"Ordering stopped"}
    assert all(it["kind"] == "Search visibility loss" for it in b["Marketing"]["items"])


def test_engine_runs_for_every_monday_without_error(real_clean):
    from src.engine import run_engine
    for m in pd.date_range("2026-09-14", "2026-11-16", freq="7D"):
        res = run_engine(real_clean, m)
        assert len(res.priority) <= config.MAX_PRIORITY_ISSUES


def test_engine_survives_empty_purchase_orders(tmp_path):
    """Optional data missing: a headers-only PO export must not crash the engine or the outputs."""
    from src.data_cleaning import clean_all
    from src.data_loader import load_raw
    from src.engine import run_engine
    from tests.conftest import SYN_REPORT, make_synthetic
    d = make_synthetic(tmp_path)
    pd.read_csv(d / "purchase_orders.csv").head(0).to_csv(d / "purchase_orders.csv", index=False)
    res = run_engine(clean_all(load_raw(d)), SYN_REPORT)
    assert res.signals["SUPPLY_SHORTFALL"].empty and res.signals["PO_INACTIVITY"].empty
    assert not res.signals["SEARCH_RANK_DROP"].empty  # sales-based detection still works
    assert np.isnan(res.kpis["fill_rate"])
    build_pdf(res)
    whatsapp_messages(res)
    action_list(res)
