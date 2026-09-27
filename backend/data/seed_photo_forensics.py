"""
seed_photo_forensics.py
========================
Adds simulated photo/EXIF metadata + ground-truth labels for Module A
(photo/document forensics), on top of mplads_synthetic_v3.csv.

Why: Module A (EXIF extraction, GPS-vs-claimed-location check, perceptual
hashing for reused photos) has no photo data to run against at all in the
current dataset - there are no photo files, let alone metadata. Since this
is a synthetic dataset (no real image files exist), we simulate what EXIF
extraction WOULD produce: a capture timestamp, GPS coordinates read from
the photo, and a perceptual hash - which is exactly the input shape
photo_forensics.py needs, without requiring actual JPEG files.

Only works with status "In Progress" or "Completed" get a photo record -
a work that hasn't started yet has nothing to photograph. This matches
the actual field-work reality and avoids inventing photos for
Recommended/Sanctioned/Rejected works.

4 ground-truth anomaly types (separate from the 7 project-level ones -
a work can have both, since these are different signals/modules):
- gps_mismatch: photo's EXIF GPS is far from the work's claimed location
- reused_photo: identical perceptual hash appears on 2 different works
  (25 pairs = 50 rows) - simulates a contractor reusing an old photo
- backdated_photo: EXIF capture date is BEFORE the work was even
  sanctioned - impossible for a genuine progress photo, strong red flag
- missing_exif: GPS/timestamp stripped from the photo entirely - can't
  be verified either way, which is itself a treated as suspicious signal

Fixed random seed (42), reproducible.
"""
import pandas as pd
import numpy as np
import hashlib

SEED = 42
rng = np.random.default_rng(SEED)
PER_TYPE_TARGET = 50

df = pd.read_csv("/mnt/user-data/outputs/mplads_synthetic_v3.csv")

df["photo_available"] = 0
df["photo_captured_at"] = pd.NA
df["photo_gps_lat"] = np.nan
df["photo_gps_lon"] = np.nan
df["photo_phash"] = pd.NA
df["photo_forensic_flag"] = "none"
df["is_photo_forensic_anomaly"] = 0

eligible = df[df.status.isin(["In Progress", "Completed"])].copy()
sanction_dt = pd.to_datetime(df["sanction_date"])
expected_dt = pd.to_datetime(df["expected_completion_date"])
actual_dt = pd.to_datetime(df["actual_completion_date"])

def phash_for(work_id, salt=""):
    return hashlib.md5(f"{work_id}{salt}".encode()).hexdigest()[:16]

# ---- Baseline (normal) photo metadata for every eligible row ----
for i in eligible.index:
    start = sanction_dt.loc[i]
    end = actual_dt.loc[i] if df.at[i, "status"] == "Completed" and pd.notna(actual_dt.loc[i]) else expected_dt.loc[i]
    if pd.isna(start) or pd.isna(end) or end <= start:
        end = start + pd.Timedelta(days=180)
    span_days = max((end - start).days, 1)
    capture = start + pd.Timedelta(days=int(rng.integers(0, span_days + 1)))

    df.at[i, "photo_available"] = 1
    df.at[i, "photo_captured_at"] = capture.strftime("%Y-%m-%d")
    df.at[i, "photo_gps_lat"] = round(df.at[i, "latitude"] + rng.normal(0, 0.001), 6)   # ~100m GPS noise
    df.at[i, "photo_gps_lon"] = round(df.at[i, "longitude"] + rng.normal(0, 0.001), 6)
    df.at[i, "photo_phash"] = phash_for(df.at[i, "work_id"])

used_idx = set()

def pick_unused(pool_idx, k):
    pool = [i for i in pool_idx if i not in used_idx]
    k = min(k, len(pool))
    return rng.choice(pool, size=k, replace=False) if k > 0 else np.array([], dtype=int)

summary = {}

# ---- 1. GPS mismatch: photo GPS is from a totally different, far-away row ----
chosen = pick_unused(eligible.index.to_numpy(), PER_TYPE_TARGET)
donor_pool = eligible.index.difference(chosen)
donors = rng.choice(donor_pool, size=len(chosen), replace=False)
for i, donor in zip(chosen, donors):
    df.at[i, "photo_gps_lat"] = round(df.at[donor, "latitude"] + rng.normal(0, 0.001), 6)
    df.at[i, "photo_gps_lon"] = round(df.at[donor, "longitude"] + rng.normal(0, 0.001), 6)
    df.at[i, "photo_forensic_flag"] = "gps_mismatch"
    df.at[i, "is_photo_forensic_anomaly"] = 1
    used_idx.add(i)
summary["gps_mismatch"] = len(chosen)

# ---- 2. Reused photo: 25 pairs share an identical hash + close capture date ----
n_pairs = PER_TYPE_TARGET // 2
pool = [i for i in eligible.index.to_numpy() if i not in used_idx]
rng.shuffle(pool)
pair_count = 0
pi = 0
while pair_count < n_pairs and pi + 1 < len(pool):
    a, b = pool[pi], pool[pi + 1]
    pi += 2
    shared_hash = phash_for(df.at[a, "work_id"], salt="-shared")
    df.at[a, "photo_phash"] = shared_hash
    df.at[b, "photo_phash"] = shared_hash
    # same photo -> same capture date, within a couple days of each other
    base_capture = pd.to_datetime(df.at[a, "photo_captured_at"])
    df.at[b, "photo_captured_at"] = (base_capture + pd.Timedelta(days=int(rng.integers(-2, 3)))).strftime("%Y-%m-%d")
    for i in (a, b):
        df.at[i, "photo_forensic_flag"] = "reused_photo"
        df.at[i, "is_photo_forensic_anomaly"] = 1
        used_idx.add(i)
    pair_count += 1
summary["reused_photo"] = pair_count * 2

# ---- 3. Backdated photo: capture date before the work was even sanctioned ----
chosen = pick_unused(eligible.index.to_numpy(), PER_TYPE_TARGET)
for i in chosen:
    s = sanction_dt.loc[i]
    backdate = s - pd.Timedelta(days=int(rng.integers(180, 730)))  # 6-24 months before sanction
    df.at[i, "photo_captured_at"] = backdate.strftime("%Y-%m-%d")
    df.at[i, "photo_forensic_flag"] = "backdated_photo"
    df.at[i, "is_photo_forensic_anomaly"] = 1
    used_idx.add(i)
summary["backdated_photo"] = len(chosen)

# ---- 4. Missing EXIF: GPS + timestamp stripped, only the image (hash) remains ----
chosen = pick_unused(eligible.index.to_numpy(), PER_TYPE_TARGET)
for i in chosen:
    df.at[i, "photo_gps_lat"] = np.nan
    df.at[i, "photo_gps_lon"] = np.nan
    df.at[i, "photo_captured_at"] = pd.NA
    df.at[i, "photo_forensic_flag"] = "missing_exif"
    df.at[i, "is_photo_forensic_anomaly"] = 1
    used_idx.add(i)
summary["missing_exif"] = len(chosen)

out_path = "/mnt/user-data/outputs/mplads_synthetic_v4.csv"
df.to_csv(out_path, index=False)

print("=== PHOTO FORENSICS SEEDING SUMMARY ===")
for k, v in summary.items():
    print(f"  {k}: {v}")
print()
print("Eligible rows (In Progress/Completed):", len(eligible))
print("Total photo_forensic anomalies:", (df.is_photo_forensic_anomaly == 1).sum())
print("Anomaly rate among eligible:", round((df.is_photo_forensic_anomaly==1).sum()/len(eligible)*100, 2), "%")
print()
print(df["photo_forensic_flag"].value_counts())
