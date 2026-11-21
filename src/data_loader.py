"""Load raw CSV exports exactly as delivered.

Everything is read as text so that nothing is silently coerced (e.g. product IDs
with leading zeros, numbers with stray spaces). Typing happens in data_cleaning,
where every coercion failure is counted and logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

import config

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "sales": [
        "date", "platform", "dark_store_id", "platform_product_id", "availability_pct",
        "units_sold", "selling_price_inr", "sales_inr", "search_rank",
    ],
    "stores": ["dark_store_id", "platform", "city", "pincode", "warehouse_id", "opened_date"],
    "products": [
        "platform", "platform_product_id", "sku_code", "product_name", "category",
        "pack_size", "mrp_inr",
    ],
    "purchase_orders": [
        "po_id", "po_date", "platform", "warehouse_id", "sku_code", "qty_ordered",
        "qty_delivered", "delivery_date",
    ],
}


class DataLoadError(Exception):
    """Raised when a required file or column is missing."""


@dataclass
class RawData:
    """Untouched source tables (all columns as strings)."""

    sales: pd.DataFrame
    stores: pd.DataFrame
    products: pd.DataFrame
    purchase_orders: pd.DataFrame

    def row_counts(self) -> dict[str, int]:
        return {name: len(getattr(self, name)) for name in REQUIRED_COLUMNS}


def _read(path: Path, name: str) -> pd.DataFrame:
    if not path.exists():
        raise DataLoadError(f"Missing input file for '{name}': {path}")
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    df.columns = [c.strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED_COLUMNS[name] if c not in df.columns]
    if missing:
        raise DataLoadError(f"{path.name} is missing required columns: {missing}")
    return df


def load_raw(data_dir: Path | str | None = None) -> RawData:
    """Read the four source CSVs from ``data_dir`` (defaults to config.DATA_DIR)."""
    data_dir = Path(data_dir) if data_dir else config.DATA_DIR
    frames = {name: _read(data_dir / fname, name) for name, fname in config.FILES.items()}
    return RawData(**frames)
