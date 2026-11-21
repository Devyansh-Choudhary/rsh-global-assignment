# Analysis notes: data audit and incident investigation

These are the findings that shaped the engine. Every number here was computed from the supplied CSVs.

## Phase 1: Data audit

### Schemas and relationships

```
products (platform, platform_product_id) ──► sku_code ◄── purchase_orders.sku_code
        ▲                                                       │
daily_sales (platform, platform_product_id)                     │ warehouse_id
daily_sales.dark_store_id ──► dark_stores.dark_store_id ──► warehouse_id
```

- `daily_sales`: one row per date × dark store × platform product. There are 186,285 rows and 70 days (Mon 7 Sep – Sun 15 Nov). Every store-day has exactly 15 products.
- `dark_stores`: 202 stores, unique `dark_store_id`. There are 18 warehouses, 6 per platform. **Each warehouse serves exactly one city on one platform**, so warehouse × SKU is equivalent to platform × city × SKU.
- `products`: 45 rows = 15 SKUs × 3 platforms. Each platform ID maps to exactly one SKU.
- `purchase_orders`: 2,694 PO lines. All are dated Mondays (10 weeks), with one line per warehouse × SKU × week. Each line carries its own `qty_delivered` and `delivery_date`, which is always 2, 3 or 4 days later. **So deliveries never need to be matched to orders by date.** A full grid would be 18 × 15 × 10 = 2,700 lines, which means 6 are missing.

### Quality findings

| Finding | Detail | Decision |
|---|---|---|
| Exact duplicate rows | 765, all Zepto, all dated 2 Oct (51 stores × 15 SKUs) | Drop, keep the first copy, log it |
| City spellings | 11 raw values: `Bengaluru`, `Bangalore`, `HYDERABAD`, `Hyderabad`, `mumbai ` (trailing space), `Mumbai`, `Delhi`, `New Delhi`, `Delhi NCR`, `Pune`, `Kolkata` | Map to 6 cities. The Delhi variants are one market: each platform has exactly one warehouse there |
| Product ID formats | Blinkit numeric (`86653`), Zepto `ZP69039X`, Instamart `IM062962` (leading zeros) | Read everything as text, so no ID loses a zero |
| Store openings | 51 stores opened during the window (first sales on their `opened_date`). The network grew from 151 to 202 stores | Use per-store-day rates. Raw totals show false growth |
| Pricing | Selling price is always below MRP. The discount steps from ~8% to ~15% on all platforms for the weeks of 19 Oct, 26 Oct and 2 Nov, then reverts | Festive promotion. Detection is normalised against platform peers |
| Search rank | Values are 1–8, plus 11–15 in 396 rows; nothing at 9–10 | The 11–15 rows are the Zepto Bengaluru incident |
| Verified clean | `sales_inr = units × price` on every row; availability 0–100; no units sold at 0% availability; no rows before store opening; delivered ≤ ordered; no blanks anywhere | Checks are logged even when they pass |

## Phase 2: Incident investigation

### 1. Blinkit Bengaluru under-delivery (Supply)

- **Fill rate** at `BLK-WH-01` for 6 SKUs (Neem FW 100/50, Papaya FW 100, Almond Lotion 200/400, Cocoa Lotion 200) was 45–59% on every PO from **21 Sep to 26 Oct**. Before that it was 90–99%, and after (2 and 9 Nov) it recovered to 90–99%.
- **Availability** in Bengaluru Blinkit stores for those SKUs fell from ~91% to 35–71% (weeks of 21 Sep – 26 Oct, mostly 40–55%), with 13–15 of 20 stores below 50%. It recovered to ~92% in the week of 9 Nov.
- Metric that reveals it: **PO-line fill rate**, available on the Monday after delivery. Detected **Mon 28 Sep**.

### 2. Zepto Bengaluru face wash rank drop (Marketing)

- `JY-FW-NEEM-100` is the best-selling face wash on Zepto Bengaluru: ₹82K in the 5 weeks before the drop, versus ₹50K for the next one.
- The daily average rank was ~3 until **Tue 13 Oct**, then 12.4–13.3 from **Wed 14 Oct**. It is still ~13 on 15 Nov, so **the incident is ongoing**.
- Availability stayed at 88–96%. Units per store-day fell from ~1.6 to ~0.7, about −60% relative to the platform.
- Metric: **weekly mean rank vs baseline**, with availability as the control. Detected **Mon 19 Oct**.

### 3. Instamart Hyderabad sunscreen PO stop (KAM)

- `IM-WH-16` raised **no PO** for `JY-SS-SPF50-50` or `JY-SS-MATTE-50` on **14 and 21 Sep**, while ordering all 13 other SKUs. Ordering resumed on 28 Sep.
- Availability fell from ~93% to 68–77% (week of 14 Sep), then to **3–5%** (week of 21 Sep), recovering by the week of 5 Oct.
- Metric: **missing PO line versus the warehouse's own regular ordering**. Detected **Mon 21 Sep**, the first Monday after the first missed PO. The data only begins on 7 Sep, so "regular" rests on one prior week. This is called out as a limitation.

### Findings not in the brief (live on 16 Nov)

- **Zepto Mumbai `ZEP-WH-08`** has raised no PO for `JY-LT-ALM-200` on 2 or 9 Nov. Its previous orders were rising (96 → 108 → 120 → 132 units). Daily availability went 91% (5 Nov) → 52% (Mon 9 Nov) → **0%** (13–15 Nov) across all 12 stores.
- **Blinkit Delhi NCR `BLK-WH-03`** was delivered 46% and 50% of the 9 Nov PO for `JY-CR-NIGHT-50` and `JY-LT-ALM-400`. Availability fell from ~92% to ~40% in the last 3 days, with 18 of 22 stores below 50%.

### What does *not* need a flag

- The network fell 10.8% week-on-week in the week of 9 Nov because the festive discount ended. This hits all platforms and cities alike, so it is context, not an incident.
- Single-week fill dips to 74–79% (6 occurrences across 4 warehouses) had no visible availability effect. They are scored Low and kept off the list.
- Two single-week, significant sales dips (Blinkit Hyderabad Papaya FW, Blinkit Mumbai SPF50) had normal stock and rank. They go on the watchlist until a second week confirms them.

### Data gaps

- There is no stock-on-hand data, so run-out timing is inferred from availability.
- There is no competitor, ad-spend or rating data, so a rank drop's cause can't be determined.
- Ten weeks of history means "regular ordering" and baselines are short. All of this is documented in the app and README.
