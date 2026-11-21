"""Shared app state: cached engine calls and the current selection.

``app.py`` sets the state once per run (data source + report Monday); every page reads it
through ``get()``. Keeping the cache functions here lets all pages share one engine run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

import config
from src.engine import EngineResult, load_clean, run_backtest, run_engine
from src.report_generator import build_pdf


def stamp(data_dir: str) -> str:
    p = Path(data_dir)
    return "|".join(f"{f}:{(p / f).stat().st_mtime_ns}" for f in config.FILES.values() if (p / f).exists())


@st.cache_resource(show_spinner="Cleaning and validating the exports…")
def get_clean(data_dir: str, stamp: str):
    return load_clean(data_dir)


@st.cache_resource(show_spinner="Running the detection engine…")
def get_result(data_dir: str, stamp: str, report_date: str) -> EngineResult:
    return run_engine(get_clean(data_dir, stamp), report_date)


@st.cache_resource(show_spinner="Replaying the engine for every past Monday (≈20s, once)…")
def get_backtest(data_dir: str, stamp: str, report_date: str):
    return run_backtest(get_clean(data_dir, stamp), report_date)


@st.cache_data(show_spinner="Building PDF…")
def get_pdf(data_dir: str, stamp: str, report_date: str) -> bytes:
    res = get_result(data_dir, stamp, report_date)
    _, bt = get_backtest(data_dir, stamp, report_date)
    return build_pdf(res, bt, get_clean(data_dir, stamp).profile)


@dataclass
class AppState:
    data_dir: str
    stamp: str
    report: str
    clean: object
    res: EngineResult


_STATE: AppState | None = None


def set_state(s: AppState) -> None:
    global _STATE
    _STATE = s


def get() -> AppState:
    if _STATE is None:
        raise RuntimeError("App state not initialised - run the app via `streamlit run app.py`.")
    return _STATE


def wh_label(wh: str) -> str:
    stores = get().clean.stores
    s = stores[stores["warehouse_id"] == wh]
    return f"{wh} · {s['platform'].iloc[0]} {s['city'].iloc[0]}" if len(s) else wh
