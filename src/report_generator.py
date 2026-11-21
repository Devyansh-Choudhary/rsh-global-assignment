"""Team briefs, CSV action list, WhatsApp messages and the Monday PDF.

All four outputs are rendered from the same ``team_briefs`` structure, which is built
from one ``EngineResult``. The dashboard, PDF and CSV therefore always agree.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

import config
from src.engine import EngineResult
from src.formatting import d, inr, inr_range, num, pct

DEADLINE = {"Critical": "Today", "High": "Within 48h", "Medium": "This week", "Low": "Monitor"}
SEV_EMOJI = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "⚪"}


# --------------------------------------------------------------------------------------
# Structured content
# --------------------------------------------------------------------------------------
def _open_incidents(res: EngineResult) -> pd.DataFrame:
    inc = res.incidents
    return inc if inc.empty else inc[inc["status"] != "Resolved"]


def _deadline(sev: str, report_date: pd.Timestamp) -> str:
    if sev == "Critical":
        return f"Today ({d(report_date, '%a %d %b')})"
    if sev == "High":
        return f"By {d(report_date + pd.Timedelta(days=1), '%a %d %b')}"
    return DEADLINE[sev]


def team_briefs(res: EngineResult) -> dict:
    """Everything each team needs, in that team's language."""
    rd = res.report_date
    inc = _open_incidents(res)
    seg = res.segments
    prio_ids = set(res.priority["incident_id"]) if len(res.priority) else set()

    def segs_for(iid):
        return seg[seg["incident_id"] == iid] if len(seg) else seg

    supply, kam, marketing = [], [], []
    for r in inc.itertuples():
        on_list = r.incident_id in prio_ids
        base = {"incident_id": r.incident_id, "severity": r.severity if on_list else "Low",
                "on_priority_list": on_list, "platform": r.platform, "city": r.city,
                "warehouse_id": r.warehouse_id, "title": r.title, "action": r.recommended_action,
                "deadline": _deadline(r.severity, rd) if on_list else "Monitor",
                "risk": inr_range(r.risk_low, r.risk_high), "risk_point": r.risk_point,
                "confidence": r.confidence, "status": r.status}
        s = segs_for(r.incident_id)
        if r.type == "SUPPLY_SHORTFALL":
            lines = [{"sku_code": x.sku_code, "product": x.product_name, "ordered": x.ordered_units,
                      "delivered": x.delivered_units, "shortfall": x.shortfall_units, "fill_rate": x.fill_rate,
                      "latest_shortfall": x.latest_shortfall_units, "latest_fill": x.latest_fill_rate,
                      "avail_now": x.last3d_avail} for x in s.itertuples()]
            supply.append({**base, "kind": "Under-delivery", "lines": lines,
                           "ship_now_units": float(sum(line["latest_shortfall"] for line in lines))})
        elif r.type == "PO_INACTIVITY":
            lines = [{"sku_code": x.sku_code, "product": x.product_name, "missed_cycles": x.missed_cycles,
                      "last_po": x.last_po_week, "avg_weekly_order": x.avg_weekly_order,
                      "avail_now": x.last3d_avail, "stores_hit": x.stores_hit, "n_stores": x.n_stores}
                     for x in s.itertuples()]
            kam.append({**base, "kind": "Ordering stopped", "lines": lines})
            supply.append({**base, "kind": "Stand-by stock (platform not ordering)", "lines": [
                {"sku_code": x["sku_code"], "product": x["product"], "ordered": np.nan, "delivered": np.nan,
                 "shortfall": np.nan, "fill_rate": np.nan, "latest_shortfall": x["avg_weekly_order"],
                 "latest_fill": np.nan, "avail_now": x["avail_now"]} for x in lines],
                "ship_now_units": float(sum(x["avg_weekly_order"] or 0 for x in lines)),
                "action": f"Keep ~{num(sum(x['avg_weekly_order'] or 0 for x in lines))} units ready to ship to "
                          f"{r.warehouse_id} within 48h of the KAM securing a PO."})
        elif r.type == "AVAILABILITY_DROP":
            lines = [{"sku_code": x.sku_code, "product": x.product_name, "avail_ref": x.ref_avail,
                      "avail_now": x.last3d_avail, "stores_hit": x.stores_hit, "n_stores": x.n_stores}
                     for x in s.itertuples()]
            kam.append({**base, "kind": "Dark-store stockouts, POs normal", "lines": lines})
        elif r.type == "SEARCH_RANK_DROP":
            lines = [{"sku_code": x.sku_code, "product": x.product_name, "rank_before": x.baseline_rank,
                      "rank_now": x.latest_rank, "avail_now": x.latest_avail, "lost_to_date": x.lost_point}
                     for x in s.itertuples()]
            marketing.append({**base, "kind": "Search visibility loss", "lines": lines,
                              "lost_to_date": inr_range(r.lost_low, r.lost_high),
                              "recoverable": inr(r.recoverable_next_week)})
            kam.append({**base, "kind": "Ranking / listing check", "lines": lines,
                        "action": f"Ask {r.platform} whether a search-algorithm, listing or content change affected "
                                  f"{', '.join(r.products)} in {r.city} around {d(r.onset_date, '%d %b')}; support "
                                  f"Marketing's ad push."})

    # Marketing should not spend on ads where the product is out of stock
    pause_ads = []
    for r in inc.itertuples():
        if r.type in ("PO_INACTIVITY", "SUPPLY_SHORTFALL", "AVAILABILITY_DROP"):
            pause_ads.append(f"{r.platform} {r.city}: {', '.join(p.replace('Joy ', '') for p in r.products)}")

    k = res.kpis
    prio = res.priority
    qc = {
        "kpis": k,
        "priority": [
            {"rank": i + 1, "incident_id": r.incident_id, "severity": r.severity, "title": r.title,
             "team": r.team, "risk": inr_range(r.risk_low, r.risk_high), "risk_point": r.risk_point,
             "lost": inr_range(r.lost_low, r.lost_high), "recoverable": inr(r.recoverable_next_week),
             "deadline": _deadline(r.severity, rd), "confidence": r.confidence, "status": r.status,
             "action": r.recommended_action, "why": r.why_ranked}
            for i, r in enumerate(prio.itertuples())
        ],
        "watchlist": [{"incident_id": r.incident_id, "title": r.title, "team": r.team,
                       "risk": inr_range(r.risk_low, r.risk_high), "confidence": r.confidence,
                       "reason": r.confidence_reason, "action": r.recommended_action}
                      for r in res.watchlist.itertuples()] if len(res.watchlist) else [],
        "resolved": [{"incident_id": r.incident_id, "title": r.title, "team": r.team,
                      "weeks": r.weeks_active, "lost": inr_range(r.lost_low, r.lost_high), "lost_point": r.lost_point}
                     for r in res.resolved.itertuples() if r.confidence != "Low"] if len(res.resolved) else [],
        "accountability": (prio.groupby("team").agg(issues=("incident_id", "count"), risk=("risk_point", "sum"))
                           .sort_values("risk", ascending=False).reset_index().to_dict("records")) if len(prio) else [],
    }
    return {"report_date": rd, "QC Manager": qc, "Supply": supply, "Platform KAM": kam,
            "Marketing": {"items": marketing, "pause_ads": pause_ads}}


