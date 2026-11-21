"""Central configuration for Joy Command Center.

Every threshold used by the detection and prioritisation engine lives here, with
the reason it was chosen. Business logic must import from this module rather
than hard-coding numbers, so that a manager can challenge a threshold in one place.
"""
from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

FILES = {
    "sales": "daily_sales.csv",
    "stores": "dark_stores.csv",
    "products": "products.csv",
    "purchase_orders": "purchase_orders.csv",
}

# Monday the report is produced for. Data up to (but excluding) this date is used.
DEFAULT_REPORT_DATE = "2026-11-16"

# --------------------------------------------------------------------------------------
# Reference data normalisation
# --------------------------------------------------------------------------------------
PLATFORM_ALIASES = {
    "blinkit": "Blinkit",
    "zepto": "Zepto",
    "instamart": "Instamart",
    "swiggy instamart": "Instamart",
    "swiggy": "Instamart",
}

# The brief says six cities. Exports spell them in 11 different ways.
# Delhi / New Delhi / Delhi NCR are treated as one market: each platform has exactly
# one warehouse there, which confirms they are the same operating city.
CITY_ALIASES = {
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "blr": "Bengaluru",
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "delhi": "Delhi NCR",
    "new delhi": "Delhi NCR",
    "delhi ncr": "Delhi NCR",
    "ncr": "Delhi NCR",
    "hyderabad": "Hyderabad",
    "pune": "Pune",
    "kolkata": "Kolkata",
    "calcutta": "Kolkata",
}

# --------------------------------------------------------------------------------------
# Detection thresholds (see README "Detection methodology" for the data behind them)
# --------------------------------------------------------------------------------------
# Weeks of history used as the "normal" baseline before a week being tested.
BASELINE_WEEKS = 4

# Supply shortfall. Outside the incidents, fill rates run from 81% to 100% (median 97%,
# 1st percentile 86%); 98.4% of all PO lines are >= 80%. 80% is therefore outside normal
# delivery variance. Below 60% is treated as a severe shortfall.
FILL_RATE_THRESHOLD = 0.80
SEVERE_FILL_RATE = 0.60

# PO inactivity. A (warehouse, SKU) is a "regular buyer" if it raised a PO in every
# observed week before the gap. We require at least this many prior PO weeks.
# NOTE: the data only starts on 7 Sep, so the minimum is 1; with a longer history
# this should be raised to 3-4 weeks.
MIN_REGULAR_PO_WEEKS = 1
# The rest of the warehouse must have ordered in the missed week (otherwise it is a
# warehouse-wide pause / export gap, not a SKU-specific stoppage).
MIN_WAREHOUSE_ACTIVITY_SHARE = 0.8
# Demand check: if the SKU's sales per store-day had already fallen by more than
# this vs. its own baseline before the gap, the stoppage may be demand-driven.
DEMAND_DROP_FOR_EXPLAINED_GAP = 0.5

# Availability. Outside incidents, warehouse x SKU weekly availability has a median of
# 91.7%, 5th percentile 87.7% and minimum 82.7%. A drop of 15 points AND a level below
# 75% never occurs in normal weeks.
AVAIL_DROP_POINTS = 15.0
AVAIL_FLOOR = 75.0
# A dark store is "out of stock" for a product-week if its mean availability is below this.
STORE_OOS_THRESHOLD = 50.0
# Availability is "stable" (for search-rank attribution) if within this many points of baseline.
AVAIL_STABLE_POINTS = 10.0

# Search rank. Outside incidents, a segment's weekly mean rank never moves more than
# 0.7 positions week-on-week (99th percentile 0.4). 3+ positions is a real change.
RANK_DROP_POSITIONS = 3.0

# Sales. A segment's sales per store-day is compared to its own baseline AND to the
# rest of the platform (to remove promotions, festive peaks and new-store effects).
SALES_DROP_RELATIVE = 0.30
# ...and the drop must be statistically significant: z <= -3 (Poisson count noise +
# baseline uncertainty). Typical segment sells ~30 units/week, so +/-30% swings are normal noise.
SALES_Z_THRESHOLD = -3.0
# Ignore segments selling less than this per week at baseline (noise / immaterial).
MIN_WEEKLY_SALES_INR = 1500.0

