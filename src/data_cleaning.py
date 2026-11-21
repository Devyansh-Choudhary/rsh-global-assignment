"""Validation and cleaning pipeline with a full audit trail.

Principles
----------
* Raw data is never modified in place; cleaning returns new frames.
* No row is dropped silently. Every removal or correction is recorded in the
  ``CleaningLog`` with the number of rows affected, the action and the reason.
* Rows that cannot be used (unparseable values, orphan keys) are moved to a
  quarantine table that the app exposes, rather than deleted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config
from src.data_loader import RawData


# --------------------------------------------------------------------------------------
# Audit log
# --------------------------------------------------------------------------------------
@dataclass
class CleaningLog:
    entries: list[dict] = field(default_factory=list)

    def add(self, dataset: str, check: str, rows: int, action: str, detail: str = "") -> None:
        self.entries.append(
            {"dataset": dataset, "check": check, "rows_affected": int(rows), "action": action, "detail": detail}
        )

    def to_frame(self) -> pd.DataFrame:
        cols = ["dataset", "check", "rows_affected", "action", "detail"]
        return pd.DataFrame(self.entries, columns=cols)


@dataclass
class CleanData:
    sales: pd.DataFrame
    stores: pd.DataFrame
    products: pd.DataFrame
    purchase_orders: pd.DataFrame
    log: CleaningLog
    quarantine: pd.DataFrame
    profile: pd.DataFrame  # per-dataset rows in / out / quarantined


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def _strip_all(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip leading/trailing whitespace from every text cell; return #rows changed."""
    out = df.copy()
    changed = pd.Series(False, index=df.index)
    for c in out.columns:
        stripped = out[c].astype(str).str.strip()
        changed |= stripped != out[c].astype(str)
        out[c] = stripped
    return out, int(changed.sum())


def normalise_platform(series: pd.Series) -> pd.Series:
    key = series.astype(str).str.strip().str.lower()
    return key.map(config.PLATFORM_ALIASES).fillna(series.astype(str).str.strip().str.title())


def normalise_city(series: pd.Series) -> pd.Series:
    key = series.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)
    return key.map(config.CITY_ALIASES).fillna(series.astype(str).str.strip().str.title())


def _quarantine(q: list[pd.DataFrame], dataset: str, rows: pd.DataFrame, reason: str) -> None:
    if len(rows):
        r = rows.copy()
        r.insert(0, "quarantine_reason", reason)
        r.insert(0, "dataset", dataset)
        q.append(r.astype(str))


def _to_num(df: pd.DataFrame, cols: list[str]) -> tuple[pd.DataFrame, pd.Series]:
    """Coerce columns to numbers; return frame and mask of rows with any failure."""
    out = df.copy()
    bad = pd.Series(False, index=df.index)
    for c in cols:
        num = pd.to_numeric(out[c].replace("", None), errors="coerce")
        bad |= num.isna()
        out[c] = num
    return out, bad


def _to_date(df: pd.DataFrame, cols: list[str], allow_blank: tuple[str, ...] = ()) -> tuple[pd.DataFrame, pd.Series]:
    out = df.copy()
    bad = pd.Series(False, index=df.index)
    for c in cols:
        raw = out[c].replace("", None)
        parsed = pd.to_datetime(raw, errors="coerce", format="mixed", dayfirst=False)
        if c in allow_blank:
            bad |= parsed.isna() & raw.notna()
        else:
            bad |= parsed.isna()
        out[c] = parsed
    return out, bad


