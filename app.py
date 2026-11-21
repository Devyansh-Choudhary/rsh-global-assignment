"""Joy Command Center - Streamlit app.

Run:  streamlit run app.py
All analytics live in src/; this file only lays out what the engine produced.
"""
from __future__ import annotations

import html
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
from components import charts
from components.alerts import incident_card, incident_detail, priority_table
from components.filters import incident_filters
from components.metrics import delta_html, kpi, kpi_row
from components.theme import inject, page_header, section, severity_pill
from src.data_loader import DataLoadError
from src.engine import EngineResult, load_clean, run_backtest, run_engine
from src.feature_engineering import safe_div
from src.formatting import d, inr, inr_range, num, pct
from src.po_analysis import po_compliance_matrix
from src.report_generator import action_list, action_list_csv, build_pdf, team_briefs, whatsapp_messages
from src.supply_analysis import fill_rate_matrix, supply_scorecard

st.set_page_config(page_title="Joy Command Center", page_icon="◆", layout="wide", initial_sidebar_state="expanded")
inject()


# ======================================================================================
# Data access (cached)
# ======================================================================================
def _stamp(data_dir: str) -> str:
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
STAMP = _stamp(DATA_DIR)
try:
    CLEAN = get_clean(DATA_DIR, STAMP)
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
RES = get_result(DATA_DIR, STAMP, REPORT)
K = RES.kpis
FEAT = RES.features

with st.sidebar:
    if rd_sel != mondays[0]:
        st.info(f"Replay mode: you are seeing what the tool would have said on {rd_sel:%d %b}.")
    st.caption(f"Data window: {CLEAN.sales['date'].min():%d %b} – {last_day:%d %b %Y} · "
               f"{num(len(CLEAN.sales))} sales rows · {CLEAN.stores['dark_store_id'].nunique()} dark stores")


def wh_label(wh: str) -> str:
    s = CLEAN.stores[CLEAN.stores["warehouse_id"] == wh]
    return f"{wh} · {s['platform'].iloc[0]} {s['city'].iloc[0]}" if len(s) else wh


# ======================================================================================
# Pages
# ======================================================================================
def page_overview() -> None:
    prio = RES.priority
    n = len(prio)
    page_header(f"Monday brief · {RES.report_date:%A %d %B %Y}",
                f"{n} issue{'s' if n != 1 else ''} need{'s' if n == 1 else ''} action this week" if n else "No issues need action this week",
                f"Week of {K['latest_week']:%d %b} – {K['latest_week'] + pd.Timedelta(days=6):%d %b} · "
                f"{K['stores']} dark stores · Blinkit, Zepto, Instamart · 6 cities")
    if n:
        top = prio.iloc[0]
        teams = ", ".join(sorted(prio["team"].unique()))
        st.markdown(f"""<div class='jc-hero'><div class='jc-hero-big'>An estimated <b>{inr_range(K['risk_low'], K['risk_high'])}</b>
of sales is at risk <b>every week</b> from {n} open issue{'s' if n > 1 else ''}; about <b>{inr(K['recoverable'])}</b> of it is
recoverable next week if {teams} act today. Biggest: <b>{html.escape(top['title'])}</b>.</div>
<div class='jc-note'>{html.escape(K['context_note'])}</div></div>""", unsafe_allow_html=True)

    kpi_row([
        kpi("GMV last week", inr(K["gmv"]), f"{delta_html(K['gmv_wow'])} WoW · like-for-like {delta_html(K['lfl_wow'])}"),
        kpi("Units sold", num(K["units"]), f"{delta_html(safe_div(K['units'] - K['units_prev'], K['units_prev']).item())} WoW"),
        kpi("Avg availability", f"{K['avail']:.1f}%", f"{delta_html((K['avail'] - K['avail_prev']) / 100, fmt='{:+.1f} pts')} WoW"),
        kpi("PO fill rate", pct(K["fill_rate"], 1), f"{delta_html(K['fill_rate'] - K['fill_rate_prev'], fmt='{:+.1f} pts')} vs prior PO"),
        kpi("Critical / high", f"{K['n_critical']} / {K['n_high']}", f"{K['n_medium']} medium · {len(RES.watchlist)} on watch",
            alert=K["n_critical"] > 0),
        kpi("Weekly ₹ at risk (est.)", inr(K["risk_point"]), f"range {inr_range(K['risk_low'], K['risk_high'])}", alert=K["risk_point"] > 0),
    ])

    section("This week's priority list")
    if n:
        priority_table(prio)
        st.caption("Open the Command Center for evidence, trends and the reasoning behind each rank.")
    else:
        st.success("Nothing crosses the action threshold this week. See the watchlist in the Command Center.")

    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.weekly_gmv_by_platform(FEAT.seg_week), use_container_width=True)
    c2.plotly_chart(charts.lfl_index_by_platform(FEAT.seg_week), use_container_width=True)

    open_inc = RES.incidents[RES.incidents["status"] != "Resolved"] if len(RES.incidents) else RES.incidents
    if len(open_inc):
        st.plotly_chart(charts.risk_bars(open_inc), use_container_width=True)

    section("Platform and city performance (last week)")
    c1, c2 = st.columns(2)
    for col, by in ((c1, "platform"), (c2, "city")):
        col.dataframe(_perf_table(by), hide_index=True, use_container_width=True, column_config=_perf_cfg(by))