# Consequence window: a supply problem in PO week W can show up in availability up to
# this many weeks later (deliveries take 2-4 days, then dark stores run down stock).
CONSEQUENCE_LAG_WEEKS = 2

# --------------------------------------------------------------------------------------
# Revenue impact
# --------------------------------------------------------------------------------------
# Share of next week's at-risk revenue that can realistically be recovered if the
# owning team acts on Monday, given the lead time of the fix. Explicit assumptions:
#   - supply top-up ships Mon, lands Wed/Thu (2-4 day delivery): ~4/7 of the week recoverable
#   - PO inactivity: KAM escalation + platform raises PO (+1 day) + 2-4 day delivery: ~3/7
#   - search visibility: ads / listing fixes can go live within ~1 day: ~6/7
#   - unexplained availability / sales: investigation first, ~2/7
RECOVERY_FRACTION = {
    "PO_INACTIVITY": 3 / 7,
    "SUPPLY_SHORTFALL": 4 / 7,
    "AVAILABILITY_DROP": 2 / 7,
    "SEARCH_RANK_DROP": 6 / 7,
    "SALES_DECLINE": 2 / 7,
}

# --------------------------------------------------------------------------------------
# Prioritisation
# --------------------------------------------------------------------------------------
# Score = (impact + urgency + persistence + breadth) x confidence multiplier, 0-100.
SCORE_WEIGHTS = {"impact": 40, "urgency": 25, "persistence": 15, "breadth": 20}
# Weekly revenue-at-risk that earns full impact points, as a share of the latest
# week's network GMV. 1% of GMV/week for one issue is a serious problem for a brand.
IMPACT_CAP_SHARE_OF_GMV = 0.01
# Persistence earns full points at this many weeks.
PERSISTENCE_FULL_WEEKS = 4
CONFIDENCE_MULTIPLIER = {"High": 1.0, "Medium": 0.85, "Low": 0.65}
URGENCY_POINTS = {"Active - worsening": 1.0, "Active": 0.8, "Recovering": 0.3, "Resolved": 0.0}
SEVERITY_BANDS = [(70, "Critical"), (50, "High"), (30, "Medium"), (0, "Low")]
# Max number of issues on the Monday priority list.
MAX_PRIORITY_ISSUES = 10

# Root-cause precedence: when several detectors fire on the same warehouse x SKU,
# the most upstream cause owns the incident and the rest become supporting evidence.
ROOT_CAUSE_PRECEDENCE = [
    "PO_INACTIVITY",
    "SUPPLY_SHORTFALL",
    "AVAILABILITY_DROP",
    "SEARCH_RANK_DROP",
    "SALES_DECLINE",
]

ISSUE_LABELS = {
    "PO_INACTIVITY": "Platform stopped ordering",
    "SUPPLY_SHORTFALL": "Under-delivery to warehouse",
    "AVAILABILITY_DROP": "Dark-store stockouts (cause unclear)",
    "SEARCH_RANK_DROP": "Search visibility loss",
    "SALES_DECLINE": "Unexplained sales decline",
}

OWNER_TEAM = {
    "PO_INACTIVITY": "Platform KAM",
    "SUPPLY_SHORTFALL": "Supply",
    "AVAILABILITY_DROP": "Platform KAM",
    "SEARCH_RANK_DROP": "Marketing",
    "SALES_DECLINE": "QC Manager",
}

TEAMS = ["QC Manager", "Supply", "Marketing", "Platform KAM"]

# --------------------------------------------------------------------------------------
# Visual system (validated categorical slots 1-3 + reserved status palette)
# --------------------------------------------------------------------------------------
PLATFORM_COLORS = {"Blinkit": "#2a78d6", "Zepto": "#eb6834", "Instamart": "#1baf7a"}
SEVERITY_COLORS = {
    "Critical": "#d03b3b",
    "High": "#ec835a",
    "Medium": "#fab219",
    "Low": "#898781",
}
SEVERITY_ICONS = {"Critical": "●", "High": "▲", "Medium": "■", "Low": "○"}
INK = {
    "primary": "#0b0b0b",
    "secondary": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "surface": "#fcfcfb",
    "page": "#f9f9f7",
    "good": "#006300",
    "bad": "#b42318",
    "accent": "#2a78d6",
}