# --------------------------------------------------------------------------------------
# Per-table cleaning
# --------------------------------------------------------------------------------------
def clean_products(raw: pd.DataFrame, log: CleaningLog, q: list) -> pd.DataFrame:
    df, n = _strip_all(raw)
    if n:
        log.add("products", "Whitespace", n, "Trimmed", "Leading/trailing spaces removed")
    df["platform"] = normalise_platform(df["platform"])
    df, bad = _to_num(df, ["mrp_inr"])
    _quarantine(q, "products", raw[bad], "Non-numeric MRP")
    if bad.sum():
        log.add("products", "Invalid MRP", bad.sum(), "Quarantined")
    df = df[~bad]
    dup = df.duplicated(["platform", "platform_product_id"], keep="first")
    if dup.sum():
        log.add("products", "Duplicate platform product ID", dup.sum(), "Kept first occurrence")
    df = df[~dup]
    multi = df.groupby(["platform", "sku_code"]).size()
    multi = multi[multi > 1]
    if len(multi):
        log.add("products", "SKU listed more than once on a platform", int(multi.sum()), "Kept (flagged)",
                ", ".join(f"{p}/{s}" for p, s in multi.index))
    log.add("products", "ID mapping integrity", 0, "Verified",
            f"{df['platform_product_id'].nunique()} platform IDs -> {df['sku_code'].nunique()} SKUs across "
            f"{df['platform'].nunique()} platforms; each platform ID maps to exactly one SKU")
    return df.reset_index(drop=True)


def clean_stores(raw: pd.DataFrame, log: CleaningLog, q: list) -> pd.DataFrame:
    df, n = _strip_all(raw)
    if n:
        log.add("dark_stores", "Whitespace", n, "Trimmed", "e.g. 'mumbai ' -> 'mumbai'")
    df["platform"] = normalise_platform(df["platform"])
    before = raw["city"].astype(str).str.strip()
    df["city_raw"] = raw["city"]
    df["city"] = normalise_city(df["city"])
    variants = (
        pd.DataFrame({"raw": raw["city"], "clean": df["city"]})
        .drop_duplicates()
        .query("raw != clean")
    )
    changed = int((before != df["city"]).sum())
    if changed:
        mapping = "; ".join(f"'{r}' -> {c}" for r, c in variants.itertuples(index=False))
        log.add("dark_stores", "Inconsistent city names", changed, "Standardised", mapping)
    unknown = set(df["city"]) - set(config.CITY_ALIASES.values())
    if unknown:
        log.add("dark_stores", "Unrecognised city", int(df["city"].isin(unknown).sum()), "Kept as-is",
                ", ".join(sorted(unknown)))
    df, bad = _to_date(df, ["opened_date"])
    if bad.sum():
        log.add("dark_stores", "Invalid opened_date", bad.sum(), "Kept; opened_date set to NaT")
    dup = df.duplicated("dark_store_id", keep="first")
    if dup.sum():
        _quarantine(q, "dark_stores", raw[dup], "Duplicate dark_store_id")
        log.add("dark_stores", "Duplicate dark_store_id", dup.sum(), "Quarantined later copies")
    df = df[~dup]
    # Warehouse must map to a single platform + city
    wh = df.groupby("warehouse_id").agg(n_city=("city", "nunique"), n_plat=("platform", "nunique"))
    bad_wh = wh[(wh.n_city > 1) | (wh.n_plat > 1)]
    if len(bad_wh):
        log.add("dark_stores", "Warehouse spans several cities/platforms", len(bad_wh), "Kept (flagged)",
                ", ".join(bad_wh.index))
    else:
        log.add("dark_stores", "Warehouse mapping integrity", 0, "Verified",
                f"{len(wh)} warehouses; each serves exactly one platform and one city")
    return df.reset_index(drop=True)


