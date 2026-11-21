"""Turn clean daily data into the weekly analytical tables the detectors use.

Grain decisions
---------------
* **Segment = warehouse x SKU.** Each platform warehouse serves exactly one city
  on one platform (verified in cleaning), so a segment is equivalently
  platform x city x SKU. This is the level at which POs are raised, which makes
  it the natural unit for joining supply, availability, rank and sales signals.
* **Weeks run Monday-Sunday** and are labelled by their Monday.
* **Per-store-day rates** are used for every comparison over time, because 51 new
  dark stores opened during the window; raw totals would show false growth.
* **As-of discipline.** Everything is computed "as of" a report Monday. Sales on or
  after that date are excluded, and a PO's delivered quantity is only known if
  its delivery date is before the report date. This lets the same engine replay
  any past Monday (used for the back-test) without look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
from src.data_cleaning import CleanData

SEG_KEYS = ["platform", "city", "warehouse_id", "sku_code"]


def week_start(dates: pd.Series) -> pd.Series:
    """Monday of the week containing each date."""
    d = pd.to_datetime(dates).dt.normalize()
    return d - pd.to_timedelta(d.dt.weekday, unit="D")


def safe_div(a, b):
    """Element-wise a / b returning NaN where b is 0 or missing (never inf)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where((b == 0) | np.isnan(b), np.nan, a / b)
    return out


@dataclass
class Features:
    report_date: pd.Timestamp
    latest_week: pd.Timestamp
    weeks: list[pd.Timestamp]
    sales: pd.DataFrame          # daily, enriched, as-of filtered
    store_week: pd.DataFrame     # store x SKU x week
    seg_week: pd.DataFrame       # warehouse x SKU x week
    po_week: pd.DataFrame        # warehouse x SKU x PO week (complete grid, missing POs explicit)
    platform_week: pd.DataFrame  # platform x week
    products: pd.DataFrame       # SKU master (one row per SKU)
    stores: pd.DataFrame


def resolve_report_date(clean: CleanData, report_date: str | pd.Timestamp | None) -> pd.Timestamp:
    """Return the Monday the report is for. Defaults to the Monday after the last data day."""
    if report_date is None:
        report_date = clean.sales["date"].max() + pd.Timedelta(days=1)
    rd = pd.Timestamp(report_date).normalize()
    if rd.weekday() != 0:  # snap forward to the next Monday
        rd = rd + pd.Timedelta(days=7 - rd.weekday())
    return rd


