# Joy Command Center: submission note

**What I flag, and why.** I flag five issue types, each with one owner:
- a platform warehouse that stopped ordering a SKU (KAM)
- under-delivery against a PO (Supply)
- stockouts with no supply cause (KAM)
- a search-rank drop while stock is fine (Marketing)
- a statistically significant unexplained sales drop (QC Manager)

Each SKU is compared with its own recent weeks *and* its platform, so promotions don't trigger alerts. Thresholds come from normal weeks: fill never drops below 81%, availability never below 82%, and rank
never moves more than 0.7 positions in a week. Linked signals become one incident, ranked by rupees at risk,
urgency, duration, breadth and confidence. For 16 Nov that leaves three actions, and replaying
every Monday flags all three historical incidents on the first possible Monday.

**What was messy.**
- 765 duplicate rows: removed.
- Cities spelt 11 ways: mapped to 6.
- Platform-specific product IDs, some with leading zeros: read as text.
- 51 stores opened mid-window: all trends are per store-day.
- A festive discount ended in the last week (network −11%): detection is peer-normalised.

Every check is logged; bad rows are quarantined, not dropped.

**AI tools.** Claude Code wrote the pipeline, app, reports and 44 tests, and ran them. Testing exposed a noisy
first sales detector (192 alerts), which it redesigned. Nothing is hard-coded: generic rules find the incidents.

**What I checked myself, in the raw CSVs.**
- BLK-WH-01's Neem Face Wash 100ml fill dropped from 236/240 to 132/240 on 21 Sep.
- IM-WH-16 raised 13 POs, not 15, on 14 and 21 Sep, and the missing two are its sunscreens.
- In a Bengaluru Zepto store, the face wash went from rank 2 (13 Oct) to 12 (14 Oct) with stock at 94–100%.
- 2 Oct has 765 extra rows.
- The PDF's top three issues match the app and the CSV.
