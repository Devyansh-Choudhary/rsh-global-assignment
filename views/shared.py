"""Helpers used by more than one page."""
from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from src.feature_engineering import safe_div
from views import state


def perf_table(by: str) -> pd.DataFrame:
    app = state.get()
    FEAT = app.res.features
    sw, lw, pw = FEAT.seg_week, FEAT.latest_week, FEAT.latest_week - pd.Timedelta(days=7)
    g = sw[sw["week"].isin([lw, pw])].groupby([by, "week"]).apply(
        lambda x: pd.Series({"sales": x["sales"].sum(), "sd": x["store_days"].sum(),
                             "avail": np.average(x["avail"], weights=x["store_days"])}), include_groups=False).reset_index()
    cur, prev = g[g["week"] == lw].set_index(by), g[g["week"] == pw].set_index(by)
    po = FEAT.po_week[(FEAT.po_week["week"] == lw) & FEAT.po_week["has_po"]]
    fr = po.groupby(by).apply(lambda x: safe_div(x["delivered"].sum(), x["ordered"].sum()).item(), include_groups=False)
    out = pd.DataFrame({
        by.title(): cur.index, "GMV (₹ lakh)": cur["sales"].values / 1e5,
        "WoW %": (safe_div(cur["sales"] - prev["sales"].reindex(cur.index), prev["sales"].reindex(cur.index)) * 100),
        "LFL WoW %": safe_div(cur["sales"] / cur["sd"] - (prev["sales"] / prev["sd"]).reindex(cur.index),
                              (prev["sales"] / prev["sd"]).reindex(cur.index)) * 100,
        "Availability %": cur["avail"].values, "PO fill %": fr.reindex(cur.index).values * 100,
    })
    return out.sort_values("GMV (₹ lakh)", ascending=False)


def perf_cfg(by: str) -> dict:
    return {"GMV (₹ lakh)": st.column_config.NumberColumn(format="₹%.2fL"),
            "WoW %": st.column_config.NumberColumn(format="%+.1f%%"),
            "LFL WoW %": st.column_config.NumberColumn(format="%+.1f%%", help="Per store-day, removes new-store effect"),
            "Availability %": st.column_config.NumberColumn(format="%.1f"),
            "PO fill %": st.column_config.NumberColumn(format="%.1f")}