def _perf_table(by: str) -> pd.DataFrame:
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


def _perf_cfg(by: str) -> dict:
    return {"GMV (₹ lakh)": st.column_config.NumberColumn(format="₹%.2fL"),
            "WoW %": st.column_config.NumberColumn(format="%+.1f%%"),
            "LFL WoW %": st.column_config.NumberColumn(format="%+.1f%%", help="Per store-day, removes new-store effect"),
            "Availability %": st.column_config.NumberColumn(format="%.1f"),
            "PO fill %": st.column_config.NumberColumn(format="%.1f")}


def page_command() -> None:
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


def page_supply() -> None:
    page_header("Supply intelligence", "Did we deliver what the platforms ordered?",
                "Fill rate = delivered ÷ ordered per PO line (each line carries its own delivery, 2–4 days after the Monday PO).")
    po = FEAT.po_week
    lw = po[(po["week"] == FEAT.latest_week) & po["has_po"] & po["delivered"].notna()]
    short = lw[lw["fill_rate"] < config.FILL_RATE_THRESHOLD]
    kpi_row([
        kpi("Fill rate, latest PO", pct(K["fill_rate"], 1), f"PO of {FEAT.latest_week:%d %b}"),
        kpi("Units short, latest PO", num((lw["ordered"] - lw["delivered"]).sum()), f"of {num(lw['ordered'].sum())} ordered"),
        kpi("PO lines below 80%", str(len(short)), f"{short['warehouse_id'].nunique()} warehouse(s)", alert=len(short) > 0),
        kpi("Missed POs this week", str(int((~po[po["week"] == FEAT.latest_week]["has_po"]).sum())), "warehouse × SKU"),
    ])
    sup_inc = RES.incidents[(RES.incidents["type"] == "SUPPLY_SHORTFALL") & (RES.incidents["status"] != "Resolved")] \
        if len(RES.incidents) else RES.incidents
    section("Supply actions")
    briefs = team_briefs(RES)["Supply"]
    if briefs:
        rows = []
        for it in briefs:
            for ln in it["lines"]:
                rows.append({"Severity": it["severity"], "Warehouse": it["warehouse_id"], "City": f"{it['platform']} {it['city']}",
                             "SKU": ln["sku_code"], "Product": ln["product"], "Ordered": ln["ordered"], "Delivered": ln["delivered"],
                             "Shortfall": ln["shortfall"], "Fill %": ln["fill_rate"] * 100 if pd.notna(ln["fill_rate"]) else np.nan,
                             "Ship now (units)": ln["latest_shortfall"], "Avail. last 3d %": ln["avail_now"],
                             "Type": it["kind"], "Due": it["deadline"]})
        tbl = pd.DataFrame(rows)
        for col in ["Ordered", "Delivered", "Shortfall", "Fill %", "Ship now (units)", "Avail. last 3d %"]:
            tbl[col] = tbl[col].map(lambda v: "–" if pd.isna(v) else f"{v:,.0f}")
        st.dataframe(tbl, hide_index=True, use_container_width=True)
        for it in briefs:
            st.markdown(f"{severity_pill(it['severity'])} **{it['warehouse_id']}** – {html.escape(it['action'])}",
                        unsafe_allow_html=True)
    else:
        st.success("No supply actions this week.")
    for _, r in sup_inc.iterrows():
        incident_detail(r, RES, f"sup_{r['incident_id']}")

    section("Fill rate by warehouse and PO week")
    m = fill_rate_matrix(FEAT)
    m.index = [wh_label(w) for w in m.index]
    st.plotly_chart(charts.heatmap(m, "", 0.4, 1.0, ".0%"), use_container_width=True)
    st.caption("A whole warehouse row can look healthy while individual SKUs are badly short - see the SKU table below.")

    section("Warehouse × SKU scorecard (last 4 PO weeks)")
    sc = supply_scorecard(FEAT)
    c = st.columns(3)
    pf = c[0].multiselect("Platform", sorted(sc["platform"].unique()), key="sup_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(sc["city"].unique()), key="sup_c", placeholder="All")
    only = c[2].toggle("Only fill rate < 90%", value=False)
    if pf:
        sc = sc[sc["platform"].isin(pf)]
    if cf:
        sc = sc[sc["city"].isin(cf)]
    if only:
        sc = sc[sc["fill_rate"] < 0.9]
    show = sc.sort_values("fill_rate")[["platform", "city", "warehouse_id", "sku_code", "product_name", "ordered", "delivered",
                                         "shortfall_units", "fill_rate", "last_ordered", "last_delivered", "last_fill_rate", "po_weeks"]]
    st.dataframe(show, hide_index=True, use_container_width=True, column_config={
        "fill_rate": st.column_config.ProgressColumn("4-wk fill", format="percent", min_value=0, max_value=1),
        "last_fill_rate": st.column_config.NumberColumn("Latest fill", format="percent"),
        "po_weeks": st.column_config.NumberColumn("POs (of 4)")})

    section("Drill into a warehouse")
    c = st.columns(2)
    wh = c[0].selectbox("Warehouse", sorted(po["warehouse_id"].unique()), format_func=wh_label, key="sup_wh")
    sku = c[1].selectbox("SKU", sorted(po["sku_code"].unique()), key="sup_sku",
                         format_func=lambda s: f"{s} · {FEAT.products.set_index('sku_code').loc[s, 'product_name']}")
    p = po[(po["warehouse_id"] == wh) & (po["sku_code"] == sku)]
    s = FEAT.seg_week[(FEAT.seg_week["warehouse_id"] == wh) & (FEAT.seg_week["sku_code"] == sku)]
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.ordered_vs_delivered(p, "Ordered vs delivered (units)"), use_container_width=True)
    c2.plotly_chart(charts.segment_trend(s, "avail", "Dark-store availability (%)", height=260, fmt=".1f"), use_container_width=True)


