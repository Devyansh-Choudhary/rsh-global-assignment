"""Filter bar for incident lists (one row, above the content)."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def incident_filters(inc: pd.DataFrame, key: str) -> pd.DataFrame:
    if inc.empty:
        return inc
    c = st.columns([1, 1, 1.4, 1, 1, 1.1])
    plat = c[0].multiselect("Platform", sorted(inc["platform"].unique()), key=f"{key}_p", placeholder="All")
    city = c[1].multiselect("City", sorted(inc["city"].unique()), key=f"{key}_c", placeholder="All")
    prods = sorted({p for ps in inc["products"] for p in ps})
    prod = c[2].multiselect("Product", prods, key=f"{key}_pr", placeholder="All")
    sev = c[3].multiselect("Severity", ["Critical", "High", "Medium", "Low"], key=f"{key}_s", placeholder="All")
    team = c[4].multiselect("Owner", sorted(inc["team"].unique()), key=f"{key}_t", placeholder="All")
    status = c[5].selectbox("Status", ["Open issues", "All incl. resolved", "Resolved only"], key=f"{key}_st")
    out = inc
    if plat:
        out = out[out["platform"].isin(plat)]
    if city:
        out = out[out["city"].isin(city)]
    if prod:
        out = out[out["products"].map(lambda ps: any(p in prod for p in ps))]
    if sev:
        out = out[out["severity"].isin(sev)]
    if team:
        out = out[out["team"].isin(team)]
    if status == "Open issues":
        out = out[out["status"] != "Resolved"]
    elif status == "Resolved only":
        out = out[out["status"] == "Resolved"]
    return out
