"""Monday brief: the 30-second summary."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from components import charts
from components.alerts import priority_table
from components.metrics import delta_html, kpi, kpi_row
from components.theme import page_header, section
from src.feature_engineering import safe_div
from src.formatting import inr, inr_range, num, pct
from views import state
from views.shared import perf_cfg, perf_table


def render() -> None:
    app = state.get()
    RES = app.res
    K = app.res.kpis
    FEAT = app.res.features
    prio = RES.priority
    n = len(prio)
    page_header(f"Monday brief · {RES.report_date:%A %d %B %Y}",
                f"{n} issue{'s' if n != 1 else ''} need{'s' if n == 1 else ''} action this week" if n else "No issues need action this week",
                f"Week of {K['latest_week']:%d %b} – {K['latest_week'] + pd.Timedelta(days=6):%d %b} · "
                f"{K['stores']} dark stores · Blinkit, Zepto, Instamart · 6 cities")
    if n:
        top = prio.iloc[0]
        teams = ", ".join(sorted(prio["team"].unique()))
        st.markdown(f"""<div class='jc-hero'><div class='jc-hero-big'>An estimated <b>{inr_range(K['risk_low'], K['risk_high'])}</b>
of sales is at risk <b>every week</b> from {n} open issue{'s' if n > 1 else ''}; about <b>{inr(K['recoverable'])}</b> of it is
recoverable next week if {teams} act today. Biggest: <b>{html.escape(top['title'])}</b>.</div>
<div class='jc-note'>{html.escape(K['context_note'])}</div></div>""", unsafe_allow_html=True)

    kpi_row([
        kpi("GMV last week", inr(K["gmv"]), f"{delta_html(K['gmv_wow'])} WoW · like-for-like {delta_html(K['lfl_wow'])}"),
        kpi("Units sold", num(K["units"]), f"{delta_html(safe_div(K['units'] - K['units_prev'], K['units_prev']).item())} WoW"),
        kpi("Avg availability", f"{K['avail']:.1f}%", f"{delta_html((K['avail'] - K['avail_prev']) / 100, fmt='{:+.1f} pts')} WoW"),
        kpi("PO fill rate", pct(K["fill_rate"], 1), f"{delta_html(K['fill_rate'] - K['fill_rate_prev'], fmt='{:+.1f} pts')} vs prior PO"),
        kpi("Critical / high", f"{K['n_critical']} / {K['n_high']}", f"{K['n_medium']} medium · {len(RES.watchlist)} on watch",
            alert=K["n_critical"] > 0),
        kpi("Weekly ₹ at risk (est.)", inr(K["risk_point"]), f"range {inr_range(K['risk_low'], K['risk_high'])}", alert=K["risk_point"] > 0),
    ])

    section("This week's priority list")
    if n:
        priority_table(prio)
        st.caption("Open the Command Center for evidence, trends and the reasoning behind each rank.")
    else:
        st.success("Nothing crosses the action threshold this week. See the watchlist in the Command Center.")

    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.weekly_gmv_by_platform(FEAT.seg_week), width="stretch")
    c2.plotly_chart(charts.lfl_index_by_platform(FEAT.seg_week), width="stretch")

    open_inc = RES.incidents[RES.incidents["status"] != "Resolved"] if len(RES.incidents) else RES.incidents
    if len(open_inc):
        st.plotly_chart(charts.risk_bars(open_inc), width="stretch")

    section("Platform and city performance (last week)")
    c1, c2 = st.columns(2)
    for col, by in ((c1, "platform"), (c2, "city")):
        col.dataframe(perf_table(by), hide_index=True, width="stretch", column_config=perf_cfg(by))