def page_marketing() -> None:
    page_header("Marketing intelligence", "Where are we hard to find?",
                "Search-rank and sales signals, separated from stock problems. Ads cannot fix an empty shelf.")
    st.markdown("<div class='jc-callout'><b>Marketing vs supply.</b> A rank or sales drop is routed to Marketing only "
                "when availability stayed normal. If stock also fell, the rank drop is treated as a symptom of the stockout "
                "and the issue goes to Supply or the platform KAM instead.</div>", unsafe_allow_html=True)
    mk = team_briefs(RES)["Marketing"]
    inc = RES.incidents
    mk_inc = inc[(inc["team"] == "Marketing") & (inc["status"] != "Resolved")] if len(inc) else inc
    section("Recommended interventions")
    if len(mk_inc):
        for _, r in mk_inc.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"mk_{r['incident_id']}")
    else:
        st.success("No search-visibility issues this week.")
    if mk["pause_ads"]:
        st.markdown("**⏸ Pause paid ads until back in stock:** " + html.escape("; ".join(mk["pause_ads"])))

    section("Search visibility watch: latest week vs baseline")
    rf = RES.flags["rank"]
    lw = rf[rf["week"] == FEAT.latest_week].copy()
    lw["rank_change"] = lw["rank"] - lw["rank_base"]
    sf = RES.flags["sales"][RES.flags["sales"]["week"] == FEAT.latest_week][["warehouse_id", "sku_code", "expected_units", "sales_z"]]
    lw = lw.merge(sf, on=["warehouse_id", "sku_code"], how="left")
    lw["sales_vs_expected"] = safe_div(lw["units"] - lw["expected_units"], lw["expected_units"]) * 100
    lw["Signal"] = np.where(lw["rank_flag"] & (lw["avail"] >= config.AVAIL_FLOOR), "Visibility loss",
                            np.where(lw["rank_flag"], "Rank + stockout", ""))
    c = st.columns(3)
    pf = c[0].multiselect("Platform", sorted(lw["platform"].unique()), key="mk_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(lw["city"].unique()), key="mk_c", placeholder="All")
    catf = c[2].multiselect("Category", sorted(lw["category"].unique()), key="mk_cat", placeholder="All")
    for col, sel in (("platform", pf), ("city", cf), ("category", catf)):
        if sel:
            lw = lw[lw[col].isin(sel)]
    show = lw.sort_values("rank_change", ascending=False)[
        ["Signal", "platform", "city", "product_name", "category", "rank_base", "rank", "rank_change", "avail", "units",
         "expected_units", "sales_vs_expected", "sales"]]
    show = show.rename(columns={"platform": "Platform", "city": "City", "product_name": "Product", "category": "Category",
                                "units": "Units"})
    st.dataframe(show, hide_index=True, use_container_width=True, column_config={
        "Signal": st.column_config.TextColumn(width="medium"),
        "rank_base": st.column_config.NumberColumn("Baseline rank", format="%.1f"),
        "rank": st.column_config.NumberColumn("Rank last wk", format="%.1f"),
        "rank_change": st.column_config.NumberColumn("Δ positions", format="%+.1f", help="Positive = worse"),
        "avail": st.column_config.NumberColumn("Avail %", format="%.0f"),
        "expected_units": st.column_config.NumberColumn("Expected units", format="%.0f"),
        "sales_vs_expected": st.column_config.NumberColumn("Units vs expected", format="%+.0f%%"),
        "sales": st.column_config.NumberColumn("Sales ₹", format="₹%d")})
    st.caption("Expected units = the SKU's own recent share of platform sales × this week's platform volume. "
               "This removes festive promotions and new-store growth from the comparison.")

    section("Rank history by platform-city (selected product)")
    prod = st.selectbox("Product", FEAT.products["product_name"].tolist(), key="mk_prod",
                        index=int(np.argmax(FEAT.products["sku_code"].eq("JY-FW-NEEM-100"))) if
                        FEAT.products["sku_code"].eq("JY-FW-NEEM-100").any() else 0)
    sku = FEAT.products.set_index("product_name").loc[prod, "sku_code"]
    m = FEAT.seg_week[FEAT.seg_week["sku_code"] == sku].assign(seg=lambda x: x["platform"] + " " + x["city"]) \
        .pivot_table(index="seg", columns="week", values="rank")
    st.plotly_chart(charts.heatmap(m, "Average search rank (1 = top; red = worse)", 1, 14, ".1f", reverse=True),
                    use_container_width=True)


def page_platform() -> None:
    page_header("Platform intelligence", "Ordering, replenishment and listing health",
                "What each platform's warehouses are (and are not) ordering, and where the KAMs need to escalate.")
    inc = RES.incidents
    kam = inc[(inc["team"] == "Platform KAM") & (inc["status"] != "Resolved")] if len(inc) else inc
    section("KAM escalations")
    if len(kam):
        for _, r in kam.iterrows():
            incident_card(r)
            incident_detail(r, RES, f"kam_{r['incident_id']}")
    else:
        st.success("No ordering or replenishment escalations this week.")
    rk = inc[(inc["type"] == "SEARCH_RANK_DROP") & (inc["status"] != "Resolved")] if len(inc) else inc
    for _, r in rk.iterrows():
        st.markdown(f"**Listing check for KAM:** {html.escape(r['title'])} – ask {r['platform']} whether a ranking or "
                    f"listing change happened around {d(r['onset_date'], '%d %b')}.")

    section("Platform scorecard (last week)")
    t = _perf_table("platform")
    po = FEAT.po_week[FEAT.po_week["week"] == FEAT.latest_week]
    missed = po.groupby("platform")["has_po"].apply(lambda x: int((~x).sum()))
    t["Missed POs"] = t["Platform"].map(missed).fillna(0).astype(int)
    st.dataframe(t, hide_index=True, use_container_width=True, column_config=_perf_cfg("platform"))

    section("PO compliance: share of SKUs each warehouse ordered, by week")
    m = po_compliance_matrix(FEAT)
    m.index = [wh_label(w) for w in m.index]
    st.plotly_chart(charts.heatmap(m, "", 0.8, 1.0, ".0%"), use_container_width=True)

    section("Missed purchase orders (all weeks)")
    gaps = FEAT.po_week[~FEAT.po_week["has_po"]][["week", "platform", "city", "warehouse_id", "sku_code", "product_name"]]
    if len(gaps):
        sig = RES.signals["PO_INACTIVITY"]
        flagged = set()
        for r in sig.itertuples():
            for w in pd.date_range(r.start_week, r.end_week, freq="7D"):
                flagged.add((r.warehouse_id, r.sku_code, w))
        gaps["Flagged as inactivity"] = [(w, s, k) in flagged for w, s, k in zip(gaps["warehouse_id"], gaps["sku_code"], gaps["week"])]
        gaps = gaps.rename(columns={"platform": "Platform", "city": "City", "warehouse_id": "Warehouse", "sku_code": "SKU",
                                    "product_name": "Product"})
        st.dataframe(gaps.sort_values("week", ascending=False), hide_index=True, use_container_width=True,
                     column_config={"week": st.column_config.DateColumn("PO week", format="DD MMM YYYY")})
    else:
        st.success("Every warehouse ordered every SKU every week.")


def page_explorer() -> None:
    page_header("Explorer", "Product & store drill-down", "Weekly history for any slice of the network.")
    s = FEAT.seg_week
    c = st.columns(4)
    pf = c[0].multiselect("Platform", sorted(s["platform"].unique()), key="ex_p", placeholder="All")
    cf = c[1].multiselect("City", sorted(s["city"].unique()), key="ex_c", placeholder="All")
    prod = c[2].multiselect("Product", FEAT.products["product_name"].tolist(), key="ex_pr", placeholder="All")
    whs = sorted(s["warehouse_id"].unique())
    wf = c[3].multiselect("Warehouse", whs, key="ex_w", format_func=wh_label, placeholder="All")
    sel = s.copy()
    if pf:
        sel = sel[sel["platform"].isin(pf)]
    if cf:
        sel = sel[sel["city"].isin(cf)]
    if prod:
        sel = sel[sel["product_name"].isin(prod)]
    if wf:
        sel = sel[sel["warehouse_id"].isin(wf)]
    if sel.empty:
        st.info("No data for this selection.")
        return
    w = sel.groupby("week").apply(lambda x: pd.Series({
        "sales": x["sales"].sum(), "units": x["units"].sum(), "store_days": x["store_days"].sum(),
        "avail": np.average(x["avail"], weights=x["store_days"]), "rank": np.average(x["rank"], weights=x["store_days"])}),
        include_groups=False).reset_index()
    w["sales_psd"] = w["sales"] / w["store_days"]
    lw = w.iloc[-1]
    prev = w.iloc[-2] if len(w) > 1 else lw
    kpi_row([kpi("Sales last week", inr(lw["sales"]), f"{delta_html(safe_div(lw['sales'] - prev['sales'], prev['sales']).item())} WoW"),
             kpi("Units", num(lw["units"]), ""), kpi("Availability", f"{lw['avail']:.1f}%", ""),
             kpi("Avg search rank", f"{lw['rank']:.1f}", "1 = top"),
             kpi("₹ per store-day", f"₹{lw['sales_psd']:.0f}", f"{delta_html(safe_div(lw['sales_psd'] - prev['sales_psd'], prev['sales_psd']).item())} LFL")])
    c1, c2 = st.columns(2)
    c1.plotly_chart(charts.segment_trend(w, "sales", "Weekly sales (₹)", fmt=",.0f", height=240), use_container_width=True)
    c2.plotly_chart(charts.segment_trend(w, "sales_psd", "Sales per store-day (₹, like-for-like)", fmt=",.0f", height=240),
                    use_container_width=True)
    c3, c4 = st.columns(2)
    c3.plotly_chart(charts.segment_trend(w, "avail", "Availability (%)", fmt=".1f", height=240), use_container_width=True)
    c4.plotly_chart(charts.segment_trend(w, "rank", "Search rank (1 = top)", invert=True, fmt=".1f", height=240),
                    use_container_width=True)

    section("Dark stores in this selection (last week)")
    sw = FEAT.store_week
    keys = sel[["warehouse_id", "sku_code"]].drop_duplicates()
    st_sel = sw.merge(keys, on=["warehouse_id", "sku_code"])
    last = st_sel[st_sel["week"] == FEAT.latest_week].groupby(["dark_store_id", "platform", "city", "warehouse_id"]).agg(
        sales=("sales", "sum"), units=("units", "sum"), avail=("avail", "mean"), rank=("rank", "mean"),
        skus_oos=("avail", lambda x: int((x < config.STORE_OOS_THRESHOLD).sum()))).reset_index()
    opened = CLEAN.stores.set_index("dark_store_id")["opened_date"]
    last["opened"] = last["dark_store_id"].map(opened)
    st.dataframe(last.sort_values("avail"), hide_index=True, use_container_width=True, column_config={
        "sales": st.column_config.NumberColumn("Sales ₹", format="₹%d"), "avail": st.column_config.NumberColumn("Avail %", format="%.1f"),
        "rank": st.column_config.NumberColumn("Avg rank", format="%.1f"),
        "skus_oos": st.column_config.NumberColumn("SKUs < 50% avail"),
        "opened": st.column_config.DateColumn("Opened", format="DD MMM YYYY")})

    store = st.selectbox("Daily view for a dark store", ["–"] + last["dark_store_id"].tolist(), key="ex_store")
    if store != "–":
        dd = FEAT.sales[FEAT.sales["dark_store_id"] == store]
        if prod:
            dd = dd[dd["product_name"].isin(prod)]
        piv = dd.pivot_table(index="product_name", columns="date", values="availability_pct")
        piv.columns = [c.strftime("%d %b") for c in piv.columns]
        st.plotly_chart(charts.heatmap(piv.iloc[:, -28:] / 100, f"{store}: daily availability, last 28 days", 0, 1, ".0%"),
                        use_container_width=True)


def page_backtest() -> None:
    page_header("Back-test", "Would we have caught it in week one?",
                "The engine is replayed for every past Monday using only the data available that morning "
                "(sales up to Sunday, POs raised so far, deliveries already made). Nothing is hard-coded.")
    log, summ = get_backtest(DATA_DIR, STAMP, REPORT)
    if summ.empty:
        st.info("No incidents reached the priority list in the replay window.")
        return
    kpi_row([kpi("Incidents caught", str(len(summ)), "reached the Monday priority list"),
             kpi("Flagged on first possible Monday", f"{int((summ['detection_lag_weeks'] == 0).sum())} / {len(summ)}",
                 "the Monday after the problem's first week"),
             kpi("Max items on any Monday", str(int(log[log['on_priority_list']].groupby('report_date').size().max())),
                 "no alert fatigue"),
             kpi("Mondays replayed", str(log["report_date"].nunique()), f"{log['report_date'].min():%d %b} – {log['report_date'].max():%d %b}")])

    on = log[log["on_priority_list"]].merge(summ[["incident_id", "title"]].rename(columns={"title": "t"}), on="incident_id")
    order = summ.sort_values("first_flagged")["incident_id"].tolist()
    label = {r.incident_id: (r.title if len(r.title) < 70 else r.title[:67] + "…") for r in summ.itertuples()}
    fig = go.Figure()
    for sev in ["Critical", "High", "Medium"]:
        x = on[on["severity"] == sev]
        fig.add_scatter(x=x["report_date"], y=[label[i] for i in x["incident_id"]], mode="markers", name=sev,
                        marker=dict(size=16, color=config.SEVERITY_COLORS[sev], symbol={"Critical": "circle", "High": "triangle-up",
                                                                                         "Medium": "square"}[sev],
                                    line=dict(color="#fff", width=2)),
                        hovertemplate="%{y}<br>%{x|%a %d %b}: " + sev + "<extra></extra>")
    for r in summ.itertuples():
        fig.add_scatter(x=[r.start_week], y=[label[r.incident_id]], mode="markers", showlegend=r.Index == 0,
                        name="Problem started (week)", marker=dict(size=10, symbol="x-thin", color="#52514e", line=dict(width=2)),
                        hovertemplate="Started w/c %{x|%d %b}<extra></extra>")
    fig.update_yaxes(categoryorder="array", categoryarray=[label[i] for i in order][::-1])
    charts.week_axis(fig, list(log["report_date"].unique()) + list(summ["start_week"]))
    fig = charts.base_layout(fig, 90 + 56 * len(summ), "Each marker = on the Monday priority list that day", legend=True)
    fig.update_layout(hovermode="closest")
    st.plotly_chart(fig, use_container_width=True)

    t = summ.assign(**{
        "Problem started": summ["start_week"].dt.strftime("w/c %d %b"),
        "First flagged": summ["first_flagged"].dt.strftime("%a %d %b"),
        "Delay": np.where(summ["detection_lag_weeks"] == 0, "First possible Monday", "+" + summ["detection_lag_weeks"].astype(str) + " wk"),
    })[["title", "team", "Problem started", "First flagged", "Delay", "first_severity", "peak_severity", "weeks_on_list"]]
    st.dataframe(t.rename(columns={"title": "Incident", "team": "Owner", "first_severity": "First severity",
                                   "peak_severity": "Peak severity", "weeks_on_list": "Mondays on list"}),
                 hide_index=True, use_container_width=True)
    st.markdown("<div class='jc-callout'>For reference, the assignment brief reports that the three historical incidents "
                "were noticed after roughly 3 to 5 weeks. Compare with the <b>First flagged</b> and <b>Delay</b> columns above. "
                "Incidents still open on the selected Monday also appear here from the week they started.</div>",
                unsafe_allow_html=True)


def page_reports() -> None:
    page_header("Reports & actions", f"Monday {RES.report_date:%d %B} pack",
                "Downloadable report and action list, plus a ready-to-paste message for each team. "
                "Nothing is sent automatically. Copy a message and send it yourself.")
    c = st.columns(3)
    with c[0]:
        try:
            pdf = get_pdf(DATA_DIR, STAMP, REPORT)
            st.download_button("⬇ Monday report (PDF)", pdf, file_name=f"joy_monday_report_{REPORT}.pdf",
                               mime="application/pdf", use_container_width=True, type="primary")
        except Exception as e:
            st.error(f"PDF generation failed: {type(e).__name__}: {e}")
    c[1].download_button("⬇ Action list (CSV)", action_list_csv(RES), file_name=f"action_list_{REPORT}.csv",
                         mime="text/csv", use_container_width=True)
    all_cols = ["priority", "incident_id", "severity", "score", "status", "confidence", "team", "platform", "city",
                "warehouse_id", "n_skus", "start_week", "weeks_active", "risk_low", "risk_point", "risk_high",
                "lost_low", "lost_point", "lost_high", "recoverable_next_week", "title", "recommended_action"]
    c[2].download_button("⬇ All incidents incl. resolved (CSV)",
                         RES.incidents[all_cols].to_csv(index=False).encode("utf-8-sig") if len(RES.incidents) else b"",
                         file_name=f"all_incidents_{REPORT}.csv", mime="text/csv", use_container_width=True)

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
                     hide_index=True, use_container_width=True,
                     column_config={"est_weekly_revenue_at_risk_inr": st.column_config.NumberColumn("₹ at risk / wk (est.)", format="₹%d"),
                                    "list": "List", "priority": "#", "severity": "Severity", "owner_team": "Owner",
                                    "platform": "Platform", "city": "City", "warehouse_id": "Warehouse", "product": "Product",
                                    "recommended_action": st.column_config.TextColumn("Recommended action", width="large"),
                                    "action_by": "Act by"})
    else:
        st.info("No open actions.")