# --------------------------------------------------------------------------------------
# CSV action list
# --------------------------------------------------------------------------------------
def action_list(res: EngineResult) -> pd.DataFrame:
    """One row per open incident x SKU, ready for Excel/Sheets."""
    inc, seg = _open_incidents(res), res.segments
    if inc.empty:
        return pd.DataFrame()
    prio_ids = set(res.priority["incident_id"])
    m = seg.merge(inc[["incident_id", "priority", "severity", "score", "team", "issue", "title",
                       "recommended_action", "confidence"]], on="incident_id", suffixes=("_sku", ""))
    m["list"] = np.where(m["incident_id"].isin(prio_ids), "Priority", "Watchlist")
    m["action_by"] = [(_deadline(s, res.report_date) if lst == "Priority" else "Monitor")
                      for s, lst in zip(m["severity"], m["list"])]
    out = pd.DataFrame({
        "report_date": res.report_date.strftime("%Y-%m-%d"), "list": m["list"], "priority": m["priority"],
        "incident_id": m["incident_id"], "severity": m["severity"], "score": m["score"],
        "owner_team": m["team"], "issue": m["issue"], "status": m["status"], "confidence": m["confidence"],
        "platform": m["platform"], "city": m["city"], "warehouse_id": m["warehouse_id"],
        "sku_code": m["sku_code"], "product": m["product_name"],
        "ordered_units": m["ordered_units"], "delivered_units": m["delivered_units"],
        "shortfall_units": m["shortfall_units"], "fill_rate_pct": (m["fill_rate"] * 100).round(1),
        "missed_po_cycles": m["missed_cycles"], "last_po_week": pd.to_datetime(m["last_po_week"]).dt.strftime("%Y-%m-%d"),
        "search_rank_before": m["baseline_rank"].round(1), "search_rank_now": m["latest_rank"].round(1),
        "availability_ref_pct": m["ref_avail"].round(1), "availability_last_week_pct": m["latest_avail"].round(1),
        "availability_last_3_days_pct": m["last3d_avail"].round(1),
        "stores_affected": m["stores_hit"], "stores_total": m["n_stores"],
        "est_weekly_revenue_at_risk_low_inr": m["risk_low"].round(0),
        "est_weekly_revenue_at_risk_inr": m["risk_point"].round(0),
        "est_weekly_revenue_at_risk_high_inr": m["risk_high"].round(0),
        "est_lost_sales_to_date_low_inr": m["lost_low"].round(0),
        "est_lost_sales_to_date_inr": m["lost_point"].round(0),
        "est_lost_sales_to_date_high_inr": m["lost_high"].round(0),
        "est_recoverable_next_week_inr": m["recoverable_next_week"].round(0),
        "baseline_weeks": m["ref_weeks"], "recommended_action": m["recommended_action"], "action_by": m["action_by"],
    })
    return out.sort_values(["list", "priority", "sku_code"], ascending=[True, True, True]).reset_index(drop=True)


