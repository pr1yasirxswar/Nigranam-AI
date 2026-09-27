# Dataset Changelog — v4 → v5 (small-scale expansion for model verification)

## The gap this closes
v4 had 5,940 rows across 542 MPs (~11 works/MP) and 5,605 local authorities
(~1 work each) — too thin to verify the detection modules at a realistic
per-jurisdiction case volume. v5 expands a **subset of MPs** so every local
authority has **3–5 cases** and every MP has **200–250 cases**, keeping the
overall row count **under 20,000** as requested.

## Scope decision: subset of MPs, not all 542
200–250 works/MP × 3–5 works/local-authority implies ~50–80 local
authorities per MP. Applying that to all 542 real MPs would mean
~120,000+ rows — well over the 20k ceiling. So v5 covers a **random subset
of 85 MPs** (chosen to keep all 36 states represented at least once) rather
than every MP. This trades national coverage for a dataset that's actually
usable for iterating on/verifying detection models.

## How the expansion was done
For each of the 85 selected MPs:
- **Target size**: `Uniform(200, 250)` works per MP.
- **New local authorities invented per district**, same naming convention as
  v4 (`Village N`, `Ward N`, `Block N`, `Gram Panchayat N`, continuing each
  district's existing numbering). Existing local authorities for these MPs
  were topped up rather than left untouched.
- **Local authority size**: `Uniform(3, 5)` works each.
- **Field generation**: every non-identity field (category, agency, status,
  BOQ quantity/rate, cost ratios, date lags, GPS jitter) is bootstrap-sampled
  from v4's own empirical distributions, category- and status-conditioned.
  `allocated_amount` is copied from each MP's existing fixed fund total.
- **work_id**: new rows use `MPLADS/<STATE>/<YEAR>/<seq>`, sequence numbers
  offset by 100000 to avoid colliding with v4's originals.

## Anomaly re-seeding (both label sets recomputed, whole file)
Same methodology as `seed_anomalies.py` / `seed_photo_forensics.py`:
qualifying pool by threshold, exclusive one-label-per-row sampling, fixed
seed (42), target count scaled to the new row count.

**Project-level (7 types)** — target scaled to `50 × (N/5940)` ≈ 163/type:

| Type | Rows (v5) |
|---|---|
| `stalled_project` | 163 |
| `payment_before_sanction` | 163 |
| `sanction_inflation` | 163 |
| `cost_overrun` | 163 |
| `progress_expenditure_mismatch` | 163 |
| `abnormal_delay` | 163 |
| `duplicate_work` | 326 (163 new `-DUPxxxx` rows + 163 `duplicate_work_original`) |

**Total: 1,304 / 19,567 rows (6.66%)** — matches v4's 6.73%.

**Photo-forensic (4 types)** — target scaled to `50 × (N_eligible/5188)` ≈ 164/type:

| Type | Rows (v5) |
|---|---|
| `gps_mismatch` | 164 |
| `reused_photo` | 164 (82 pairs) |
| `backdated_photo` | 164 |
| `missing_exif` | 164 |

**Total: 656 / 17,057 eligible rows (3.85%)** — matches v4's 3.86%.

## Final numbers
- **Rows: 19,567** (963 original rows for the 85 selected MPs, kept
  byte-for-byte except re-run anomaly labels, + 18,441 new base rows +
  163 new `duplicate_work` rows)
- **MPs covered: 85** of 542 (all 36 states represented)
- **Works per MP**: mean 230, range 204–255 (target 200–250)
- **Works per local authority**: mean 4.0, range 3–7 (target 3–5; a few
  local authorities land at 6–7 where an existing one already had more
  works than the new target before topping up)
- **Columns: 39** — identical names/order to `mplads_synthetic_v4.csv`

## What did NOT change
- The 85 selected MPs' real names, states, constituencies, and
  `allocated_amount` are untouched.
- Their original v4 rows are field-level untouched — only
  `seeded_anomaly_type` / `is_seeded_anomaly` / photo-forensic columns were
  recomputed as part of the whole-file re-seed.
- Schema: no columns added, removed, or renamed.
- The other 457 MPs are simply **not present** in v5 — this is a subset
  file, not a shrunk version of the full MP list.

## Caveats
- New rows bootstrap v4's marginal distributions field-by-field
  (independently), so some cross-field correlations present in the
  original data are flattened in the new rows — fine for detection-model
  verification, not a demographic simulation.
- New local authorities are invented (no real ward/village data).
- MPs were chosen at random (seed 42), not by any significance criterion —
  say if you'd rather target specific states or MPs instead.

## Files delivered
- `mplads_synthetic_v5.csv` — the expanded dataset (~8.6 MB)
- This changelog