def build_features(clean: CleanData, report_date: str | pd.Timestamp | None = None) -> Features:
    rd = resolve_report_date(clean, report_date)
    latest_week = rd - pd.Timedelta(days=7)

    sku_master = (clean.products.sort_values("platform")
                  .drop_duplicates("sku_code")[["sku_code", "product_name", "category", "pack_size"]])
    mrp = clean.products[["platform", "sku_code", "mrp_inr"]]

    s = clean.sales[clean.sales["date"] < rd].copy()
    s["week"] = week_start(s["date"])
    s = s.merge(sku_master, on="sku_code", how="left", validate="many_to_one")
    s = s.merge(mrp, on=["platform", "sku_code"], how="left", validate="many_to_one")
    first_week = s["week"].min() if len(s) else latest_week
    weeks = list(pd.date_range(first_week, latest_week, freq="7D"))

    # ---- store x SKU x week
    sw = (s.groupby(SEG_KEYS + ["dark_store_id", "week"], observed=True)
          .agg(days=("date", "nunique"), avail=("availability_pct", "mean"), units=("units_sold", "sum"),
               sales=("sales_inr", "sum"), rank=("search_rank", "mean"))
          .reset_index())

    # ---- segment x week
    g = s.groupby(SEG_KEYS + ["week"], observed=True)
    seg = g.agg(store_days=("date", "size"), n_stores=("dark_store_id", "nunique"),
                avail=("availability_pct", "mean"), units=("units_sold", "sum"),
                sales=("sales_inr", "sum"), rank=("search_rank", "mean"),
                price=("selling_price_inr", "mean"), mrp=("mrp_inr", "first")).reset_index()
    oos = (sw[sw["avail"] < config.STORE_OOS_THRESHOLD]
           .groupby(SEG_KEYS + ["week"]).size().rename("stores_oos").reset_index())
    seg = seg.merge(oos, on=SEG_KEYS + ["week"], how="left")
    seg["stores_oos"] = seg["stores_oos"].fillna(0).astype(int)
    seg["units_psd"] = safe_div(seg["units"], seg["store_days"])
    seg["sales_psd"] = safe_div(seg["sales"], seg["store_days"])
    seg["discount_pct"] = (1 - safe_div(seg["price"], seg["mrp"])) * 100
    seg = seg.merge(sku_master, on="sku_code", how="left")

    # ---- platform x week (per store-day, for peer normalisation)
    pw = (s.groupby(["platform", "week"]).agg(store_days=("date", "size"), sales=("sales_inr", "sum"),
                                              units=("units_sold", "sum"), avail=("availability_pct", "mean"),
                                              n_stores=("dark_store_id", "nunique"))
          .reset_index())
    pw["sales_psd"] = safe_div(pw["sales"], pw["store_days"])

    # ---- PO grid
    po = clean.purchase_orders[clean.purchase_orders["po_date"] < rd].copy()
    po["week"] = week_start(po["po_date"])
    known = po["delivery_date"].notna() & (po["delivery_date"] < rd) & po["qty_delivered"].notna()
    po["delivered_known"] = np.where(known, po["qty_delivered"], np.nan)
    agg = (po.groupby(["platform", "warehouse_id", "sku_code", "week"])
           .agg(ordered=("qty_ordered", "sum"), delivered=("delivered_known", lambda x: x.sum(min_count=1)),
                n_po=("po_id", "nunique"), lag_days=("lag_days", "mean"),
                delivery_date=("delivery_date", "max"))
           .reset_index())
    pairs = po[["platform", "warehouse_id", "sku_code"]].drop_duplicates()
    grid = pairs.merge(pd.DataFrame({"week": weeks}), how="cross")
    pog = grid.merge(agg, on=["platform", "warehouse_id", "sku_code", "week"], how="left")
    pog["has_po"] = pog["n_po"].fillna(0) > 0
    pog["ordered"] = pog["ordered"].fillna(0)
    pog["fill_rate"] = np.clip(safe_div(pog["delivered"], pog["ordered"]), 0, 1)
    pog["shortfall_units"] = np.where(pog["delivered"].notna(), np.maximum(pog["ordered"] - pog["delivered"], 0), np.nan)
    wh_city = clean.stores.drop_duplicates("warehouse_id").set_index("warehouse_id")["city"]
    pog["city"] = pog["warehouse_id"].map(wh_city)
    pog = pog.merge(sku_master, on="sku_code", how="left")

    return Features(report_date=rd, latest_week=latest_week, weeks=weeks, sales=s, store_week=sw,
                    seg_week=seg, po_week=pog, platform_week=pw, products=sku_master, stores=clean.stores)


def sequential_baseline(values: np.ndarray, is_anomalous, n: int = config.BASELINE_WEEKS,
                        how: str = "median") -> tuple[np.ndarray, np.ndarray]:
    """Walk forward through a weekly series, testing each week against a baseline built
    from the previous ``n`` *healthy* weeks.

    Weeks that test anomalous are excluded from later baselines, so an incident that
    lasts six weeks never becomes "the new normal". Returns (baseline, flags).
    ``is_anomalous(value, baseline) -> bool`` is the detector-specific test.
    """
    vals = np.asarray(values, dtype=float)
    base = np.full(len(vals), np.nan)
    flags = np.zeros(len(vals), dtype=bool)
    hist: list[float] = []
    for i, v in enumerate(vals):
        if hist:
            window = hist[-n:]
            base[i] = float(np.median(window) if how == "median" else np.mean(window))
            if not np.isnan(v):
                flags[i] = bool(is_anomalous(v, base[i]))
        if not flags[i] and not np.isnan(v):
            hist.append(v)
    return base, flags


def runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Return [start, end] index pairs (inclusive) of consecutive True values."""
    out, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        if not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out
