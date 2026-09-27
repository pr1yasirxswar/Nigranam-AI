"""
Phase 7, step 1 (Implementation-Guide.md): "Re-run the 18-point dataset
validation checklist against the final pipeline."

NOTE ON PROVENANCE: the original 18-point checklist itself isn't in this
codebase -- Tech-Stack-Reference.md and DATASET-CHANGELOG.md both refer to
it ("An 18-point validation checklist runs after every dataset change")
but no script or itemized list of the 18 points ships with this delivery
(seed_anomalies.py, which produced v3, is also referenced but not present
here -- only seed_photo_forensics.py, which produced v4, is). Per Rules.md
("ask instead of guessing"), if the team has the original checklist/script
saved elsewhere, use that as the source of truth instead of this one.

Absent that file, this reconstructs 18 checks directly from the specific,
falsifiable claims DATASET-CHANGELOG.md and Tech-Stack-Reference.md already
make about mplads_synthetic_v4.csv -- nothing here is a new invented rule,
each check cites the line it verifies. Run from backend/:

    python -m scripts.validate_dataset
"""
import pandas as pd

CSV_PATH = "data/mplads_synthetic_v4.csv"

SEEDED_TYPES = {
    "stalled_project", "payment_before_sanction", "sanction_inflation",
    "cost_overrun", "progress_expenditure_mismatch", "abnormal_delay",
    "duplicate_work", "duplicate_work_original",
}
PHOTO_TYPES = {"gps_mismatch", "reused_photo", "backdated_photo", "missing_exif"}
PHOTO_ELIGIBLE_STATUSES = {"In Progress", "Completed"}

results = []  # (n, description, passed, detail)


def check(n, description, condition, detail=""):
    results.append((n, description, bool(condition), detail))


def main():
    df = pd.read_csv(CSV_PATH)

    # ---- v2->v3 seeded-anomaly checks (DATASET-CHANGELOG.md, top section) ----
    check(1, "Row count is 5,940 (5,890 + 50 appended duplicate_work rows)",
          len(df) == 5940, f"got {len(df)}")

    check(2, "seeded_anomaly_type only takes the 7 documented types + duplicate_work_original + none",
          set(df["seeded_anomaly_type"].dropna().unique()) <= (SEEDED_TYPES | {"none"}),
          f"got {sorted(df['seeded_anomaly_type'].unique())}")

    check(3, "is_seeded_anomaly (0/1) agrees with seeded_anomaly_type != 'none'",
          ((df["seeded_anomaly_type"] != "none") == (df["is_seeded_anomaly"] == 1)).all())

    check(4, "Each of the 6 sampled-from-organic types has exactly 50 labeled rows",
          all((df["seeded_anomaly_type"] == t).sum() == 50 for t in
              ["stalled_project", "payment_before_sanction", "sanction_inflation",
               "cost_overrun", "progress_expenditure_mismatch", "abnormal_delay"]),
          {t: int((df["seeded_anomaly_type"] == t).sum()) for t in
           ["stalled_project", "payment_before_sanction", "sanction_inflation",
            "cost_overrun", "progress_expenditure_mismatch", "abnormal_delay"]})

    check(5, "duplicate_work has 50 new rows + 50 traceable duplicate_work_original rows",
          (df["seeded_anomaly_type"] == "duplicate_work").sum() == 50
          and (df["seeded_anomaly_type"] == "duplicate_work_original").sum() == 50)

    check(6, "Total seeded anomalies = 400 (6.73% of 5,940)",
          int((df["seeded_anomaly_type"] != "none").sum()) == 400)

    check(7, "Every seeded row has exactly one label -- no row double-counted across types",
          df["seeded_anomaly_type"].notna().all() and (df["seeded_anomaly_type"] != "").all())

    check(8, "All 542 real Lok Sabha MPs represented",
          df["mp_name"].nunique() == 542, f"got {df['mp_name'].nunique()}")

    check(9, "All 36 states represented",
          df["state"].nunique() == 36, f"got {df['state'].nunique()}")

    check(10, "50 duplicate rows carry the '-DUPxxx' work_id suffix and work_id stays unique dataset-wide",
           df["work_id"].astype(str).str.contains("-DUP").sum() == 50
           and df["work_id"].nunique() == len(df))

    # ---- v3->v4 photo-forensics checks (DATASET-CHANGELOG.md, bottom section) ----
    check(11, "Row count still 5,940 after v4 (columns added, no rows added/removed)",
           len(df) == 5940)

    check(12, "Exactly 5,188 rows (In Progress/Completed) are photo-eligible",
           int(df["status"].isin(PHOTO_ELIGIBLE_STATUSES).sum()) == 5188,
           f"got {int(df['status'].isin(PHOTO_ELIGIBLE_STATUSES).sum())}")

    check(13, "photo_available == 1 exactly for the eligible rows, 0 for the rest",
           (df["photo_available"] == df["status"].isin(PHOTO_ELIGIBLE_STATUSES).astype(int)).all())

    check(14, "Ineligible rows (Recommended/Sanctioned/Rejected) have null photo fields, not fabricated ones",
           df.loc[~df["status"].isin(PHOTO_ELIGIBLE_STATUSES),
                  ["photo_captured_at", "photo_gps_lat", "photo_gps_lon", "photo_phash"]].isna().all().all())

    check(15, "photo_forensic_flag only takes the 4 documented types + none",
           set(df["photo_forensic_flag"].dropna().unique()) <= (PHOTO_TYPES | {"none"}),
           f"got {sorted(df['photo_forensic_flag'].dropna().unique())}")

    check(16, "is_photo_forensic_anomaly (0/1) agrees with photo_forensic_flag != 'none'",
           ((df["photo_forensic_flag"] != "none") == (df["is_photo_forensic_anomaly"] == 1)).all())

    check(17, "Each of the 4 photo-forensic types has exactly 50 labeled rows (reused_photo = 25 pairs)",
           all((df["photo_forensic_flag"] == t).sum() == 50 for t in PHOTO_TYPES),
           {t: int((df["photo_forensic_flag"] == t).sum()) for t in PHOTO_TYPES})

    check(18, "Total photo-forensic anomalies = 200 (3.86% of the 5,188 eligible rows)",
           int((df["photo_forensic_flag"] != "none").sum()) == 200)

    passed = sum(1 for _, _, ok, _ in results if ok)
    print(f"Dataset validation: {passed}/{len(results)} checks passed\n")
    for n, desc, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {n:2d}. {desc}"
        if not ok and detail:
            line += f"  -- {detail}"
        print(line)

    if passed < len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
