"""Incident cards with expandable, fully traceable evidence."""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

import config
from components import charts
from components.theme import platform_tag, severity_pill
from src.formatting import d, inr, inr_range, num, pct


def incident_card(r: pd.Series) -> None:
    esc = html.escape
    st.markdown(f"""
<div class='jc-card sev-{r['severity'] if r['status'] != 'Resolved' else 'Low'}'>
  <div class='meta'>{severity_pill(r['severity'])}{platform_tag(r['platform'])}
    <span>#{r['priority']}</span><span>·</span><span>{esc(r['city'])} · {esc(r['warehouse_id'])}</span><span>·</span>
    <span>Owner: <b style='color:var(--ink)'>{esc(r['team'])}</b></span><span>·</span><span>{esc(r['status'])}</span>
    <span>·</span><span>{esc(r['confidence'])} confidence</span><span>·</span><span>Since {d(r['impact_start_week'], 'w/c %d %b')}</span>
  </div>
  <div class='t'>{esc(r['title'])}</div>
  <div class='w'>{esc(r['what_happened'])}</div>
  <div class='nums'>
    <div>Weekly revenue at risk (est.)<b>{esc(inr_range(r['risk_low'], r['risk_high']) if r['risk_point'] > 0 else '₹0 – resolved')}</b></div>
    <div>Lost sales to date (est.)<b>{esc(inr_range(r['lost_low'], r['lost_high']))}</b></div>
    <div>Recoverable next week (est.)<b>{esc(inr(r['recoverable_next_week']))}</b></div>
    <div>Stores affected<b>{r['stores_hit']} / {r['n_stores']}</b></div>
    <div>Duration<b>{r['weeks_active']} wk</b></div>
    <div>SKUs<b>{r['n_skus']}</b></div>
  </div>
  <div class='a'><b>Next action →</b> {esc(r['recommended_action'])}</div>
</div>""", unsafe_allow_html=True)


def incident_detail(r: pd.Series, res, key: str) -> None:
    feat = res.features
    segs = res.segments[res.segments["incident_id"] == r["incident_id"]]
    with st.expander("Evidence, estimate breakdown and trends", expanded=False):
        t1, t2, t3, t4 = st.tabs(["Evidence", "Trends", "Revenue estimate", "Why this priority"])
        with t1:
            for e in r["evidence"]:
                st.markdown(f"- {html.escape(e)}")
            if r["linked_signals"]:
                st.caption("Also triggered (absorbed as evidence, not double-counted): "
                           + ", ".join(config.ISSUE_LABELS[s] for s in r["linked_signals"]))
            st.caption(f"Confidence: {r['confidence']} – {r['confidence_reason']}.")
        with t2:
            sku = segs["sku_code"].iloc[0]
            if len(segs) > 1:
                labels = dict(zip(segs["sku_code"], segs["product_name"]))
                sku = st.selectbox("SKU", list(labels), format_func=lambda s: labels[s], key=f"{key}_sku")
            s = feat.seg_week[(feat.seg_week["warehouse_id"] == r["warehouse_id"]) & (feat.seg_week["sku_code"] == sku)]
            start = r["impact_start_week"]
            c1, c2 = st.columns(2)
            c1.plotly_chart(charts.segment_trend(s, "avail", "Availability (%)", start=start, fmt=".1f"),
                            use_container_width=True, key=f"{key}_a")
            c2.plotly_chart(charts.segment_trend(s, "rank", "Search rank (1 = top)", invert=True, start=start, fmt=".1f"),
                            use_container_width=True, key=f"{key}_r")
            c3, c4 = st.columns(2)
            c3.plotly_chart(charts.segment_trend(s, "sales", "Sales (₹ per week)", start=start, fmt=",.0f"),
                            use_container_width=True, key=f"{key}_s")
            po = feat.po_week[(feat.po_week["warehouse_id"] == r["warehouse_id"]) & (feat.po_week["sku_code"] == sku)]
            c4.plotly_chart(charts.ordered_vs_delivered(po, "Purchase orders (units)"), use_container_width=True, key=f"{key}_po")
            st.caption("Shaded area = incident window. Week labels are the Monday of each week.")
        with t3:
            est = pd.DataFrame([
                ["Observed: avg weekly sales in reference weeks", inr(r["observed_ref_weekly"]), "Fact (export)"],
                ["Observed: sales in the latest affected week", inr(r["observed_latest"]), "Fact (export)"],
                ["Estimated lost sales to date", inr_range(r["lost_low"], r["lost_high"]), f"Peer-trend vs {r['alt_method']}"],
                ["Estimated weekly revenue at risk", inr_range(r["risk_low"], r["risk_high"]) if r["risk_point"] else "₹0 (resolved)",
                 "Last week's loss rate vs last-3-days run-rate" + (" vs full exposure" if r["type"] == "PO_INACTIVITY" else "")],
                ["Potentially recoverable next week", inr(r["recoverable_next_week"]),
                 f"{config.RECOVERY_FRACTION[r['type']]:.0%} of a week left after the fix's lead time"],
            ], columns=["Measure", "Value", "Basis"])
            st.dataframe(est, hide_index=True, use_container_width=True)
            st.caption(f"Reference period: {r['ref_source']}. Estimates assume sales would have continued at the SKU's "
                       "pre-incident rate per store-day, moved in line with unaffected SKUs on the same platform. "
                       "They are not booked losses.")
            cols = {"product_name": "Product", "sku_code": "SKU", "stores_hit": "Stores hit", "ref_avail": "Ref. avail %",
                    "latest_avail": "Last wk avail %", "last3d_avail": "Last 3d avail %", "ref_weekly_sales": "Ref. ₹/wk",
                    "observed_sales": "Latest ₹", "lost_point": "Lost to date ₹ (est.)", "risk_point": "₹ at risk / wk (est.)",
                    "ref_weeks": "Reference weeks"}
            st.dataframe(segs[list(cols)].rename(columns=cols).round(1), hide_index=True, use_container_width=True)
        with t4:
            c1, c2 = st.columns([1, 1.2])
            c1.plotly_chart(charts.score_breakdown(r), use_container_width=True, key=f"{key}_sc")
            c2.markdown(f"**{r['why_ranked']}**")
            c2.caption("Impact: √(weekly ₹ at risk ÷ 1% of network GMV) × 40 · Urgency: worsening 25, active 20, "
                       "recovering 7.5 · Persistence: weeks ÷ 4 × 15 · Breadth: 70% share of stores hit + 30% SKUs (÷5), × 20.")


def priority_table(df: pd.DataFrame) -> None:
    """Compact Monday list (HTML grid) used on the overview."""
    esc = html.escape
    rows = ["<div class='jc-row h'><div>#</div><div>Severity</div><div>Issue</div><div>Owner</div>"
            "<div>₹ at risk / week (est.)</div><div>Act by</div></div>"]
    for r in df.itertuples():
        deadline = "Today" if r.severity == "Critical" else ("Within 48h" if r.severity == "High" else "This week")
        rows.append(f"<div class='jc-row'><div><b>{r.priority}</b></div><div>{severity_pill(r.severity)}</div>"
                    f"<div><b>{esc(r.title)}</b><div style='color:var(--muted);font-size:.78rem'>{esc(r.issue)} · "
                    f"{esc(r.status)} · {esc(r.confidence)} confidence</div></div>"
                    f"<div>{esc(r.team)}</div><div><b>{esc(inr_range(r.risk_low, r.risk_high))}</b></div>"
                    f"<div>{deadline}</div></div>")
    st.markdown("<div class='jc-table'>" + "".join(rows) + "</div>", unsafe_allow_html=True)