def page_data() -> None:
    page_header("Data quality & methodology", "What we found in the exports, and how the engine works")
    p = CLEAN.profile
    kpi_row([kpi("Rows processed", num(p["rows_in"].sum()), "across 4 files"),
             kpi("Duplicates removed", num(p["duplicates_removed"].sum()), "exact duplicate sales rows"),
             kpi("Rows quarantined", num(p["quarantined"].sum()), "unusable, kept for review"),
             kpi("City spellings fixed", str(int(CLEAN.stores["city_raw"].str.strip().ne(CLEAN.stores["city"]).sum())),
                 f"{CLEAN.stores['city_raw'].nunique()} variants → {CLEAN.stores['city'].nunique()} cities")])
    tabs = st.tabs(["Cleaning log", "Row counts", "Quarantine", "Methodology", "Thresholds", "Limitations"])
    with tabs[0]:
        st.dataframe(CLEAN.log.to_frame(), hide_index=True, use_container_width=True)
    with tabs[1]:
        st.dataframe(p, hide_index=True, use_container_width=True)
    with tabs[2]:
        if len(CLEAN.quarantine):
            st.dataframe(CLEAN.quarantine, hide_index=True, use_container_width=True)
        else:
            st.success("No rows needed quarantining in this data set: every row parsed and joined.")
    with tabs[3]:
        st.markdown(METHOD_MD)
    with tabs[4]:
        th = [(k, getattr(config, k)) for k in ["BASELINE_WEEKS", "FILL_RATE_THRESHOLD", "SEVERE_FILL_RATE", "MIN_REGULAR_PO_WEEKS",
                                                 "MIN_WAREHOUSE_ACTIVITY_SHARE", "AVAIL_DROP_POINTS", "AVAIL_FLOOR",
                                                 "STORE_OOS_THRESHOLD", "AVAIL_STABLE_POINTS", "RANK_DROP_POSITIONS",
                                                 "SALES_DROP_RELATIVE", "SALES_Z_THRESHOLD", "MIN_WEEKLY_SALES_INR",
                                                 "CONSEQUENCE_LAG_WEEKS", "IMPACT_CAP_SHARE_OF_GMV", "MAX_PRIORITY_ISSUES"]]
        st.dataframe(pd.DataFrame(th, columns=["Setting (config.py)", "Value"]).astype(str), hide_index=True, use_container_width=True)
        st.caption("Each value's justification from the data is documented next to it in config.py and in the README.")
    with tabs[5]:
        st.markdown(LIMITS_MD)


