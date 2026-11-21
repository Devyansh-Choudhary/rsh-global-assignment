"""Explorer: drill-down by platform, city, product, warehouse and store."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import config
from components import charts
from components.metrics import delta_html, kpi, kpi_row
from components.theme import page_header, section
from src.feature_engineering import safe_div
from src.formatting import inr, num
from views import state
from views.state import wh_label


def render() -> None:
    app = state.get()
    FEAT = app.res.features
    CLEAN = app.clean
    page_header("Explorer", "Product & store drill-down", "Weekly history for any slice of the network.")
    s = FEAT.seg_week
    c = st.columns(4)
    pf = c[0].multiselect("Platform", sorted(s["platform"].unique()), key="ex_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(s["city"].unique()), key="ex_c", placeholder="All")
    prod = c[2].multiselect("Product", FEAT.products["product_name"].tolist(), key="ex_pr", placeholder="All")
    whs = sorted(s["warehouse_id"].unique())
    wf = c[3].multiselect("Warehouse", whs, key="ex_w", format_func=wh_label, placeholder="All")
    sel = s.copy()
    if pf:
        sel = sel[sel["platform"].isin(pf)]
    if cf:
        sel = sel[sel["city"].isin(cf)]
    if prod:
        sel = sel[sel["product_name"].isin(prod)]
    if wf:
        sel = sel[sel["warehouse_id"].isin(wf)]
    if sel.empty:
        st.info("No data for this selection.")
        return
    w = sel.groupby("week").apply(lambda x: pd.Series({
        "sales": x["sales"].sum(), "units": x["units"].sum(), "store_days": x["store_days"].sum(),
        "avail": np.average(x["avail"], weights=x["store_days"]), "rank": np.average(x["rank"], weights=x["store_days"])}),
        include_groups=False).reset_index()
    w["sales_psd"] = w["sales"] / w["store_days"]
    lw = w.iloc[-1]
    prev = w.iloc[-2] if len(w) > 1 else lw
    kpi_row([kpi("Sales last week", inr(lw["sales"]), f"{delta_html(safe_div(lw['sales'] - prev['sales'], prev['sales']).item())} WoW"),
             kpi("Units", num(lw["units"]), ""), kpi("Availability", f"{lw['avail']:.1f}%", ""),
             kpi("Avg search rank", f"{lw['rank']:.1f}", "1 = top"),
             kpi("₹ per store-day", f"₹{lw['sales_psd']:.0f}", f"{delta_html(safe_div(lw['sales_psd'] - prev['sales_psd'], prev['sales_psd']).item())} LFL")])
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.segment_trend(w, "sales", "Weekly sales (₹)", fmt=",.0f", height=240), width="stretch")
    c2.plotly_chart(charts.segment_trend(w, "sales_psd", "Sales per store-day (₹, like-for-like)", fmt=",.0f", height=240),
                    width="stretch")
    c3, c4 = st.columns(2)
    c3.plotly_chart(charts.segment_trend(w, "avail", "Availability (%)", fmt=".1f", height=240), width="stretch")
    c4.plotly_chart(charts.segment_trend(w, "rank", "Search rank (1 = top)", invert=True, fmt=".1f", height=240),
                    width="stretch")

    section("Dark stores in this selection (last week)")
    sw = FEAT.store_week
    keys = sel[["warehouse_id", "sku_code"]].drop_duplicates()
    st_sel = sw.merge(keys, on=["warehouse_id", "sku_code"])
    last = st_sel[st_sel["week"] == FEAT.latest_week].groupby(["dark_store_id", "platform", "city", "warehouse_id"]).agg(
        sales=("sales", "sum"), units=("units", "sum"), avail=("avail", "mean"), rank=("rank", "mean"),
        skus_oos=("avail", lambda x: int((x < config.STORE_OOS_THRESHOLD).sum()))).reset_index()
    opened = CLEAN.stores.set_index("dark_store_id")["opened_date"]
    last["opened"] = last["dark_store_id"].map(opened)
    st.dataframe(last.sort_values("avail"), hide_index=True, width="stretch", column_config={
        "sales": st.column_config.NumberColumn("Sales ₹", format="₹%d"), "avail": st.column_config.NumberColumn("Avail %", format="%.1f"),
        "rank": st.column_config.NumberColumn("Avg rank", format="%.1f"),
        "skus_oos": st.column_config.NumberColumn("SKUs < 50% avail"),
        "opened": st.column_config.DateColumn("Opened", format="DD MMM YYYY")})

    store = st.selectbox("Daily view for a dark store", ["–"] + last["dark_store_id"].tolist(), key="ex_store")
    if store != "–":
        dd = FEAT.sales[FEAT.sales["dark_store_id"] == store]
        if prod:
            dd = dd[dd["product_name"].isin(prod)]
        piv = dd.pivot_table(index="product_name", columns="date", values="availability_pct")
        piv.columns = [c.strftime("%d %b") for c in piv.columns]
        st.plotly_chart(charts.heatmap(piv.iloc[:, -28:] / 100, f"{store}: daily availability, last 28 days", 0, 1, ".0%"),
                        width="stretch")
