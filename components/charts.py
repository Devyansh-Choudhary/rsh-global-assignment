"""Plotly chart builders with one consistent visual system.

Rules followed (see dataviz guidance): platforms use fixed categorical slots 1-3 (the
only slots that pass all-pairs colour-blindness checks); severities use the reserved
status palette and always appear with a text label; one y-axis per chart (never dual);
thin marks, recessive grid, hover on every mark.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config

INK = config.INK
FONT = "Inter, system-ui, -apple-system, Segoe UI, sans-serif"


def base_layout(fig: go.Figure, height: int = 300, title: str | None = None, y_title: str | None = None,
                legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=40 if title else 12, b=8),
        paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        font=dict(family=FONT, size=12, color=INK["secondary"]),
        title=dict(text=title, x=0.01, y=0.97, font=dict(size=13, color=INK["primary"], family=FONT)) if title else None,
        hovermode="x unified", hoverlabel=dict(bgcolor="#ffffff", bordercolor=INK["grid"], font=dict(family=FONT, color=INK["primary"])),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
        barcornerradius=4, bargap=0.28,
    )
    fig.update_xaxes(showgrid=False, linecolor=INK["axis"], ticks="", tickfont=dict(color=INK["muted"], size=11))
    fig.update_yaxes(gridcolor=INK["grid"], gridwidth=1, zeroline=False, linecolor=INK["axis"], ticks="",
                     tickfont=dict(color=INK["muted"], size=11), title=dict(text=y_title or "", font=dict(size=11)))
    return fig


def week_axis(fig: go.Figure, weeks, every: int = 1) -> go.Figure:
    """Pin x ticks to the actual Mondays (Plotly's automatic weekly ticks land on Sundays)."""
    w = sorted(pd.to_datetime(pd.Series(list(weeks))).dropna().unique())
    w = w[::every]
    fig.update_xaxes(tickmode="array", tickvals=w, ticktext=[pd.Timestamp(x).strftime("%d %b") for x in w])
    return fig


def weekly_gmv_by_platform(seg_week: pd.DataFrame) -> go.Figure:
    g = seg_week.groupby(["week", "platform"])["sales"].sum().reset_index()
    fig = go.Figure()
    for p in ["Blinkit", "Zepto", "Instamart"]:
        x = g[g["platform"] == p]
        fig.add_bar(x=x["week"], y=x["sales"] / 1e5, name=p, marker_color=config.PLATFORM_COLORS[p],
                    marker_line=dict(color="#ffffff", width=2),
                    hovertemplate=f"{p}: ₹%{{y:.2f}}L<extra></extra>")
    fig.update_layout(barmode="stack")
    week_axis(fig, g["week"])
    return base_layout(fig, 300, "Weekly GMV by platform (₹ lakh)")


def lfl_index_by_platform(seg_week: pd.DataFrame) -> go.Figure:
    """Sales per store-day, indexed to the first week = 100 (removes new-store growth)."""
    g = seg_week.groupby(["week", "platform"])[["sales", "store_days"]].sum().reset_index()
    g["psd"] = g["sales"] / g["store_days"]
    fig = go.Figure()
    for p in ["Blinkit", "Zepto", "Instamart"]:
        x = g[g["platform"] == p].sort_values("week")
        idx = x["psd"] / x["psd"].iloc[0] * 100
        fig.add_scatter(x=x["week"], y=idx, name=p, mode="lines+markers", line=dict(color=config.PLATFORM_COLORS[p], width=2),
                        marker=dict(size=7, line=dict(color="#ffffff", width=2)),
                        hovertemplate=f"{p}: %{{y:.0f}}<extra></extra>")
    fig.add_hline(y=100, line=dict(color=INK["axis"], width=1, dash="dot"))
    week_axis(fig, g["week"])
    return base_layout(fig, 300, "Like-for-like sales per store-day (first week = 100)")


def segment_trend(seg: pd.DataFrame, metric: str, title: str, y_title: str = "", baseline: float | None = None,
                  start: pd.Timestamp | None = None, invert: bool = False, color: str | None = None,
                  height: int = 210, fmt: str = ".0f") -> go.Figure:
    seg = seg.sort_values("week")
    fig = go.Figure()
    fig.add_scatter(x=seg["week"], y=seg[metric], mode="lines+markers", name=title,
                    line=dict(color=color or INK["accent"], width=2), marker=dict(size=7, line=dict(color="#fff", width=2)),
                    hovertemplate=f"%{{x|%d %b}}: %{{y:{fmt}}}<extra></extra>")
    if baseline is not None and np.isfinite(baseline):
        fig.add_hline(y=baseline, line=dict(color=INK["muted"], width=1, dash="dot"),
                      annotation_text="baseline", annotation_font=dict(size=10, color=INK["muted"]),
                      annotation_position="top left")
    if start is not None:
        fig.add_vrect(x0=start - pd.Timedelta(days=3.5), x1=seg["week"].max() + pd.Timedelta(days=3.5),
                      fillcolor=config.SEVERITY_COLORS["Critical"], opacity=0.06, line_width=0)
    # Fixed, honest scales: auto-zoom would make a 2-point wobble look like a collapse
    if metric == "avail":
        fig.update_yaxes(range=[0, 103])
    elif metric == "rank":
        top = max(15.0, float(np.nanmax(seg[metric])) + 1) if len(seg) else 15.0
        fig.update_yaxes(range=[top, 0.5] if invert else [0.5, top])
    elif invert:
        fig.update_yaxes(autorange="reversed")
    else:
        fig.update_yaxes(rangemode="tozero")
    week_axis(fig, seg["week"], every=2 if len(seg) > 6 else 1)
    fig.update_layout(hovermode="closest")
    return base_layout(fig, height, title, y_title, legend=False)


def ordered_vs_delivered(po: pd.DataFrame, title: str) -> go.Figure:
    po = po.sort_values("week")
    fig = go.Figure()
    fig.add_bar(x=po["week"], y=po["ordered"], name="Ordered", marker_color="#b7d3f6",
                hovertemplate="Ordered: %{y:,.0f}<extra></extra>")
    fig.add_bar(x=po["week"], y=po["delivered"], name="Delivered", marker_color=INK["accent"],
                hovertemplate="Delivered: %{y:,.0f}<extra></extra>")
    fig.update_layout(barmode="group")
    week_axis(fig, po["week"], every=2 if len(po) > 6 else 1)
    return base_layout(fig, 260, title, "units")


def heatmap(matrix: pd.DataFrame, title: str, zmin: float, zmax: float, fmt: str = ".0%", reverse: bool = False,
            height: int | None = None, colorbar_title: str = "") -> go.Figure:
    """Sequential single-hue heatmap (red for 'bad' end) with values printed in cells."""
    cols = [c.strftime("%d %b") if hasattr(c, "strftime") else str(c) for c in matrix.columns]
    scale = [[0, "#d03b3b"], [0.55, "#f2b8a2"], [0.85, "#fbe9e1"], [1, "#f4f8fd"]]
    if reverse:
        scale = [[1 - a, c] for a, c in reversed(scale)]
    text = matrix.map(lambda v: "" if pd.isna(v) else format(v, fmt))
    fig = go.Figure(go.Heatmap(z=matrix.values, x=cols, y=list(matrix.index), zmin=zmin, zmax=zmax, colorscale=scale,
                               text=text.values, texttemplate="%{text}", textfont=dict(size=10, color=INK["primary"]),
                               xgap=2, ygap=2, hovertemplate="%{y} · %{x}: %{text}<extra></extra>",
                               colorbar=dict(title=colorbar_title, thickness=10, outlinewidth=0)))
    fig.update_yaxes(autorange="reversed", gridcolor="rgba(0,0,0,0)")
    fig = base_layout(fig, height or max(220, 26 * len(matrix) + 70), title, legend=False)
    fig.update_layout(hovermode="closest")
    return fig


def score_breakdown(row: pd.Series) -> go.Figure:
    parts = ["impact", "urgency", "persistence", "breadth"]
    maxes = config.SCORE_WEIGHTS
    fig = go.Figure()
    fig.add_bar(y=[p.title() for p in parts], x=[maxes[p] for p in parts], orientation="h", marker_color="#f0efec",
                hoverinfo="skip", showlegend=False)
    fig.add_bar(y=[p.title() for p in parts], x=[row[f"score_{p}"] for p in parts], orientation="h",
                marker_color=INK["accent"], showlegend=False,
                text=[f"{row[f'score_{p}']:.0f} / {maxes[p]}" for p in parts], textposition="outside",
                hovertemplate="%{y}: %{x:.1f}<extra></extra>")
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(range=[0, 48], showticklabels=False)
    fig = base_layout(fig, 170, f"Score {row['score']:.0f} = sum × {row['confidence_multiplier']:.2f} confidence", legend=False)
    fig.update_layout(hovermode="closest", margin=dict(l=8, r=8, t=36, b=4))
    return fig


def risk_bars(df: pd.DataFrame) -> go.Figure:
    """Weekly revenue at risk per open incident, with low-high range as error bars."""
    df = df.sort_values("risk_point")
    labels = [t if len(t) < 60 else t[:57] + "…" for t in df["title"]]
    fig = go.Figure(go.Bar(
        y=labels, x=df["risk_point"], orientation="h",
        marker_color=[config.SEVERITY_COLORS.get(s, INK["muted"]) for s in df["severity"]],
        error_x=dict(type="data", symmetric=False, array=df["risk_high"] - df["risk_point"],
                     arrayminus=df["risk_point"] - df["risk_low"], color=INK["secondary"], thickness=1, width=4),
        customdata=np.stack([df["severity"], df["risk_low"], df["risk_high"]], axis=1),
        hovertemplate="%{customdata[0]} · ₹%{x:,.0f}/week (range ₹%{customdata[1]:,.0f}–₹%{customdata[2]:,.0f})<extra></extra>",
    ))
    fig = base_layout(fig, max(180, 46 * len(df) + 60), "Estimated weekly revenue at risk (₹, with range)", legend=False)
    fig.update_layout(hovermode="closest")
    fig.update_xaxes(showgrid=True, gridcolor=INK["grid"], tickprefix="₹")
    return fig
