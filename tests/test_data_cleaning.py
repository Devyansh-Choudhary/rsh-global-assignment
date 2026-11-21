"""Cleaning, validation and mapping tests."""
from __future__ import annotations

import pandas as pd
import pytest

from src.data_cleaning import clean_all, normalise_city, normalise_platform
from src.data_loader import DataLoadError, load_raw
from tests.conftest import make_synthetic


def test_city_and_platform_normalisation():
    raw = pd.Series(["Bangalore", "bengaluru ", "HYDERABAD", "mumbai ", "New Delhi", "Delhi", "Delhi NCR", "Pune"])
    assert normalise_city(raw).tolist() == ["Bengaluru", "Bengaluru", "Hyderabad", "Mumbai", "Delhi NCR",
                                            "Delhi NCR", "Delhi NCR", "Pune"]
    assert normalise_platform(pd.Series(["blinkit", "Swiggy Instamart", " Zepto"])).tolist() == ["Blinkit", "Instamart", "Zepto"]


def test_duplicates_removed_and_logged(tmp_path):
    d = make_synthetic(tmp_path, duplicates=25)
    c = clean_all(load_raw(d))
    log = c.log.to_frame()
    row = log[log["check"] == "Exact duplicate rows"]
    assert row["rows_affected"].iloc[0] == 25
    assert not c.sales.duplicated(["date", "dark_store_id", "platform_product_id"]).any()


def test_row_counts_reconcile(real_clean):
    """Nothing is silently lost: rows_in = rows_out + duplicates + quarantined."""
    for r in real_clean.profile.itertuples():
        assert r.rows_in == r.rows_out + r.duplicates_removed + r.quarantined, r.dataset


def test_real_data_known_issues(real_clean):
    log = real_clean.log.to_frame().set_index("check")
    assert log.loc["Exact duplicate rows", "rows_affected"] == 765
    assert real_clean.stores["city"].nunique() == 6
    assert set(real_clean.stores["city"]) == {"Bengaluru", "Mumbai", "Delhi NCR", "Hyderabad", "Pune", "Kolkata"}
    assert len(real_clean.sales) == 186285 - 765


def test_product_id_mapping(real_clean):
    s = real_clean.sales
    assert s["sku_code"].notna().all()
    # each platform product ID maps to exactly one SKU, each SKU has one ID per platform
    m = s.groupby(["platform", "platform_product_id"])["sku_code"].nunique()
    assert (m == 1).all()
    assert s.groupby("platform")["sku_code"].nunique().eq(15).all()


def test_warehouse_mapping(real_clean):
    s = real_clean.sales
    assert s["warehouse_id"].notna().all()
    wh = real_clean.stores.groupby("warehouse_id").agg(c=("city", "nunique"), p=("platform", "nunique"))
    assert (wh["c"] == 1).all() and (wh["p"] == 1).all()
    # warehouse prefix agrees with platform
    prefix = {"BLK": "Blinkit", "ZEP": "Zepto", "IM": "Instamart"}
    assert (s["warehouse_id"].str.split("-").str[0].map(prefix) == s["platform"]).all()


def test_orphans_and_bad_values_are_quarantined_not_dropped(tmp_path):
    d = make_synthetic(tmp_path)
    sales = pd.read_csv(d / "daily_sales.csv", dtype=str)
    bad = sales.head(3).copy()
    bad.loc[bad.index[0], "platform_product_id"] = "UNKNOWN"
    bad.loc[bad.index[1], "date"] = "not-a-date"
    bad.loc[bad.index[2], "availability_pct"] = "140"
    bad.loc[bad.index[2], "date"] = "2026-03-30"  # unique key so it is not a duplicate
    pd.concat([sales, bad], ignore_index=True).to_csv(d / "daily_sales.csv", index=False)
    c = clean_all(load_raw(d))
    q = c.quarantine
    assert len(q[q["dataset"] == "daily_sales"]) == 3
    reasons = set(q["quarantine_reason"])
    assert {"Product ID not in products.csv", "Unparseable date or number", "Out-of-range value"} <= reasons
    prof = c.profile.set_index("dataset").loc["daily_sales"]
    assert prof.rows_in == prof.rows_out + prof.duplicates_removed + prof.quarantined


def test_sales_inr_recomputed_when_inconsistent(tmp_path):
    d = make_synthetic(tmp_path)
    sales = pd.read_csv(d / "daily_sales.csv")
    i = sales.index[sales["units_sold"] > 0][0]
    sales.loc[i, "sales_inr"] = 1
    sales.to_csv(d / "daily_sales.csv", index=False)
    c = clean_all(load_raw(d))
    assert (c.sales["sales_inr"] == c.sales["units_sold"] * c.sales["selling_price_inr"]).all()
    assert "sales_inr != units x price" in set(c.log.to_frame()["check"])


def test_missing_delivery_is_pending_not_zero(tmp_path):
    d = make_synthetic(tmp_path)
    po = pd.read_csv(d / "purchase_orders.csv")
    po.loc[0, ["qty_delivered", "delivery_date"]] = [None, None]
    po.to_csv(d / "purchase_orders.csv", index=False)
    c = clean_all(load_raw(d))
    assert c.purchase_orders["qty_delivered"].isna().sum() == 1
    from src.feature_engineering import build_features
    f = build_features(c, "2026-03-02")
    row = f.po_week[(f.po_week["warehouse_id"] == po.loc[0, "warehouse_id"]) & (f.po_week["sku_code"] == po.loc[0, "sku_code"])
                    & (f.po_week["week"] == pd.Timestamp(po.loc[0, "po_date"]))]
    assert row["has_po"].iloc[0] and pd.isna(row["fill_rate"].iloc[0])


def test_missing_file_and_column_raise(tmp_path):
    d = make_synthetic(tmp_path)
    (d / "products.csv").unlink()
    with pytest.raises(DataLoadError):
        load_raw(d)
    d2 = make_synthetic(tmp_path / "b")
    pd.read_csv(d2 / "dark_stores.csv").drop(columns=["warehouse_id"]).to_csv(d2 / "dark_stores.csv", index=False)
    with pytest.raises(DataLoadError):
        load_raw(d2)
