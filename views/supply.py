"""Supply intelligence: fill rates, shortfalls, ship-now quantities."""
from __future__ import annotations

import html

import numpy as np
import pandas as pd
import streamlit as st

import config
from components import charts
from components.alerts import incident_detail
from components.metrics import kpi, kpi_row
from components.theme import page_header, section, severity_pill
from src.formatting import num, pct
from src.report_generator import team_briefs
from src.supply_analysis import fill_rate_matrix, supply_scorecard
from views import state
from views.state import wh_label


def render() -> None:
    app = state.get()
    RES = app.res
    K = app.res.kpis
    FEAT = app.res.features
    page_header("Supply intelligence", "Did we deliver what the platforms ordered?",
                "Fill rate = delivered ÷ ordered per PO line (each line carries its own delivery, 2–4 days after the Monday PO).")
    po = FEAT.po_week
    lw = po[(po["week"] == FEAT.latest_week) & po["has_po"] & po["delivered"].notna()]
    short = lw[lw["fill_rate"] < config.FILL_RATE_THRESHOLD]
    kpi_row([
        kpi("Fill rate, latest PO", pct(K["fill_rate"], 1), f"PO of {FEAT.latest_week:%d %b}"),
        kpi("Units short, latest PO", num((lw["ordered"] - lw["delivered"]).sum()), f"of {num(lw['ordered'].sum())} ordered"),
        kpi("PO lines below 80%", str(len(short)), f"{short['warehouse_id'].nunique()} warehouse(s)", alert=len(short) > 0),
        kpi("Missed POs this week", str(int((~po[po["week"] == FEAT.latest_week]["has_po"]).sum())), "warehouse × SKU"),
    ])
    sup_inc = RES.incidents[(RES.incidents["type"] == "SUPPLY_SHORTFALL") & (RES.incidents["status"] != "Resolved")] \
        if len(RES.incidents) else RES.incidents
    section("Supply actions")
    briefs = team_briefs(RES)["Supply"]
    if briefs:
        rows = []
        for it in briefs:
            for ln in it["lines"]:
                rows.append({"Severity": it["severity"], "Warehouse": it["warehouse_id"], "City": f"{it['platform']} {it['city']}",
                             "SKU": ln["sku_code"], "Product": ln["product"], "Ordered": ln["ordered"], "Delivered": ln["delivered"],
                             "Shortfall": ln["shortfall"], "Fill %": ln["fill_rate"] * 100 if pd.notna(ln["fill_rate"]) else np.nan,
                             "Ship now (units)": ln["latest_shortfall"], "Avail. last 3d %": ln["avail_now"],
                             "Type": it["kind"], "Due": it["deadline"]})
        tbl = pd.DataFrame(rows)
        for col in ["Ordered", "Delivered", "Shortfall", "Fill %", "Ship now (units)", "Avail. last 3d %"]:
            tbl[col] = tbl[col].map(lambda v: "–" if pd.isna(v) else f"{v:,.0f}")
        st.dataframe(tbl, hide_index=True, width="stretch")
        for it in briefs:
            st.markdown(f"{severity_pill(it['severity'])} **{it['warehouse_id']}** – {html.escape(it['action'])}",
                        unsafe_allow_html=True)
    else:
        st.success("No supply actions this week.")
    for _, r in sup_inc.iterrows():
        incident_detail(r, RES, f"sup_{r['incident_id']}")

    section("Fill rate by warehouse and PO week")
    m = fill_rate_matrix(FEAT)
    m.index = [wh_label(w) for w in m.index]
    st.plotly_chart(charts.heatmap(m, "", 0.4, 1.0, ".0%"), width="stretch")
    st.caption("A whole warehouse row can look healthy while individual SKUs are badly short - see the SKU table below.")

    section("Warehouse × SKU scorecard (last 4 PO weeks)")
    sc = supply_scorecard(FEAT)
    c = st.columns(3)
    pf = c[0].multiselect("Platform", sorted(sc["platform"].unique()), key="sup_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(sc["city"].unique()), key="sup_c", placeholder="All")
    only = c[2].toggle("Only fill rate < 90%", value=False)
    if pf:
        sc = sc[sc["platform"].isin(pf)]
    if cf:
        sc = sc[sc["city"].isin(cf)]
    if only:
        sc = sc[sc["fill_rate"] < 0.9]
    show = sc.sort_values("fill_rate")[["platform", "city", "warehouse_id", "sku_code", "product_name", "ordered", "delivered",
                                         "shortfall_units", "fill_rate", "last_ordered", "last_delivered", "last_fill_rate", "po_weeks"]]
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "fill_rate": st.column_config.ProgressColumn("4-wk fill", format="percent", min_value=0, max_value=1),
        "last_fill_rate": st.column_config.NumberColumn("Latest fill", format="percent"),
        "po_weeks": st.column_config.NumberColumn("POs (of 4)")})

    section("Drill into a warehouse")
    c = st.columns(2)
    wh = c[0].selectbox("Warehouse", sorted(po["warehouse_id"].unique()), format_func=wh_label, key="sup_wh")
    sku = c[1].selectbox("SKU", sorted(po["sku_code"].unique()), key="sup_sku",
                         format_func=lambda s: f"{s} · {FEAT.products.set_index('sku_code').loc[s, 'product_name']}")
    p = po[(po["warehouse_id"] == wh) & (po["sku_code"] == sku)]
    s = FEAT.seg_week[(FEAT.seg_week["warehouse_id"] == wh) & (FEAT.seg_week["sku_code"] == sku)]
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.ordered_vs_delivered(p, "Ordered vs delivered (units)"), width="stretch")
    c2.plotly_chart(charts.segment_trend(s, "avail", "Dark-store availability (%)", height=260, fmt=".1f"), width="stretch")
