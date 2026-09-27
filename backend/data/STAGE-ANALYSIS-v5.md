# Detail-Analysis Page: Data Support — Stage-by-Stage (v5, corrected)

**Dataset:** `mplads_synthetic_v5.csv` — 19,567 rows, 85 MPs (all 36 states),
4,862 local authorities. This replaces `mplads_synthetic_v4.csv`.

**The core point this document proves:** for every project that is *not* a
seeded anomaly, its other fields (documents, photos, dates, money) actually
back up its stated status/progress — e.g. an 80%-complete project has ~80%
of money spent and a photo whose location and date line up. Only the rows
explicitly seeded as anomalies contradict themselves, and each one is
labelled with which contradiction it is. That's what makes the detail page's
"why is this flagged" reasoning meaningful instead of arbitrary.

## A bug this caught (fixed before delivering this file)
The first pass of v5 sampled `physical_progress_pct` and `expenditure`
independently for new rows. Result: **43% of non-flagged "In Progress"
projects** would have shown progress and spending that didn't agree (e.g.
"60% complete" next to "8% of funds spent") — exactly the inconsistency you
were pointing at. Fixed by sampling both fields **jointly** from the same
real v4 project per status, so they move together the way real projects do.
Verified below (Stage 2).

---

## Stage 1 — Implementing Agency risk profile
**Page fields:** Agency name, total works, high-risk count, flag rate.
**Source columns:** `executing_agency`, `is_seeded_anomaly`, `is_photo_forensic_anomaly`.

| Agency | Total works | Flagged (project) | Flagged (photo) | Flag rate |
|---|---|---|---|---|
| Zilla Parishad | 2,859 | 205 | 105 | 7.17% |
| State PHED | 2,674 | 188 | 94 | 7.03% |
| PWD | 2,580 | 177 | 73 | 6.86% |
| Municipal Corporation | 2,889 | 192 | 103 | 6.65% |
| District Rural Development Agency | 2,792 | 180 | 85 | 6.45% |
| Rural Engineering Services | 2,958 | 189 | 95 | 6.39% |
| Panchayati Raj Department | 2,815 | 173 | 101 | 6.15% |

Every agency sits in a tight 6.1–7.2% band — none is an outlier by
construction, so the network/agency-risk page won't show a fake "this
agency is uniquely corrupt" signal unless your own detection logic finds
one from patterns beyond this base rate (e.g. clustering of the same
anomaly type under one agency, which you'd compute at the app layer).

## Stage 2 — Progress % vs money spent (the bug above)
**Page fields:** "X% complete" badge next to expenditure/sanctioned amount bar.
**Source columns:** `physical_progress_pct`, `expenditure`, `sanctioned_amount`, `status`.

- Normal (non-flagged) "In Progress" rows (n=3,004): mean gap between
  spend-fraction and progress-fraction = **0.116**; correlation = **0.66**
  (v4 original baseline: 0.13 gap, 0.57 correlation — matches).
- Rows labelled `progress_expenditure_mismatch` (n=163): mean gap = **0.60**
  — a real, visible contradiction (e.g. "70% complete" but only 15% spent,
  or the reverse).
- **Rule for the page:** if `|expenditure/sanctioned_amount −
  physical_progress_pct/100| > 0.30` on an "In Progress" row, flag it. That
  threshold now genuinely separates the two populations instead of
  catching half of everything.

## Stage 3 — Time delay
**Page fields:** Expected vs actual completion, "delayed by N days" badge.
**Source columns:** `expected_completion_date`, `actual_completion_date`, `status`.

- Normal completed projects (n=12,868): median delay **12 days** late (i.e.
  basically on time); only **1.9%** organically run over 150 days late.
- Rows labelled `abnormal_delay` (n=163): delay ranges **159–727 days**,
  mean **293 days** — unambiguously separated from the normal population.

## Stage 4 — Sanction inflation, cost overrun, payment-before-sanction
**Page fields:** "Sanctioned amount vs BOQ estimate" and "spend vs sanctioned" ratios; payment date order.

- `sanction_inflation` (n=163): sanctioned amount averages **2.54×** the
  BOQ-estimated cost, vs. **1.02×** for normal projects (max normal ratio
  is 4.45x-ish organically only in rare unflagged cases — the sampled
  anomalies sit well past typical).
- `cost_overrun` (n=163): expenditure averages **1.28×** the sanctioned
  amount (i.e. spent more than was sanctioned).
- `payment_before_sanction` (n=163): `first_payment_date` predates
  `sanction_date` — a payment that happened before the work was even
  approved.

## Stage 5 — Duplicate work detection
**Page fields:** "Possible duplicate of [work_id]" cross-reference.
**Source columns:** `seeded_anomaly_type` (`duplicate_work` /
`duplicate_work_original`), `work_id`.

163 pairs (326 rows). Each `duplicate_work` row's `work_id` carries a
`-DUPxxxx` suffix and its cost is within ±8% of its `duplicate_work_original`
partner — e.g. `MPLADS/WES/2021/100042` (original, ₹19.5L sanctioned) ↔
`MPLADS/WES/2021/100042-DUP0000` (₹19.7L sanctioned, reworded description).
The page can find a duplicate's partner by stripping the `-DUPxxxx` suffix
from the work_id.

## Stage 6 — Photo / document / location forensics
**Page fields:** Photo thumbnail + "GPS matches claimed site" / "photo
predates sanction" / "duplicate photo" badges.
**Source columns:** `photo_available`, `photo_captured_at`, `photo_gps_lat/lon`
vs `latitude/longitude`, `photo_phash`, `photo_forensic_flag`.

- Normal photos (18,911 of 17,049 eligible... see note below): GPS sits
  **~139m** from the claimed site on average (realistic phone-GPS noise).
- `gps_mismatch` (164): GPS is **1,207 km** away on average (min 7 km) —
  clearly a different place.
- `backdated_photo` (164): capture date is **180–727 days before**
  `sanction_date` — physically impossible for a genuine progress photo.
- `reused_photo` (164, 82 pairs): two different `work_id`s share an
  identical `photo_phash`.
- `missing_exif` (164): `photo_captured_at`/`photo_gps_lat`/`photo_gps_lon`
  are all null — metadata stripped, itself a red flag.
- Only works with status `In Progress`/`Completed` get a photo record at
  all (17,049 of 19,567 rows) — a Recommended/Sanctioned/Rejected work
  has nothing to photograph yet, and the page should show "no photo yet"
  rather than a false mismatch for those.

## Stage 7 — Overall numbers for the page's summary tiles
- **Project-level anomalies:** 1,304 / 19,567 rows flagged (**6.66%**) —
  matches v4's 6.73% rate, so precision/recall figures you validate against
  v4 stay meaningful at this scale.
- **Photo-forensic anomalies:** 656 / 17,049 eligible rows (**3.85%**),
  vs. v4's 3.86%.
- 43 rows are flagged by *both* modules independently — realistic overlap
  (a genuinely bad project often trips more than one check), not a
  duplicated count.

## What this means for "new projects added" going forward
If your app later adds a brand-new project row through its own workflow
(not from this seed file), the same rule holds for it to look legitimate
on the detail page: its `physical_progress_pct` and `expenditure` should
move together (Stage 2's 0.30 rule), its dates should be chronological
(recommend → sanction → payment → expected/actual completion), and if it
has a photo, the photo's GPS should sit within roughly 100–200m of the
work's claimed `latitude`/`longitude` and be dated on/after `sanction_date`.
Any of those breaking is what should earn it a flag — not an ungrounded
model score.

## Files
- `mplads_synthetic_v5.csv` — corrected dataset (19,567 rows)
- This document
