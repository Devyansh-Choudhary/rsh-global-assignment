"""Reports & messages: PDF, CSV and WhatsApp downloads."""
from __future__ import annotations

import html

import streamlit as st

from components.theme import page_header, section
from src.report_generator import action_list, action_list_csv, whatsapp_messages
from views import state
from views.state import get_pdf


def render() -> None:
    app = state.get()
    RES = app.res
    DATA_DIR = app.data_dir
    STAMP = app.stamp
    REPORT = app.report
    page_header("Reports & actions", f"Monday {RES.report_date:%d %B} pack",
                "Downloadable report and action list, plus a ready-to-paste message for each team. "
                "Nothing is sent automatically. Copy a message and send it yourself.")
    c = st.columns(3)
    with c[0]:
        try:
            pdf = get_pdf(DATA_DIR, STAMP, REPORT)
            st.download_button("⬇ Monday report (PDF)", pdf, file_name=f"joy_monday_report_{REPORT}.pdf",
                               mime="application/pdf", width="stretch", type="primary")
        except Exception as e:
            st.error(f"PDF generation failed: {type(e).__name__}: {e}")
    c[1].download_button("⬇ Action list (CSV)", action_list_csv(RES), file_name=f"action_list_{REPORT}.csv",
                         mime="text/csv", width="stretch")
    all_cols = ["priority", "incident_id", "severity", "score", "status", "confidence", "team", "platform", "city",
                "warehouse_id", "n_skus", "start_week", "weeks_active", "risk_low", "risk_point", "risk_high",
                "lost_low", "lost_point", "lost_high", "recoverable_next_week", "title", "recommended_action"]
    c[2].download_button("⬇ All incidents incl. resolved (CSV)",
                         RES.incidents[all_cols].to_csv(index=False).encode("utf-8-sig") if len(RES.incidents) else b"",
                         file_name=f"all_incidents_{REPORT}.csv", mime="text/csv", width="stretch")

    section("WhatsApp messages")
    msgs = whatsapp_messages(RES)
    tabs = st.tabs(list(msgs))
    for tab, (team, m) in zip(tabs, msgs.items()):
        with tab:
            c1, c2 = st.columns([1, 1])
            c1.markdown(f"<div class='jc-wa'>{html.escape(m)}</div>", unsafe_allow_html=True)
            c2.caption("Copy (button at top-right of the box):")
            c2.code(m, language=None, wrap_lines=True)

    section("Action list preview")
    al = action_list(RES)
    if len(al):
        st.dataframe(al[["list", "priority", "severity", "owner_team", "platform", "city", "warehouse_id", "product",
                         "est_weekly_revenue_at_risk_inr", "recommended_action", "action_by"]],
                     hide_index=True, width="stretch",
                     column_config={"est_weekly_revenue_at_risk_inr": st.column_config.NumberColumn("₹ at risk / wk (est.)", format="₹%d"),
                                    "list": "List", "priority": "#", "severity": "Severity", "owner_team": "Owner",
                                    "platform": "Platform", "city": "City", "warehouse_id": "Warehouse", "product": "Product",
                                    "recommended_action": st.column_config.TextColumn("Recommended action", width="large"),
                                    "action_by": "Act by"})
    else:
        st.info("No open actions.")