def action_list_csv(res: EngineResult) -> bytes:
    return action_list(res).to_csv(index=False).encode("utf-8-sig")  # BOM so Excel shows ₹/UTF-8 correctly


# --------------------------------------------------------------------------------------
# WhatsApp
# --------------------------------------------------------------------------------------
def whatsapp_messages(res: EngineResult) -> dict[str, str]:
    b = team_briefs(res)
    rd = d(b["report_date"], "%a %d %b")
    msgs = {}

    # QC Manager
    qc, k = b["QC Manager"], b["QC Manager"]["kpis"]
    lines = [f"*Joy Quick Commerce – Monday brief, {rd}*",
             f"Last week: GMV {inr(k['gmv'])} ({pct(k['gmv_wow'], 1, True)} WoW, like-for-like {pct(k['lfl_wow'], 1, True)}), "
             f"availability {k['avail']:.1f}%, PO fill {pct(k['fill_rate'], 1)}."]
    if k.get("context_note"):
        lines.append(f"_{k['context_note']}_")
    lines.append(f"\n*{len(qc['priority'])} issues need action* – est. {inr_range(k['risk_low'], k['risk_high'])}/week at risk:")
    for p in qc["priority"]:
        lines.append(f"{SEV_EMOJI[p['severity']]} {p['rank']}. {p['title']} → *{p['team']}*, {p['deadline']} "
                     f"({p['risk']}/wk)")
    if qc["watchlist"]:
        lines.append("\nWatching (no action yet): " + "; ".join(w["title"] for w in qc["watchlist"]))
    msgs["QC Manager"] = "\n".join(lines)

    # Supply
    sup = b["Supply"]
    if sup:
        lines = [f"*Joy Supply – actions for {rd}*"]
        for it in sup:
            lines.append(f"\n{SEV_EMOJI[it['severity']]} *{it['platform']} {it['city']} – {it['warehouse_id']}* ({it['kind']})")
            for ln in it["lines"]:
                if it["kind"] == "Under-delivery":
                    lines.append(f"• {ln['product'].replace('Joy ', '')} ({ln['sku_code']}): delivered "
                                 f"{num(ln['delivered'])}/{num(ln['ordered'])} since start ({pct(ln['fill_rate'])}); "
                                 f"last PO short {num(ln['latest_shortfall'])} units")
                else:
                    lines.append(f"• {ln['product'].replace('Joy ', '')} ({ln['sku_code']}): ~{num(ln['latest_shortfall'])} units/week")
            lines.append(f"➡️ {it['action']}")
            lines.append(f"Due: {it['deadline']} · est. {it['risk']}/week at risk")
        msgs["Supply"] = "\n".join(lines)
    else:
        msgs["Supply"] = f"*Joy Supply – {rd}*\nNo supply actions this week. Fill rate {pct(k['fill_rate'], 1)}."

    # Platform KAMs (one block per platform)
    kam = b["Platform KAM"]
    if kam:
        lines = [f"*Joy Platform KAMs – escalations for {rd}*"]
        for plat in sorted({x["platform"] for x in kam}):
            lines.append(f"\n*{plat}*")
            for it in [x for x in kam if x["platform"] == plat]:
                lines.append(f"{SEV_EMOJI[it['severity']]} {it['city']} / {it['warehouse_id']} – {it['kind']}")
                for ln in it["lines"]:
                    if it["kind"] == "Ordering stopped":
                        lines.append(f"• {ln['product'].replace('Joy ', '')}: no PO for {int(ln['missed_cycles'])} week(s) "
                                     f"(last {d(ln['last_po'], '%d %b')}, ~{num(ln['avg_weekly_order'])}/wk); "
                                     f"availability now {ln['avail_now']:.0f}%, {ln['stores_hit']}/{ln['n_stores']} stores out")
                    elif it["kind"] == "Ranking / listing check":
                        lines.append(f"• {ln['product'].replace('Joy ', '')}: search rank #{ln['rank_before']:.0f} → #{ln['rank_now']:.0f}")
                    else:
                        lines.append(f"• {ln['product'].replace('Joy ', '')}: availability {ln['avail_ref']:.0f}% → "
                                     f"{ln['avail_now']:.0f}%, {ln['stores_hit']}/{ln['n_stores']} stores")
                lines.append(f"➡️ {it['action']}")
        msgs["Platform KAM"] = "\n".join(lines)
    else:
        msgs["Platform KAM"] = f"*Joy Platform KAMs – {rd}*\nNo platform escalations this week."

    # Marketing
    mk = b["Marketing"]
    if mk["items"]:
        lines = [f"*Joy Marketing – search visibility, {rd}*"]
        for it in mk["items"]:
            ln = it["lines"][0]
            lines.append(f"\n{SEV_EMOJI[it['severity']]} *{it['title']}*")
            lines.append(f"Stock is fine ({ln['avail_now']:.0f}% available), so this is visibility, not supply. "
                         f"Est. lost so far {it['lost_to_date']}; {it['risk']}/week at risk; ~{it['recoverable']} recoverable next week if ads go live now.")
            lines.append(f"➡️ {it['action']}")
        if mk["pause_ads"]:
            lines.append("\n⏸ Don't spend on ads for these until back in stock: " + "; ".join(mk["pause_ads"]))
        msgs["Marketing"] = "\n".join(lines)
    else:
        msgs["Marketing"] = f"*Joy Marketing – {rd}*\nNo search-visibility issues detected this week."
    return msgs