def clean_sales(raw: pd.DataFrame, products: pd.DataFrame, stores: pd.DataFrame,
                log: CleaningLog, q: list) -> pd.DataFrame:
    df, n = _strip_all(raw)
    if n:
        log.add("daily_sales", "Whitespace", n, "Trimmed")
    df["platform"] = normalise_platform(df["platform"])

    # 1. Exact duplicate rows (the same export row twice)
    dup_mask = df.duplicated(keep="first")
    if dup_mask.sum():
        dates = df.loc[dup_mask, "date"].value_counts()
        plats = df.loc[dup_mask, "platform"].value_counts()
        detail = (f"Dates: {', '.join(f'{d} ({c})' for d, c in dates.items())}; "
                  f"platforms: {', '.join(f'{p} ({c})' for p, c in plats.items())}")
        log.add("daily_sales", "Exact duplicate rows", dup_mask.sum(), "Removed (kept first copy)", detail)
    df = df[~dup_mask]

    # 2. Types
    df, bad_date = _to_date(df, ["date"])
    df, bad_num = _to_num(df, ["availability_pct", "units_sold", "selling_price_inr", "sales_inr", "search_rank"])
    bad = bad_date | bad_num
    _quarantine(q, "daily_sales", raw.loc[df.index[bad]], "Unparseable date or number")
    if bad.sum():
        log.add("daily_sales", "Unparseable values", bad.sum(), "Quarantined")
    else:
        log.add("daily_sales", "Type parsing", 0, "Verified", "All dates and numeric fields parsed")
    df = df[~bad]

    # 3. Range checks
    rng = (
        (df.availability_pct < 0) | (df.availability_pct > 100) | (df.units_sold < 0)
        | (df.selling_price_inr <= 0) | (df.search_rank < 1) | (df.sales_inr < 0)
    )
    _quarantine(q, "daily_sales", raw.loc[df.index[rng]], "Out-of-range value")
    log.add("daily_sales", "Value ranges (availability 0-100, units>=0, price>0, rank>=1)", rng.sum(),
            "Quarantined" if rng.sum() else "Verified")
    df = df[~rng]

    # 4. sales_inr consistency
    mism = (df.units_sold * df.selling_price_inr - df.sales_inr).abs() > 0.5
    if mism.sum():
        df.loc[mism, "sales_inr"] = df.loc[mism, "units_sold"] * df.loc[mism, "selling_price_inr"]
        log.add("daily_sales", "sales_inr != units x price", mism.sum(), "Recomputed from units x price")
    else:
        log.add("daily_sales", "sales_inr = units x price", 0, "Verified", "Holds on every row")

    sold_while_oos = (df.availability_pct == 0) & (df.units_sold > 0)
    log.add("daily_sales", "Units sold while 0% available", sold_while_oos.sum(),
            "Kept (flagged)" if sold_while_oos.sum() else "Verified")

    # 5. Conflicting duplicates on the natural key (same store/product/day, different values)
    key = ["date", "dark_store_id", "platform_product_id"]
    kdup = df.duplicated(key, keep="last")
    if kdup.sum():
        _quarantine(q, "daily_sales", raw.loc[df.index[kdup]], "Conflicting duplicate key (older copy)")
        log.add("daily_sales", "Conflicting duplicates on date/store/product", kdup.sum(),
                "Quarantined all but the last row")
    else:
        log.add("daily_sales", "Unique key date x store x product", 0, "Verified", "After removing exact duplicates")
    df = df[~kdup]

    # 6. Referential integrity
    pmap = products[["platform", "platform_product_id", "sku_code"]]
    m = df.merge(pmap, on=["platform", "platform_product_id"], how="left", validate="many_to_one")
    m.index = df.index
    orphan_p = m["sku_code"].isna()
    _quarantine(q, "daily_sales", raw.loc[df.index[orphan_p]], "Product ID not in products.csv")
    log.add("daily_sales", "Product ID -> sku_code mapping", orphan_p.sum(),
            "Quarantined unmapped" if orphan_p.sum() else "Verified", "Joined on (platform, platform_product_id)")
    df = m[~orphan_p]

    smap = stores[["dark_store_id", "platform", "city", "warehouse_id", "opened_date"]].rename(
        columns={"platform": "store_platform"})
    m = df.merge(smap, on="dark_store_id", how="left", validate="many_to_one")
    m.index = df.index
    orphan_s = m["warehouse_id"].isna()
    _quarantine(q, "daily_sales", raw.loc[df.index[orphan_s]], "Dark store not in dark_stores.csv")
    log.add("daily_sales", "Dark store -> warehouse mapping", orphan_s.sum(),
            "Quarantined unmapped" if orphan_s.sum() else "Verified")
    m = m[~orphan_s]
    plat_mis = m["store_platform"] != m["platform"]
    _quarantine(q, "daily_sales", raw.loc[m.index[plat_mis]], "Store belongs to a different platform")
    log.add("daily_sales", "Store platform matches row platform", plat_mis.sum(),
            "Quarantined" if plat_mis.sum() else "Verified")
    m = m[~plat_mis].drop(columns=["store_platform"])

    pre_open = m["opened_date"].notna() & (m["date"] < m["opened_date"])
    log.add("daily_sales", "Sales before store opening date", pre_open.sum(),
            "Kept (flagged)" if pre_open.sum() else "Verified")
    n_new = int((stores["opened_date"] >= m["date"].min()).sum())
    log.add("dark_stores", "Stores opened during the data window", n_new, "Kept; analysis uses per-store-day rates",
            "Store count grows over the 10 weeks, so raw totals overstate like-for-like growth")

    for c in ["units_sold", "search_rank"]:
        m[c] = m[c].astype(int)
    return m.reset_index(drop=True)


