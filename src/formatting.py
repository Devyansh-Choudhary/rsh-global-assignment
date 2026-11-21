"""Shared, presentation-neutral formatting helpers (Indian number conventions)."""
from __future__ import annotations

import math

import pandas as pd


def inr(x: float | None, short: bool = True) -> str:
    """Format rupees. short=True gives ₹12.3K / ₹4.56L / ₹1.2Cr."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    sign = "-" if x < 0 else ""
    x = abs(float(x))
    if not short:
        return f"{sign}₹{indian_grouping(round(x))}"
    if x >= 1e7:
        return f"{sign}₹{x / 1e7:.2f}Cr"
    if x >= 1e5:
        return f"{sign}₹{x / 1e5:.2f}L"
    if x >= 1e3:
        return f"{sign}₹{x / 1e3:.1f}K"
    return f"{sign}₹{x:.0f}"


def inr_range(low: float, high: float) -> str:
    if abs(high - low) < 0.05 * max(abs(high), 1):
        return inr((low + high) / 2)
    return f"{inr(low)}–{inr(high)}"


def indian_grouping(n: int) -> str:
    s = str(int(n))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def pct(x: float | None, digits: int = 0, signed: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    fmt = f"{{:{'+' if signed else ''}.{digits}f}}%"
    return fmt.format(x * 100)


def num(x: float | None, digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return indian_grouping(round(x)) if digits == 0 else f"{x:,.{digits}f}"


def d(ts, fmt: str = "%a %d %b") -> str:
    if ts is None or pd.isna(ts):
        return "–"
    return pd.Timestamp(ts).strftime(fmt)


def week_label(ts) -> str:
    """'w/c 21 Sep' (week commencing)."""
    return "–" if ts is None or pd.isna(ts) else f"w/c {pd.Timestamp(ts).strftime('%d %b')}"
