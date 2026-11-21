"""KPI tiles rendered as one HTML grid (responsive, consistent)."""
from __future__ import annotations

import html
import math

import streamlit as st


def delta_html(change: float | None, good_when_up: bool = True, fmt: str = "{:+.1f}%", scale: float = 100) -> str:
    if change is None or (isinstance(change, float) and math.isnan(change)):
        return ""
    up = change >= 0
    cls = "up" if (up == good_when_up) else "down"
    arrow = "▲" if up else "▼"
    return f"<span class='{cls}'>{arrow} {fmt.format(change * scale)}</span>"


def kpi(label: str, value: str, sub: str = "", alert: bool = False) -> str:
    return (f"<div class='jc-kpi{' alert' if alert else ''}'><div class='l'>{html.escape(label)}</div>"
            f"<div class='v'>{html.escape(value)}</div><div class='s'>{sub}</div></div>")


def kpi_row(tiles: list[str]) -> None:
    st.markdown("<div class='jc-kpis'>" + "".join(tiles) + "</div>", unsafe_allow_html=True)
