"""Joy Command Center - Streamlit entry point.

Run:  streamlit run app.py
This file only sets up the page, the sidebar (data source + report Monday) and navigation.
Each page lives in views/; all analytics live in src/.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import config
from components.theme import inject
from src.data_loader import DataLoadError
from src.formatting import num
from views import backtest, brief, command_center, data_method, explorer, marketing, platform, reports, state, supply

st.set_page_config(page_title="Joy Command Center", page_icon="◆", layout="wide", initial_sidebar_state="expanded")
inject()


# ======================================================================================
# Sidebar: data source + report Monday
# ======================================================================================
st.logo(str(config.PROJECT_ROOT / "assets" / "logo.svg"), size="large")

if "data_dir" not in st.session_state:
    st.session_state.data_dir = str(config.DATA_DIR)

with st.sidebar.expander("Load this week's exports", expanded=False):
    st.caption("Upload all four CSVs to analyse a new week. Otherwise the bundled 7 Sep–15 Nov data is used.")
    ups = {name: st.file_uploader(fname, type="csv", key=f"up_{name}") for name, fname in config.FILES.items()}
    if all(ups.values()):
        tmp = Path(tempfile.mkdtemp(prefix="joy_upload_"))
        for name, f in ups.items():
            (tmp / config.FILES[name]).write_bytes(f.getvalue())
        st.session_state.data_dir = str(tmp)
        st.success("Using uploaded files.")
    if st.session_state.data_dir != str(config.DATA_DIR) and st.button("Revert to bundled data"):
        st.session_state.data_dir = str(config.DATA_DIR)
        st.rerun()

DATA_DIR = st.session_state.data_dir
STAMP = state.stamp(DATA_DIR)
try:
    CLEAN = state.get_clean(DATA_DIR, STAMP)
except DataLoadError as e:
    st.error(f"Could not load the exports: {e}")
    st.stop()
except Exception as e:  # surface, never hide
    st.error(f"Unexpected error while reading the data: {type(e).__name__}: {e}")
    st.stop()

if CLEAN.sales.empty:
    st.error("No usable sales rows after cleaning. Check the Data & Method page for the quarantine reasons.")
    st.stop()

last_day = CLEAN.sales["date"].max()
latest_monday = (last_day + pd.Timedelta(days=1)) + pd.Timedelta(days=(7 - (last_day + pd.Timedelta(days=1)).weekday()) % 7)
first_monday = CLEAN.sales["date"].min() + pd.Timedelta(days=14)
first_monday = first_monday + pd.Timedelta(days=(7 - first_monday.weekday()) % 7)
mondays = list(pd.date_range(first_monday, latest_monday, freq="7D"))[::-1] or [latest_monday]
with st.sidebar:
    rd_sel = st.selectbox("Report for Monday", mondays, format_func=lambda x: x.strftime("%a %d %b %Y"),
                          help="Replay any past Monday: the engine only uses data available on that day.")
REPORT = rd_sel.strftime("%Y-%m-%d")
RES = state.get_result(DATA_DIR, STAMP, REPORT)
state.set_state(state.AppState(DATA_DIR, STAMP, REPORT, CLEAN, RES))

with st.sidebar:
    if rd_sel != mondays[0]:
        st.info(f"Replay mode: you are seeing what the tool would have said on {rd_sel:%d %b}.")
    st.caption(f"Data window: {CLEAN.sales['date'].min():%d %b} – {last_day:%d %b %Y} · "
               f"{num(len(CLEAN.sales))} sales rows · {CLEAN.stores['dark_store_id'].nunique()} dark stores")

# ======================================================================================
# Navigation (one module per page in views/)
# ======================================================================================
pages = [
    st.Page(brief.render, title="Monday brief", icon="🏠", url_path="brief", default=True),
    st.Page(command_center.render, title="Command Center", icon="🚨", url_path="alerts"),
    st.Page(supply.render, title="Supply", icon="📦", url_path="supply"),
    st.Page(marketing.render, title="Marketing", icon="🔎", url_path="marketing"),
    st.Page(platform.render, title="Platform KAMs", icon="🤝", url_path="platform"),
    st.Page(explorer.render, title="Explorer", icon="🧭", url_path="explorer"),
    st.Page(backtest.render, title="Back-test", icon="⏪", url_path="backtest"),
    st.Page(reports.render, title="Reports & messages", icon="📄", url_path="reports"),
    st.Page(data_method.render, title="Data & method", icon="🧪", url_path="data"),
]
st.navigation(pages, position="sidebar").run()