# --------------------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------------------
FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _register_fonts() -> tuple[str, str, bool]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    try:
        if "Joy" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("Joy", str(FONT_DIR / "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("Joy-Bold", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        return "Joy", "Joy-Bold", True
    except Exception:  # font file missing: fall back to built-in Helvetica without the ₹ glyph
        return "Helvetica", "Helvetica-Bold", False


def build_pdf(res: EngineResult, backtest_summary: pd.DataFrame | None = None, clean_profile: pd.DataFrame | None = None) -> bytes:
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                    TableStyle)

    font, bold, has_rupee = _register_fonts()

    def R(text: str) -> str:  # rupee-safe text
        return text if has_rupee else text.replace("₹", "Rs ")

    ink, ink2, muted = colors.HexColor(config.INK["primary"]), colors.HexColor(config.INK["secondary"]), colors.HexColor(config.INK["muted"])
    grid = colors.HexColor(config.INK["grid"])
    st = {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=18, leading=22, textColor=ink),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=12.5, leading=16, textColor=ink, spaceBefore=10, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=bold, fontSize=10, leading=13, textColor=ink, spaceAfter=2),
        "body": ParagraphStyle("body", fontName=font, fontSize=8.8, leading=12, textColor=ink, alignment=TA_LEFT),
        "small": ParagraphStyle("small", fontName=font, fontSize=7.6, leading=10, textColor=ink2),
        "muted": ParagraphStyle("muted", fontName=font, fontSize=7.6, leading=10, textColor=muted),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=7.8, leading=10, textColor=ink),
        "cellb": ParagraphStyle("cellb", fontName=bold, fontSize=7.8, leading=10, textColor=ink),
    }
    P = lambda t, s="body": Paragraph(R(t), st[s])  # noqa: E731

    b = team_briefs(res)
    k = res.kpis
    rd = res.report_date
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=f"Joy QC Report {rd:%d %b %Y}", author="Joy Command Center")
    W = A4[0] - 32 * mm

    hdr = ParagraphStyle("hdr", fontName=bold, fontSize=7.2, leading=9, textColor=ink2)

    def table(data, widths, header=True, zebra=True):
        if header:  # wrap header text so long labels never overlap the next column
            data = [[Paragraph(R(c), hdr) if isinstance(c, str) else c for c in data[0]]] + list(data[1:])
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [("FONT", (0, 0), (-1, -1), font, 7.8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, 0), 0.6, ink2), ("TEXTCOLOR", (0, 0), (-1, 0), ink2),
                 ("FONT", (0, 0), (-1, 0), bold, 7.4), ("TOPPADDING", (0, 0), (-1, -1), 3),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEBELOW", (0, 1), (-1, -1), 0.25, grid)]
        t.setStyle(TableStyle(style))
        return t

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(font, 7)
        canvas.setFillColor(muted)
        canvas.drawString(16 * mm, 8 * mm, R(f"Joy Command Center · Monday {rd:%d %b %Y} · Revenue figures are estimates "
                                              f"unless labelled observed · page {doc_.page}"))
        canvas.restoreState()

    el = []
    el += [P(f"Joy Quick Commerce · Monday {rd:%d %B %Y}", "muted"), P("Weekly action report", "h1"),
           P(f"Data to Sunday {res.features.latest_week + pd.Timedelta(days=6):%d %b %Y} · Blinkit, Zepto, Instamart · "
             f"{k['stores']} dark stores, 6 cities, 15 SKUs", "muted"), Spacer(1, 6)]

    # KPI strip
    def kcell(label, value, sub):
        return [P(label, "muted"), Paragraph(R(value), ParagraphStyle("kv", fontName=bold, fontSize=13, leading=16, textColor=ink)),
                P(sub, "small")]
    kp = [kcell("GMV last week", inr(k["gmv"]), f"{pct(k['gmv_wow'], 1, True)} WoW · LFL {pct(k['lfl_wow'], 1, True)}"),
          kcell("Units sold", num(k["units"]), f"prev {num(k['units_prev'])}"),
          kcell("Availability", f"{k['avail']:.1f}%", f"prev {k['avail_prev']:.1f}%"),
          kcell("PO fill rate", pct(k["fill_rate"], 1), f"prev {pct(k['fill_rate_prev'], 1)}"),
          kcell("Weekly revenue at risk (est.)", inr(k["risk_point"]), f"range {inr_range(k['risk_low'], k['risk_high'])}"),
          kcell("Issues needing action", str(len(b['QC Manager']['priority'])),
                f"{k['n_critical']} critical · {k['n_high']} high")]
    kt = Table([kp[:3], kp[3:]], colWidths=[W / 3] * 3)
    kt.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.4, grid),
                            ("INNERGRID", (0, 0), (-1, -1), 0.4, grid), ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [kt, Spacer(1, 4)]
    if k.get("context_note"):
        el.append(P(f"<b>Context:</b> {k['context_note']}", "small"))

    # Priority table
    el.append(P("This week's priorities", "h2"))
    qc = b["QC Manager"]
    if qc["priority"]:
        rows = [["#", "Severity", "Issue", "Owner", "₹ at risk / week (est.)", "Act by"]]
        for p in qc["priority"]:
            rows.append([str(p["rank"]), Paragraph(f"<font color='{config.SEVERITY_COLORS[p['severity']]}'>"
                                                    f"{config.SEVERITY_ICONS[p['severity']]}</font> {p['severity']}", st["cell"]),
                         Paragraph(R(p["title"]), st["cell"]), p["team"], R(p["risk"]), p["deadline"]])
        el.append(table(rows, [7 * mm, 19 * mm, 76 * mm, 22 * mm, 27 * mm, W - 151 * mm]))
    else:
        el.append(P("No issues meet the bar for action this week."))

    # Weekly GMV chart
    sw = res.features.seg_week.groupby("week").agg(sales=("sales", "sum"), sd=("store_days", "sum")).reset_index()
    dr = Drawing(W, 120)
    bc = VerticalBarChart()
    bc.x, bc.y, bc.width, bc.height = 30, 18, W - 40, 85
    bc.data = [list((sw["sales"] / 1e5).round(2))]
    bc.categoryAxis.categoryNames = [w.strftime("%d %b") for w in sw["week"]]
    bc.categoryAxis.labels.fontName = font
    bc.categoryAxis.labels.fontSize = 6.5
    bc.valueAxis.labels.fontName = font
    bc.valueAxis.labels.fontSize = 6.5
    bc.valueAxis.valueMin = 0
    bc.valueAxis.gridStrokeColor = grid
    bc.valueAxis.visibleGrid = True
    bc.bars[0].fillColor = colors.HexColor(config.INK["accent"])
    bc.bars[0].strokeColor = None
    bc.barSpacing = 2
    dr.add(bc)
    dr.add(String(30, 110, R("Weekly GMV, ₹ lakh (all platforms) · store count grew from "
                             f"{res.features.sales.groupby('week')['dark_store_id'].nunique().iloc[0]} to {k['stores']}"),
                  fontName=font, fontSize=7.5, fillColor=ink2))
    el += [Spacer(1, 6), dr]

    # Incident cards
    el.append(P("Issue details", "h2"))
    for r in res.priority.itertuples():
        card = [P(f"<font color='{config.SEVERITY_COLORS[r.severity]}'>{config.SEVERITY_ICONS[r.severity]} {r.severity}</font> · "
                  f"#{r.priority} · {r.team} · {r.status} · {r.confidence} confidence", "muted"),
                P(r.title, "h3"), P(r.what_happened)]
        for e in r.evidence[:5]:
            card.append(P(f"• {e}", "small"))
        card.append(P(f"<b>Estimates:</b> observed sales {inr(r.observed_ref_weekly)}/week before → "
                      f"{inr(r.observed_latest)} last week. Lost to date {inr_range(r.lost_low, r.lost_high)}; "
                      f"at risk {inr_range(r.risk_low, r.risk_high)}/week; ~{inr(r.recoverable_next_week)} recoverable next week.", "small"))
        card.append(P(f"<b>Action:</b> {r.recommended_action}"))
        card.append(P(f"<i>Why this rank:</i> {r.why_ranked}", "muted"))
        card.append(Spacer(1, 8))
        el.append(KeepTogether(card))

    # Team sections
    el.append(PageBreak())
    el.append(P("Supply team", "h2"))
    if b["Supply"]:
        rows = [["Warehouse", "SKU / product", "Ordered", "Delivered", "Shortfall", "Fill", "Ship now"]]
        for it in b["Supply"]:
            for ln in it["lines"]:
                rows.append([f"{it['warehouse_id']}\n{it['platform']} {it['city']}",
                             Paragraph(R(f"{ln['sku_code']}<br/>{ln['product']}"), st["cell"]),
                             num(ln["ordered"]), num(ln["delivered"]), num(ln["shortfall"]),
                             pct(ln["fill_rate"]), num(ln["latest_shortfall"])])
        el.append(table(rows, [32 * mm, 54 * mm, 18 * mm, 18 * mm, 18 * mm, 14 * mm, W - 154 * mm]))
        for it in b["Supply"]:
            el.append(P(f"<b>{it['warehouse_id']} ({it['kind']}):</b> {it['action']} <i>Due {it['deadline']}.</i>", "small"))
    else:
        el.append(P("No supply actions this week."))

    el.append(P("Platform account managers", "h2"))
    if b["Platform KAM"]:
        rows = [["Platform / warehouse", "Product", "Signal", "Escalation"]]
        for it in b["Platform KAM"]:
            for ln in it["lines"]:
                if it["kind"] == "Ordering stopped":
                    sig = (f"No PO for {int(ln['missed_cycles'])} wk (last {d(ln['last_po'], '%d %b')}); availability now "
                           f"{ln['avail_now']:.0f}%; {ln['stores_hit']}/{ln['n_stores']} stores out")
                elif it["kind"] == "Ranking / listing check":
                    sig = f"Search rank #{ln['rank_before']:.0f} → #{ln['rank_now']:.0f}"
                else:
                    sig = f"Availability {ln['avail_ref']:.0f}% → {ln['avail_now']:.0f}%"
                rows.append([f"{it['platform']} {it['city']}\n{it['warehouse_id']}", Paragraph(R(ln["product"]), st["cell"]),
                             Paragraph(R(sig), st["cell"]), Paragraph(R(it["action"]), st["cell"])])
        el.append(table(rows, [30 * mm, 34 * mm, 44 * mm, W - 108 * mm]))
    else:
        el.append(P("No platform escalations this week."))

    el.append(P("Marketing", "h2"))
    mk = b["Marketing"]
    if mk["items"]:
        rows = [["Product / platform", "Search rank", "Availability", "Lost to date (est.)", "Intervention"]]
        for it in mk["items"]:
            ln = it["lines"][0]
            rows.append([Paragraph(R(f"{ln['product']}<br/>{it['platform']} {it['city']}"), st["cell"]),
                         f"#{ln['rank_before']:.0f} → #{ln['rank_now']:.0f}", f"{ln['avail_now']:.0f}% (healthy)",
                         R(it["lost_to_date"]), Paragraph(R(it["action"]), st["cell"])])
        el.append(table(rows, [36 * mm, 22 * mm, 24 * mm, 24 * mm, W - 106 * mm]))
    else:
        el.append(P("No search-visibility issues this week."))
    if mk["pause_ads"]:
        el.append(P("<b>Pause paid ads until back in stock:</b> " + "; ".join(mk["pause_ads"]), "small"))

    if qc["watchlist"]:
        el.append(P("Watchlist (not yet actionable)", "h2"))
        rows = [["Signal", "Owner", "Why not escalated"]]
        for w in qc["watchlist"]:
            rows.append([Paragraph(R(w["title"]), st["cell"]), w["team"], Paragraph(R(w["reason"]), st["cell"])])
        el.append(table(rows, [80 * mm, 24 * mm, W - 104 * mm]))

    # Back-test and resolved
    if backtest_summary is not None and len(backtest_summary):
        el.append(P("Would we have caught it? Back-test of the last 9 Mondays", "h2"))
        el.append(P("The same engine was replayed each Monday using only data available that day.", "small"))
        rows = [["Incident", "Started (week)", "First flagged", "Delay", "Peak severity"]]
        for r in backtest_summary.itertuples():
            rows.append([Paragraph(R(r.title), st["cell"]), d(r.start_week, "%d %b"), d(r.first_flagged, "%a %d %b"),
                         "first possible Monday" if r.detection_lag_weeks == 0 else f"+{r.detection_lag_weeks} wk",
                         r.peak_severity])
        el.append(table(rows, [86 * mm, 22 * mm, 24 * mm, 26 * mm, W - 158 * mm]))
    if qc["resolved"]:
        el.append(P("Resolved incidents in the data window (for the record)", "h2"))
        rows = [["Incident", "Owner", "Weeks", "Est. lost sales"]]
        for r in qc["resolved"]:
            rows.append([Paragraph(R(r["title"]), st["cell"]), r["team"], str(r["weeks"]), R(r["lost"])])
        el.append(table(rows, [104 * mm, 26 * mm, 14 * mm, W - 144 * mm]))

    # Method appendix
    el.append(P("How to read the numbers", "h2"))
    for t in [
        "<b>Observed</b> figures are straight from the exports. <b>Estimated</b> figures are model-based and shown as ranges.",
        "<b>Lost sales to date</b> = expected minus actual sales over the affected weeks. Expected = the SKU's pre-incident "
        "sales per store-day × store-days × the platform's like-for-like trend (unaffected SKUs only). The range comes from a "
        "second method: availability gap (stock issues) or the same SKU's trend in other cities (visibility issues).",
        "<b>Weekly revenue at risk</b> = next week's expected loss if nothing is done: last week's loss rate and the rate implied "
        "by the last 3 days of availability (PO stoppages also include full exposure). Resolved issues carry no forward risk.",
        "<b>Priority score</b> (0-100) = confidence × (40 impact + 25 urgency + 15 persistence + 20 breadth). "
        "Critical ≥ 70, High ≥ 50, Medium ≥ 30. Only Medium+ open issues reach the list.",
        "Alerts are judged against each SKU's own history <b>and</b> its platform peers, so festive discounts, new store "
        "openings and other network-wide shifts do not trigger them.",
    ]:
        el.append(P(t, "small"))
    if clean_profile is not None:
        el.append(P("Data processed: " + "; ".join(f"{r.dataset} {num(r.rows_in)} rows in, {num(r.rows_out)} used"
                                                   + (f" ({num(r.duplicates_removed)} duplicates removed)" if r.duplicates_removed else "")
                                                   for r in clean_profile.itertuples()), "muted"))

    doc.build(el, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
