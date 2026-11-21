"""Platform KAM view: ordering, replenishment and listing health."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from components import charts
from components.alerts import incident_card, incident_detail
from components.theme import page_header, section
from src.formatting import d
from src.po_analysis import po_compliance_matrix
from views import state
from views.shared import perf_cfg, perf_table
from views.state import wh_label


def render() -> None:
    app = state.get()
    RES = app.res
    FEAT = app.res.features
    page_header("Platform intelligence", "Ordering, replenishment and listing health",
                "What each platform's warehouses are (and are not) ordering, and where the KAMs need to escalate.")
    inc = RES.incidents
    kam = inc[(inc["team"] == "Platform KAM") & (inc["status"] != "Resolved")] if len(inc) else inc
    section("KAM escalations")
    if len(kam):
        for _, r in kam.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"kam_{r['incident_id']}")
    else:
        st.success("No ordering or replenishment escalations this week.")
    rk = inc[(inc["type"] == "SEARCH_RANK_DROP") & (inc["status"] != "Resolved")] if len(inc) else inc
    for _, r in rk.iterrows():
        st.markdown(f"**Listing check for KAM:** {html.escape(r['title'])} – ask {r['platform']} whether a ranking or "
                    f"listing change happened around {d(r['onset_date'], '%d %b')}.")

    section("Platform scorecard (last week)")
    t = perf_table("platform")
    po = FEAT.po_week[FEAT.po_week["week"] == FEAT.latest_week]
    missed = po.groupby("platform")["has_po"].apply(lambda x: int((~x).sum()))
    t["Missed POs"] = t["Platform"].map(missed).fillna(0).astype(int)
    st.dataframe(t, hide_index=True, width="stretch", column_config=perf_cfg("platform"))

    section("PO compliance: share of SKUs each warehouse ordered, by week")
    m = po_compliance_matrix(FEAT)
    m.index = [wh_label(w) for w in m.index]
    st.plotly_chart(charts.heatmap(m, "", 0.8, 1.0, ".0%"), width="stretch")

    section("Missed purchase orders (all weeks)")
    gaps = FEAT.po_week[~FEAT.po_week["has_po"]][["week", "platform", "city", "warehouse_id", "sku_code", "product_name"]]
    if len(gaps):
        sig = RES.signals["PO_INACTIVITY"]
        flagged = set()
        for r in sig.itertuples():
            for w in pd.date_range(r.start_week, r.end_week, freq="7D"):
                flagged.add((r.warehouse_id, r.sku_code, w))
        gaps["Flagged as inactivity"] = [(w, s, k) in flagged for w, s, k in zip(gaps["warehouse_id"], gaps["sku_code"], gaps["week"])]
        gaps = gaps.rename(columns={"platform": "Platform", "city": "City", "warehouse_id": "Warehouse", "sku_code": "SKU",
                                    "product_name": "Product"})
        st.dataframe(gaps.sort_values("week", ascending=False), hide_index=True, width="stretch",
                     column_config={"week": st.column_config.DateColumn("PO week", format="DD MMM YYYY")})
    else:
        st.success("Every warehouse ordered every SKU every week.")