METHOD_MD = """
**Pipeline.** Load as text → validate & clean (audit log) → weekly tables at *warehouse × SKU* grain → five detectors →
root-cause linking → grouping → revenue estimate → score → team briefs (app, PDF, CSV, WhatsApp all use the same output).

**Grain.** Every platform warehouse serves exactly one city on one platform, so *warehouse × SKU* = *platform × city × SKU*.
That is where POs are raised, so supply, availability, rank and sales signals all join cleanly there. All comparisons over
time use **per-store-day** rates because 51 stores opened during the window.

**Detectors** (each compares a week with the segment's previous 4 *healthy* weeks, so an ongoing incident never becomes the new normal):

| Detector | Fires when | Owner |
|---|---|---|
| Purchase-order inactivity | A warehouse that ordered a SKU every week skips it, while still ordering its other SKUs, and demand had not collapsed | Platform KAM |
| Supply shortfall | PO-line fill rate < 80% (normal: 81–100%, median 97%) | Supply |
| Availability drop | Weekly availability ≥ 15 pts below baseline **and** < 75% (normal never < 82.7%) | Linked to its cause; else KAM |
| Search-rank drop | Mean rank ≥ 3 positions worse than baseline (normal weekly movement ≤ 0.7) | Marketing if stock is fine |
| Sales decline | Units ≥ 30% below expected **and** z ≤ −3, where expected = own share of platform volume | QC Manager |

**Root-cause linking.** PO inactivity → supply shortfall → availability → rank → sales. A downstream signal overlapping an
upstream one (allowing 2 weeks for stock to run down) becomes evidence, not a second alert, so revenue is counted once.

**Revenue.** *Observed* = export figures. *Lost to date* = Σ(expected − actual), expected = pre-incident ₹/store-day ×
store-days × platform like-for-like trend (unaffected SKUs); range from a second method (availability gap or same-SKU trend
in other cities). *At risk / week* = next week's loss if nothing changes (last week's rate vs last-3-days rate; PO stoppages
include full exposure). *Recoverable* = at-risk × share of week left after the fix's lead time.

**Priority score** = confidence × (40·impact + 25·urgency + 15·persistence + 20·breadth). Critical ≥ 70, High ≥ 50, Medium ≥ 30.
"""

