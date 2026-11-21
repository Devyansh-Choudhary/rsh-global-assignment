"""Global styling: one stylesheet, injected once per page render."""
from __future__ import annotations

import html

import streamlit as st

import config

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {{
  --ink: {config.INK['primary']}; --ink2: {config.INK['secondary']}; --muted: {config.INK['muted']};
  --grid: {config.INK['grid']}; --surface: #ffffff; --page: {config.INK['page']};
  --accent: {config.INK['accent']}; --good: {config.INK['good']}; --bad: {config.INK['bad']};
  --crit: {config.SEVERITY_COLORS['Critical']}; --high: {config.SEVERITY_COLORS['High']};
  --med: {config.SEVERITY_COLORS['Medium']}; --low: {config.SEVERITY_COLORS['Low']};
}}
html, body, [class*="css"], .stMarkdown, .stText, button, input, select, textarea {{
  font-family: 'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif !important;
}}
.stApp {{ background: var(--page); }}
#MainMenu, footer, [data-testid="stDecoration"] {{ display: none !important; }}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1320px; }}
h1, h2, h3, h4 {{ color: var(--ink); letter-spacing: -0.01em; }}
h1 {{ font-weight: 700 !important; font-size: 1.65rem !important; }}
h3 {{ font-weight: 600 !important; font-size: 1.08rem !important; margin-top: .6rem !important; }}
section[data-testid="stSidebar"] {{ background: #ffffff; border-right: 1px solid var(--grid); }}
section[data-testid="stSidebar"] .block-container {{ padding-top: 1rem; }}

.jc-eyebrow {{ color: var(--muted); font-size: .78rem; font-weight: 500; text-transform: uppercase; letter-spacing: .06em; }}
.jc-title {{ font-size: 1.65rem; font-weight: 700; color: var(--ink); margin: .1rem 0 .15rem 0; line-height: 1.2; }}
.jc-sub {{ color: var(--ink2); font-size: .92rem; margin-bottom: .9rem; }}
.jc-hero {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 12px; padding: 18px 22px; margin-bottom: 14px; }}
.jc-hero-big {{ font-size: 1.32rem; font-weight: 650; color: var(--ink); line-height: 1.35; }}
.jc-hero-big b {{ font-weight: 700; }}
.jc-note {{ color: var(--ink2); font-size: .85rem; margin-top: 6px; }}

.jc-kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 4px 0 14px 0; }}
.jc-kpi {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 10px; padding: 12px 14px; }}
.jc-kpi .l {{ color: var(--muted); font-size: .74rem; font-weight: 500; text-transform: uppercase; letter-spacing: .04em; }}
.jc-kpi .v {{ color: var(--ink); font-size: 1.45rem; font-weight: 700; margin-top: 2px; font-variant-numeric: tabular-nums; }}
.jc-kpi .s {{ color: var(--ink2); font-size: .78rem; margin-top: 1px; }}
.jc-kpi .up {{ color: var(--good); font-weight: 600; }}
.jc-kpi .down {{ color: var(--bad); font-weight: 600; }}
.jc-kpi.alert {{ border-color: var(--crit); box-shadow: inset 3px 0 0 var(--crit); }}

.jc-card {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 12px; padding: 14px 18px; margin-bottom: 10px; }}
.jc-card.sev-Critical {{ box-shadow: inset 4px 0 0 var(--crit); }}
.jc-card.sev-High {{ box-shadow: inset 4px 0 0 var(--high); }}
.jc-card.sev-Medium {{ box-shadow: inset 4px 0 0 var(--med); }}
.jc-card.sev-Low {{ box-shadow: inset 4px 0 0 var(--low); }}
.jc-card .meta {{ color: var(--muted); font-size: .78rem; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
.jc-card .t {{ font-size: 1.02rem; font-weight: 650; color: var(--ink); margin: 4px 0 4px 0; }}
.jc-card .w {{ color: var(--ink2); font-size: .88rem; line-height: 1.45; }}
.jc-card .a {{ color: var(--ink); font-size: .88rem; margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--grid); }}
.jc-card .nums {{ display: flex; gap: 22px; flex-wrap: wrap; margin-top: 10px; }}
.jc-card .nums div {{ font-size: .76rem; color: var(--muted); }}
.jc-card .nums b {{ display: block; color: var(--ink); font-size: .98rem; font-weight: 650; font-variant-numeric: tabular-nums; }}

.jc-pill {{ display: inline-flex; align-items: center; gap: 5px; padding: 1px 9px; border-radius: 999px; font-size: .74rem;
           font-weight: 600; border: 1px solid var(--grid); background: #fff; color: var(--ink); white-space: nowrap; }}
.jc-pill .dot {{ font-size: .8rem; line-height: 1; }}
.jc-tag {{ display: inline-block; padding: 1px 8px; border-radius: 6px; font-size: .74rem; background: #f0efec; color: var(--ink2); }}
.jc-row {{ display: grid; grid-template-columns: 28px 110px 1fr 130px 150px 130px; gap: 10px; align-items: center;
          padding: 10px 14px; border-bottom: 1px solid var(--grid); font-size: .88rem; }}
.jc-row:last-child {{ border-bottom: none; }}
.jc-row.h {{ color: var(--muted); font-size: .72rem; text-transform: uppercase; letter-spacing: .04em; font-weight: 600; }}
.jc-table {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 12px; overflow: hidden; margin-bottom: 14px; }}
@media (max-width: 900px) {{
  .jc-row {{ grid-template-columns: 22px 1fr; }}
  .jc-row > :nth-child(n+4) {{ grid-column: 2; }}
  .jc-row.h {{ display: none; }}
}}
.jc-section {{ font-size: .78rem; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 600; margin: 18px 0 8px 0; }}
.jc-callout {{ background: #f4f8fd; border: 1px solid #cde2fb; border-radius: 10px; padding: 10px 14px; color: var(--ink); font-size: .86rem; margin-bottom: 12px; }}
.jc-wa {{ background: #e7f6e9; border-radius: 10px; padding: 12px 14px; white-space: pre-wrap; font-size: .86rem; color: #111; border: 1px solid #cfe9d3; }}
div[data-testid="stExpander"] details {{ background: var(--surface); border: 1px solid var(--grid) !important; border-radius: 10px; }}
div[data-testid="stMetric"] {{ background: var(--surface); border: 1px solid var(--grid); border-radius: 10px; padding: 10px 14px; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 6px; }}
.stTabs [data-baseweb="tab"] {{ height: 38px; padding: 0 12px; }}
</style>
"""


def inject() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(eyebrow: str, title: str, sub: str = "") -> None:
    st.markdown(
        f"<div class='jc-eyebrow'>{html.escape(eyebrow)}</div><div class='jc-title'>{html.escape(title)}</div>"
        + (f"<div class='jc-sub'>{sub}</div>" if sub else ""),
        unsafe_allow_html=True,
    )


def section(label: str) -> None:
    st.markdown(f"<div class='jc-section'>{html.escape(label)}</div>", unsafe_allow_html=True)


def severity_pill(sev: str) -> str:
    color = config.SEVERITY_COLORS.get(sev, config.INK["muted"])
    icon = config.SEVERITY_ICONS.get(sev, "○")
    return f"<span class='jc-pill'><span class='dot' style='color:{color}'>{icon}</span>{html.escape(sev)}</span>"


def platform_tag(p: str) -> str:
    color = config.PLATFORM_COLORS.get(p, config.INK["muted"])
    return (f"<span class='jc-pill'><span class='dot' style='color:{color}'>■</span>{html.escape(p)}</span>")
