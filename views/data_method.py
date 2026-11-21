"""Data quality and methodology."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import config
from components.metrics import kpi, kpi_row
from components.theme import page_header
from src.formatting import num
from views import state

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

def render() -> None:
    CLEAN = state.get().clean
    page_header("Data quality & methodology", "What we found in the exports, and how the engine works")
    p = CLEAN.profile
    kpi_row([kpi("Rows processed", num(p["rows_in"].sum()), "across 4 files"),
             kpi("Duplicates removed", num(p["duplicates_removed"].sum()), "exact duplicate sales rows"),
             kpi("Rows quarantined", num(p["quarantined"].sum()), "unusable, kept for review"),
             kpi("City spellings fixed", str(int(CLEAN.stores["city_raw"].str.strip().ne(CLEAN.stores["city"]).sum())),
                 f"{CLEAN.stores['city_raw'].nunique()} variants → {CLEAN.stores['city'].nunique()} cities")])
    tabs = st.tabs(["Cleaning log", "Row counts", "Quarantine", "Methodology", "Thresholds", "Limitations"])
    with tabs[0]:
        st.dataframe(CLEAN.log.to_frame(), hide_index=True, width="stretch")
    with tabs[1]:
        st.dataframe(p, hide_index=True, width="stretch")
    with tabs[2]:
        if len(CLEAN.quarantine):
            st.dataframe(CLEAN.quarantine, hide_index=True, width="stretch")
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
        st.dataframe(pd.DataFrame(th, columns=["Setting (config.py)", "Value"]).astype(str), hide_index=True, width="stretch")
        st.caption("Each value's justification from the data is documented next to it in config.py and in the README.")
    with tabs[5]:
        st.markdown(LIMITS_MD)