LIMITS_MD = """
- **Availability, not inventory.** We see the share of the day a product could be ordered, not stock on hand, so we cannot
  forecast the exact day a store will run out.
- **Short history.** Data starts 7 Sep. A "regular buyer" is one that ordered every observed week; with longer history this
  should require 3–4 weeks. The Hyderabad sunscreen stoppage starts in week 2, so its revenue reference week is a single week.
- **Search rank is a store-level average** of the category search position reported by the app; we do not see competitors,
  ad spend or ratings, so the *cause* of a rank drop (algorithm, competitor ads, reviews) is a question for Marketing/KAM.
- **Estimates are counterfactual.** Lost-sales figures assume demand would have continued at the pre-incident rate, moved with
  unaffected SKUs. Substitution to other Joy SKUs is not netted off, so true brand-level loss may be lower.
- **Delivery timing.** We trust the delivery date and quantity on each PO line; we cannot see partial deliveries split across days.
- **Single-week sales dips** are kept on a watchlist rather than escalated: at ~30 units/week per SKU-city, one week is often noise.
"""

# ======================================================================================
# Navigation
# ======================================================================================
pages = [
    st.Page(page_overview, title="Monday brief", icon="🏠", url_path="brief", default=True),
    st.Page(page_command, title="Command Center", icon="🚨", url_path="alerts"),
    st.Page(page_supply, title="Supply", icon="📦", url_path="supply"),
    st.Page(page_marketing, title="Marketing", icon="🔎", url_path="marketing"),
    st.Page(page_platform, title="Platform KAMs", icon="🤝", url_path="platform"),
    st.Page(page_explorer, title="Explorer", icon="🧭", url_path="explorer"),
    st.Page(page_backtest, title="Back-test", icon="⏪", url_path="backtest"),
    st.Page(page_reports, title="Reports & messages", icon="📄", url_path="reports"),
    st.Page(page_data, title="Data & method", icon="🧪", url_path="data"),
]
st.navigation(pages, position="sidebar").run()
