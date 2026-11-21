"""Command Center: ranked incident cards with evidence."""
from __future__ import annotations

import streamlit as st

from components.alerts import incident_card, incident_detail
from components.filters import incident_filters
from components.theme import page_header, section
from views import state


def render() -> None:
    RES = state.get().res
    page_header("Command Center", "Priority alerts",
                "Every open issue, ranked by impact × urgency × persistence × breadth × confidence. "
                "Expand any alert to see the evidence and the maths.")
    inc = RES.incidents
    if inc.empty:
        st.success("No anomalies detected for this Monday.")
        return
    f = incident_filters(inc, "cc")
    prio_ids = set(RES.priority["incident_id"])
    main = f[f["incident_id"].isin(prio_ids)]
    watch = f[(f["status"] != "Resolved") & ~f["incident_id"].isin(prio_ids)]
    resolved = f[f["status"] == "Resolved"]
    if len(main):
        section(f"Act now · {len(main)}")
        for _, r in main.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"m_{r['incident_id']}")
    if len(watch):
        section(f"Watchlist · {len(watch)} (below the action bar)")
        st.caption("Real but small, low-confidence or recovering. Shown for completeness; not sent to teams as actions.")
        for _, r in watch.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"w_{r['incident_id']}")
    if len(resolved):
        section(f"Resolved in the data window · {len(resolved)}")
        for _, r in resolved.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"r_{r['incident_id']}")
    if not (len(main) or len(watch) or len(resolved)):
        st.info("No incidents match these filters.")
