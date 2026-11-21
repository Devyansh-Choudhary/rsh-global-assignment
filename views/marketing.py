"""Marketing intelligence: search visibility, separated from stock issues."""
from __future__ import annotations

import html

import numpy as np
import streamlit as st

import config
from components import charts
from components.alerts import incident_card, incident_detail
from components.theme import page_header, section
from src.feature_engineering import safe_div
from src.report_generator import team_briefs
from views import state


def render() -> None:
    app = state.get()
    RES = app.res
    FEAT = app.res.features
    page_header("Marketing intelligence", "Where are we hard to find?",
                "Search-rank and sales signals, separated from stock problems. Ads cannot fix an empty shelf.")
    st.markdown("<div class='jc-callout'><b>Marketing vs supply.</b> A rank or sales drop is routed to Marketing only "
                "when availability stayed normal. If stock also fell, the rank drop is treated as a symptom of the stockout "
                "and the issue goes to Supply or the platform KAM instead.</div>", unsafe_allow_html=True)
    mk = team_briefs(RES)["Marketing"]
    inc = RES.incidents
    mk_inc = inc[(inc["team"] == "Marketing") & (inc["status"] != "Resolved")] if len(inc) else inc
    section("Recommended interventions")
    if len(mk_inc):
        for _, r in mk_inc.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"mk_{r['incident_id']}")
    else:
        st.success("No search-visibility issues this week.")
    if mk["pause_ads"]:
        st.markdown("**⏸ Pause paid ads until back in stock:** " + html.escape("; ".join(mk["pause_ads"])))

    section("Search visibility watch: latest week vs baseline")
    rf = RES.flags["rank"]
    lw = rf[rf["week"] == FEAT.latest_week].copy()
    lw["rank_change"] = lw["rank"] - lw["rank_base"]
    sf = RES.flags["sales"][RES.flags["sales"]["week"] == FEAT.latest_week][["warehouse_id", "sku_code", "expected_units", "sales_z"]]
    lw = lw.merge(sf, on=["warehouse_id", "sku_code"], how="left")
    lw["sales_vs_expected"] = safe_div(lw["units"] - lw["expected_units"], lw["expected_units"]) * 100
    lw["Signal"] = np.where(lw["rank_flag"] & (lw["avail"] >= config.AVAIL_FLOOR), "Visibility loss",
                            np.where(lw["rank_flag"], "Rank + stockout", ""))
    c = st.columns(3)
    pf = c[0].multiselect("Platform", sorted(lw["platform"].unique()), key="mk_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(lw["city"].unique()), key="mk_c", placeholder="All")
    catf = c[2].multiselect("Category", sorted(lw["category"].unique()), key="mk_cat", placeholder="All")
    for col, sel in (("platform", pf), ("city", cf), ("category", catf)):
        if sel:
            lw = lw[lw[col].isin(sel)]
    show = lw.sort_values("rank_change", ascending=False)[
        ["Signal", "platform", "city", "product_name", "category", "rank_base", "rank", "rank_change", "avail", "units",
         "expected_units", "sales_vs_expected", "sales"]]
    show = show.rename(columns={"platform": "Platform", "city": "City", "product_name": "Product", "category": "Category",
                                "units": "Units"})
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "Signal": st.column_config.TextColumn(width="medium"),
        "rank_base": st.column_config.NumberColumn("Baseline rank", format="%.1f"),
        "rank": st.column_config.NumberColumn("Rank last wk", format="%.1f"),
        "rank_change": st.column_config.NumberColumn("Δ positions", format="%+.1f", help="Positive = worse"),
        "avail": st.column_config.NumberColumn("Avail %", format="%.0f"),
        "expected_units": st.column_config.NumberColumn("Expected units", format="%.0f"),
        "sales_vs_expected": st.column_config.NumberColumn("Units vs expected", format="%+.0f%%"),
        "sales": st.column_config.NumberColumn("Sales ₹", format="₹%d")})
    st.caption("Expected units = the SKU's own recent share of platform sales × this week's platform volume. "
               "This removes festive promotions and new-store growth from the comparison.")

    section("Rank history by platform-city (selected product)")
    prod = st.selectbox("Product", FEAT.products["product_name"].tolist(), key="mk_prod",
                        index=int(np.argmax(FEAT.products["sku_code"].eq("JY-FW-NEEM-100"))) if
                        FEAT.products["sku_code"].eq("JY-FW-NEEM-100").any() else 0)
    sku = FEAT.products.set_index("product_name").loc[prod, "sku_code"]
    m = FEAT.seg_week[FEAT.seg_week["sku_code"] == sku].assign(seg=lambda x: x["platform"] + " " + x["city"]) \
        .pivot_table(index="seg", columns="week", values="rank")
    st.plotly_chart(charts.heatmap(m, "Average search rank (1 = top; red = worse)", 1, 14, ".1f", reverse=True),
                    width="stretch")
