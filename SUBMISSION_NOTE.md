# Joy Command Center: submission note

**What I flag, and why.** Five issue types, each with one owner: a warehouse that stopped ordering a SKU (KAM),
under-delivery (Supply), stockouts with no supply cause (KAM), a rank drop while stock is fine (Marketing), and a
significant unexplained sales drop (QC Manager). Each SKU is judged against its own recent weeks *and* its platform,
so promotions don't trigger alerts, and thresholds come from normal weeks (fill never drops below 81% outside
incidents). Linked signals become one ranked incident. On 16 Nov that leaves three actions.

**What was messy.** 765 duplicate rows (removed); cities spelt 11 ways (mapped to 6); product IDs with leading zeros
(read as text); 51 stores opened mid-window (trends per store-day); a festive discount ending in the last week, with
network sales down 11% (detection is peer-normalised). Every check is logged, and bad rows are quarantined, not dropped.

**How I used AI, and why you can trust the result.** I used Claude Code (Anthropic) as my engineering team, so my
time went on product decisions and on proving the output right. I wrote the spec it built against: a decision tool,
not a dashboard; each problem owned by one team; rupees split into observed, estimated and recoverable, never counted
twice; no hard-coded answers; every number traceable to the data. I treated nothing as done until it was proven:

- **Replay test.** The tool re-runs every past Monday using only that day's data. All three known incidents are
  caught on the first possible Monday, and integration tests lock this in.
- **Hand checks in the raw CSVs.** BLK-WH-01's face-wash fill fell from 236/240 to 132/240 on 21 Sep. IM-WH-16
  raised 13 POs instead of 15 on 14 and 21 Sep, missing exactly its two sunscreens. In a Bengaluru Zepto store,
  rank went from 2 to 12 on 14 Oct with stock at 94–100%. The PDF, app and CSV agree on the top three issues.
- **Problems this caught.** A first sales detector fired 192 alerts and was rebuilt around a significance test.
  Charts on zoomed axes exaggerated small dips. A README line misdescribed the 16 Nov issues. All were fixed before
  I deployed the live app.