def clean_purchase_orders(raw: pd.DataFrame, stores: pd.DataFrame, products: pd.DataFrame,
                          log: CleaningLog, q: list) -> pd.DataFrame:
    df, n = _strip_all(raw)
    if n:
        log.add("purchase_orders", "Whitespace", n, "Trimmed")
    df["platform"] = normalise_platform(df["platform"])
    df, bad_d = _to_date(df, ["po_date", "delivery_date"], allow_blank=("delivery_date",))
    df, bad_n = _to_num(df, ["qty_ordered"])
    qd = pd.to_numeric(df["qty_delivered"].replace("", None), errors="coerce")
    bad_qd = qd.isna() & df["qty_delivered"].ne("")
    df["qty_delivered"] = qd
    bad = bad_d | bad_n | bad_qd
    _quarantine(q, "purchase_orders", raw.loc[df.index[bad]], "Unparseable date or quantity")
    log.add("purchase_orders", "Type parsing", bad.sum(), "Quarantined" if bad.sum() else "Verified")
    df = df[~bad]

    dup = df.duplicated("po_id", keep="first")
    if dup.sum():
        _quarantine(q, "purchase_orders", raw.loc[df.index[dup]], "Duplicate po_id")
        log.add("purchase_orders", "Duplicate po_id", dup.sum(), "Quarantined later copies")
    df = df[~dup]
    kdup = df.duplicated(["po_date", "warehouse_id", "sku_code"], keep=False)
    log.add("purchase_orders", "More than one PO per warehouse x SKU x week", kdup.sum(),
            "Kept; quantities summed per week" if kdup.sum() else "Verified")

    neg = (df.qty_ordered <= 0) | (df.qty_delivered < 0)
    _quarantine(q, "purchase_orders", raw.loc[df.index[neg]], "Non-positive ordered or negative delivered qty")
    log.add("purchase_orders", "Quantities positive", neg.sum(), "Quarantined" if neg.sum() else "Verified")
    df = df[~neg]

    over = df.qty_delivered > df.qty_ordered
    if over.sum():
        log.add("purchase_orders", "Delivered more than ordered", over.sum(), "Kept; fill rate capped at 100%")
    else:
        log.add("purchase_orders", "Delivered <= ordered", 0, "Verified")

    pending = df.delivery_date.isna() | df.qty_delivered.isna()
    log.add("purchase_orders", "No delivery recorded", pending.sum(),
            "Treated as pending, not as zero delivered" if pending.sum() else "Verified")

    lag = (df.delivery_date - df.po_date).dt.days
    bad_lag = lag.notna() & ((lag < 0) | (lag > 14))
    log.add("purchase_orders", "Delivery lag outside 0-14 days", bad_lag.sum(),
            "Kept (flagged)" if bad_lag.sum() else "Verified",
            f"Observed lags: {', '.join(f'{int(k)}d ({v})' for k, v in lag.value_counts().sort_index().items())}")
    not_monday = df.po_date.dt.weekday != 0
    log.add("purchase_orders", "PO not raised on a Monday", not_monday.sum(),
            "Kept; assigned to its Monday week" if not_monday.sum() else "Verified")

    known_wh = set(stores["warehouse_id"])
    orphan = ~df["warehouse_id"].isin(known_wh)
    _quarantine(q, "purchase_orders", raw.loc[df.index[orphan]], "Warehouse not in dark_stores.csv")
    log.add("purchase_orders", "Warehouse known", orphan.sum(), "Quarantined" if orphan.sum() else "Verified")
    df = df[~orphan]
    orphan_sku = ~df["sku_code"].isin(set(products["sku_code"]))
    _quarantine(q, "purchase_orders", raw.loc[df.index[orphan_sku]], "SKU not in products.csv")
    log.add("purchase_orders", "SKU known", orphan_sku.sum(), "Quarantined" if orphan_sku.sum() else "Verified")
    df = df[~orphan_sku]

    wh_city = stores.drop_duplicates("warehouse_id").set_index("warehouse_id")["city"]
    df["city"] = df["warehouse_id"].map(wh_city)
    df["lag_days"] = (df.delivery_date - df.po_date).dt.days
    df["qty_ordered"] = df["qty_ordered"].astype(int)
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def clean_all(raw: RawData) -> CleanData:
    log = CleaningLog()
    q: list[pd.DataFrame] = []
    for name, frame in [("daily_sales", raw.sales), ("dark_stores", raw.stores),
                        ("products", raw.products), ("purchase_orders", raw.purchase_orders)]:
        tokens = ["", "nan", "NaN", "NULL", "null", "None"]
        masks = {c: frame[c].astype(str).str.strip().isin(tokens).to_numpy(dtype=bool) for c in frame.columns}
        per_col = pd.Series({c: int(m.sum()) for c, m in masks.items()}, dtype=int)
        per_col = per_col[per_col > 0]
        any_blank = np.logical_or.reduce(list(masks.values())) if masks and len(frame) else np.zeros(len(frame), bool)
        log.add(name, "Missing / blank values", int(any_blank.sum()),
                "Handled per column (see below)" if len(per_col) else "Verified",
                ", ".join(f"{c}: {v}" for c, v in per_col.items()) or "No blank cells in any column")
    products = clean_products(raw.products, log, q)
    stores = clean_stores(raw.stores, log, q)
    sales = clean_sales(raw.sales, products, stores, log, q)
    pos = clean_purchase_orders(raw.purchase_orders, stores, products, log, q)
    quarantine = pd.concat(q, ignore_index=True) if q else pd.DataFrame(columns=["dataset", "quarantine_reason"])

    qcount = quarantine["dataset"].value_counts() if len(quarantine) else pd.Series(dtype=int)
    removed_dups = sum(e["rows_affected"] for e in log.entries
                       if e["check"] == "Exact duplicate rows" and e["dataset"] == "daily_sales")
    profile = pd.DataFrame([
        {"dataset": "daily_sales", "rows_in": len(raw.sales), "duplicates_removed": removed_dups,
         "quarantined": int(qcount.get("daily_sales", 0)), "rows_out": len(sales)},
        {"dataset": "dark_stores", "rows_in": len(raw.stores), "duplicates_removed": 0,
         "quarantined": int(qcount.get("dark_stores", 0)), "rows_out": len(stores)},
        {"dataset": "products", "rows_in": len(raw.products), "duplicates_removed": 0,
         "quarantined": int(qcount.get("products", 0)), "rows_out": len(products)},
        {"dataset": "purchase_orders", "rows_in": len(raw.purchase_orders), "duplicates_removed": 0,
         "quarantined": int(qcount.get("purchase_orders", 0)), "rows_out": len(pos)},
    ])
    return CleanData(sales=sales, stores=stores, products=products, purchase_orders=pos,
                     log=log, quarantine=quarantine, profile=profile)
