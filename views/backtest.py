"""Back-test: replay every past Monday with no look-ahead."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
import streamlit as st

import config
from components import charts
from components.metrics import kpi, kpi_row
from components.theme import page_header
from views import state
from views.state import get_backtest


def render() -> None:
    app = state.get()
    DATA_DIR = app.data_dir
    STAMP = app.stamp
    REPORT = app.report
    page_header("Back-test", "Would we have caught it in week one?",
                "The engine is replayed for every past Monday using only the data available that morning "
                "(sales up to Sunday, POs raised so far, deliveries already made). Nothing is hard-coded.")
    log, summ = get_backtest(DATA_DIR, STAMP, REPORT)
    if summ.empty:
        st.info("No incidents reached the priority list in the replay window.")
        return
    kpi_row([kpi("Incidents caught", str(len(summ)), "reached the Monday priority list"),
             kpi("Flagged on first possible Monday", f"{int((summ['detection_lag_weeks'] == 0).sum())} / {len(summ)}",
                 "the Monday after the problem's first week"),
             kpi("Max items on any Monday", str(int(log[log['on_priority_list']].groupby('report_date').size().max())),
                 "no alert fatigue"),
             kpi("Mondays replayed", str(log["report_date"].nunique()), f"{log['report_date'].min():%d %b} – {log['report_date'].max():%d %b}")])

    on = log[log["on_priority_list"]].merge(summ[["incident_id", "title"]].rename(columns={"title": "t"}), on="incident_id")
    order = summ.sort_values("first_flagged")["incident_id"].tolist()
    label = {r.incident_id: (r.title if len(r.title) < 70 else r.title[:67] + "…") for r in summ.itertuples()}
    fig = go.Figure()
    for sev in ["Critical", "High", "Medium"]:
        x = on[on["severity"] == sev]
        fig.add_scatter(x=x["report_date"], y=[label[i] for i in x["incident_id"]], mode="markers", name=sev,
                        marker=dict(size=16, color=config.SEVERITY_COLORS[sev], symbol={"Critical": "circle", "High": "triangle-up",
                                                                                         "Medium": "square"}[sev],
                                    line=dict(color="#fff", width=2)),
                        hovertemplate="%{y}<br>%{x|%a %d %b}: " + sev + "<extra></extra>")
    for r in summ.itertuples():
        fig.add_scatter(x=[r.start_week], y=[label[r.incident_id]], mode="markers", showlegend=r.Index == 0,
                        name="Problem started (week)", marker=dict(size=10, symbol="x-thin", color="#52514e", line=dict(width=2)),
                        hovertemplate="Started w/c %{x|%d %b}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=[label[i] for i in order][::-1])
    charts.week_axis(fig, list(log["report_date"].unique()) + list(summ["start_week"]))
    fig = charts.base_layout(fig, 90 + 56 * len(summ), "Each marker = on the Monday priority list that day", legend=True)
    fig.update_layout(hovermode="closest")
    st.plotly_chart(fig, width="stretch")

    t = summ.assign(**{
        "Problem started": summ["start_week"].dt.strftime("w/c %d %b"),
        "First flagged": summ["first_flagged"].dt.strftime("%a %d %b"),
        "Delay": np.where(summ["detection_lag_weeks"] == 0, "First possible Monday", "+" + summ["detection_lag_weeks"].astype(str) + " wk"),
    })[["title", "team", "Problem started", "First flagged", "Delay", "first_severity", "peak_severity", "weeks_on_list"]]
    st.dataframe(t.rename(columns={"title": "Incident", "team": "Owner", "first_severity": "First severity",
                                   "peak_severity": "Peak severity", "weeks_on_list": "Mondays on list"}),
                 hide_index=True, width="stretch")
    st.markdown("<div class='jc-callout'>For reference, the assignment brief reports that the three historical incidents "
                "were noticed after roughly 3 to 5 weeks. Compare with the <b>First flagged</b> and <b>Delay</b> columns above. "
                "Incidents still open on the selected Monday also appear here from the week they started.</div>",
                unsafe_allow_html=True)
