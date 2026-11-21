"""Shared fixtures: a small seeded synthetic dataset with known injected incidents,
and the real bundled data (loaded once per test session)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_cleaning import clean_all  # noqa: E402
from src.data_loader import load_raw  # noqa: E402
from src.engine import load_clean, run_engine  # noqa: E402

START = pd.Timestamp("2026-01-05")  # a Monday
N_WEEKS = 8
SKUS = [("JY-A", "Joy A Face Wash", "Face Wash", 100), ("JY-B", "Joy B Cream", "Cream", 200),
        ("JY-C", "Joy C Lotion", "Lotion", 150), ("JY-D", "Joy D Serum", "Serum", 300)]
WAREHOUSES = {"BLK-WH-01": ("Blinkit", "Bengaluru"), "BLK-WH-02": ("Blinkit", "Mumbai"),
              "ZEP-WH-01": ("Zepto", "bangalore ")}  # messy city spelling on purpose


def make_synthetic(tmp: Path, *, shortfall: bool = True, po_gap: bool = True, rank_drop: bool = True,
                   duplicates: int = 0, seed: int = 7) -> Path:
    """Write four CSVs to ``tmp``. Injected incidents (all from week 5, index 4):

    * shortfall: BLK-WH-01 / JY-A delivered 50%, availability falls to ~40%
    * po_gap:    BLK-WH-02 / JY-B no PO in weeks 5-6, availability falls to ~20% in week 6+
    * rank_drop: ZEP-WH-01 / JY-C rank 3 -> 12, availability unchanged, sales -60%
    """
    rng = np.random.default_rng(seed)
    products, stores, sales, pos = [], [], [], []
    for plat in ["Blinkit", "Zepto"]:
        for i, (sku, name, cat, mrp) in enumerate(SKUS):
            pid = f"{1000 + i}" if plat == "Blinkit" else f"ZP{i:03d}X"
            products.append({"platform": plat, "platform_product_id": pid, "sku_code": sku, "product_name": name,
                             "category": cat, "pack_size": "50ml", "mrp_inr": mrp})
    pmap = {(p["platform"], p["sku_code"]): p["platform_product_id"] for p in products}
    for wh, (plat, city) in WAREHOUSES.items():
        for s in range(6):
            stores.append({"dark_store_id": f"{wh[:3]}-{wh[-2:]}{s}", "platform": plat, "city": city,
                           "pincode": "560001", "warehouse_id": wh, "opened_date": "2025-01-01"})
    days = pd.date_range(START, periods=7 * N_WEEKS)
    for st in stores:
        wh, plat = st["warehouse_id"], st["platform"]
        for sku, _, _, mrp in SKUS:
            for day in days:
                wk = (day - START).days // 7
                avail, rank, lam = 95.0, 3, 6.0
                if shortfall and wh == "BLK-WH-01" and sku == "JY-A" and wk >= 4:
                    avail = 40.0
                if po_gap and wh == "BLK-WH-02" and sku == "JY-B" and wk >= 5:
                    avail = 20.0
                if rank_drop and wh == "ZEP-WH-01" and sku == "JY-C" and wk >= 4:
                    rank, lam = 12, 2.4
                a = float(np.clip(avail + rng.normal(0, 2), 0, 100))
                u = int(rng.poisson(lam * a / 100))
                price = int(mrp * 0.9)
                sales.append({"date": day.strftime("%Y-%m-%d"), "platform": plat, "dark_store_id": st["dark_store_id"],
                              "platform_product_id": pmap[(plat, sku)], "availability_pct": round(a),
                              "units_sold": u, "selling_price_inr": price, "sales_inr": u * price, "search_rank": rank})
    n = 0
    for wh, (plat, _) in WAREHOUSES.items():
        for sku, *_ in SKUS:
            for wk in range(N_WEEKS):
                if po_gap and wh == "BLK-WH-02" and sku == "JY-B" and wk in (4, 5):
                    continue
                po_date = START + pd.Timedelta(weeks=wk)
                ordered = 100
                delivered = 50 if (shortfall and wh == "BLK-WH-01" and sku == "JY-A" and wk >= 4) else 97
                n += 1
                pos.append({"po_id": f"PO{n:05d}", "po_date": po_date.strftime("%Y-%m-%d"), "platform": plat,
                            "warehouse_id": wh, "sku_code": sku, "qty_ordered": ordered, "qty_delivered": delivered,
                            "delivery_date": (po_date + pd.Timedelta(days=3)).strftime("%Y-%m-%d")})
    sdf = pd.DataFrame(sales)
    if duplicates:
        sdf = pd.concat([sdf, sdf.head(duplicates)], ignore_index=True)
    tmp.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(products).to_csv(tmp / "products.csv", index=False)
    pd.DataFrame(stores).to_csv(tmp / "dark_stores.csv", index=False)
    sdf.to_csv(tmp / "daily_sales.csv", index=False)
    pd.DataFrame(pos).to_csv(tmp / "purchase_orders.csv", index=False)
    return tmp


SYN_REPORT = (START + pd.Timedelta(weeks=N_WEEKS)).strftime("%Y-%m-%d")


@pytest.fixture(scope="session")
def synthetic_dir(tmp_path_factory) -> Path:
    return make_synthetic(tmp_path_factory.mktemp("syn"))


@pytest.fixture(scope="session")
def synthetic_clean(synthetic_dir):
    return clean_all(load_raw(synthetic_dir))


@pytest.fixture(scope="session")
def synthetic_result(synthetic_clean):
    return run_engine(synthetic_clean, SYN_REPORT)


@pytest.fixture(scope="session")
def real_clean():
    return load_clean()


@pytest.fixture(scope="session")
def real_result(real_clean):
    return run_engine(real_clean, "2026-11-16")
