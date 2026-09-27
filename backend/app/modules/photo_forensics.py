"""
Phase 6 -- Module A: photo/document forensics.
EXIF extraction, GPS-vs-claimed-location distance check, perceptual hashing.
Dataset ground truth ready: photo_gps_lat/lon, photo_phash, photo_captured_at,
photo_forensic_flag, is_photo_forensic_anomaly (see mplads_synthetic_v4.csv,
DATASET-CHANGELOG.md).

Interface: score_photo_forensics(work: dict, all_works: pd.DataFrame) -> float in [0, 1].
Same pattern as duplicate.py/delay.py -- max across the 4 sub-checks below,
not an average, so this module's own output slots into risk_aggregator.py's
max-tier logic without dilution (Rules.md, "same interface pattern").
"""
from math import radians, sin, cos, sqrt, atan2

import numpy as np
import pandas as pd

_phash_index_cache = {}  # id(all_works) -> {phash: [row positions]}

# DATASET-CHANGELOG.md v4: baseline (non-anomalous) photos carry ~100m of
# simulated GPS noise (normal(0, 0.001) degrees). gps_mismatch swaps in a
# real coordinate from an unrelated work, verified hundreds-to-1000+km away.
# A wide gap between "plausible noise" and "verified anomaly range" means
# the exact cutoff isn't sensitive -- 5km is generous headroom over 100m.
GPS_MISMATCH_KM_FLOOR = 5.0
GPS_MISMATCH_KM_CEILING = 100.0  # score saturates at 1.0 by here, well short of the ~100s-1000s km seeded cases

# reused_photo pairs share a capture date within +/-2 days (DATASET-CHANGELOG.md).
# Widened slightly to +/-3 days so the check isn't brittle to off-by-one date math.
REUSED_PHOTO_DATE_WINDOW_DAYS = 3

# backdated_photo: capture date 6-24 months before sanction_date. Any capture
# strictly before sanction_date is already a temporal impossibility (Rules.md
# calls this out explicitly), so the score ramps in from day 1 rather than
# waiting for the 6-month mark -- 6mo+ just saturates it.
BACKDATED_SATURATION_DAYS = 180


def _haversine_km(lat1, lon1, lat2, lon2):
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * atan2(sqrt(a), sqrt(1 - a))


def _get_phash_index(all_works: pd.DataFrame):
    """work_id -> row position isn't unique enough for the reused-photo check
    (need every OTHER row sharing a phash); cached per DataFrame instance
    same as duplicate.py's embedding cache -- avoids an O(n) scan per work."""
    key = id(all_works)
    if key in _phash_index_cache:
        return _phash_index_cache[key]

    index = {}
    phashes = all_works["photo_phash"]
    for pos, phash in enumerate(phashes.tolist()):
        if phash is None or (isinstance(phash, float) and pd.isna(phash)):
            continue
        index.setdefault(phash, []).append(pos)

    _phash_index_cache[key] = index
    return index


def _gps_mismatch_score(work: dict) -> float:
    dist_km = _haversine_km(
        work.get("photo_gps_lat"), work.get("photo_gps_lon"),
        work.get("latitude"), work.get("longitude"),
    )
    if dist_km is None:
        return 0.0
    if dist_km <= GPS_MISMATCH_KM_FLOOR:
        return 0.0
    span = GPS_MISMATCH_KM_CEILING - GPS_MISMATCH_KM_FLOOR
    return float(np.clip((dist_km - GPS_MISMATCH_KM_FLOOR) / span, 0.0, 1.0))


def _backdated_photo_score(work: dict) -> float:
    captured = work.get("photo_captured_at")
    sanctioned = work.get("sanction_date")
    if pd.isna(captured) or pd.isna(sanctioned):
        return 0.0
    days_before = (pd.to_datetime(sanctioned) - pd.to_datetime(captured)).days
    if days_before <= 0:
        return 0.0  # captured on/after sanction -- not backdated
    return float(np.clip(days_before / BACKDATED_SATURATION_DAYS, 0.0, 1.0))


def _missing_exif_score(work: dict) -> float:
    # Per DATASET-CHANGELOG.md, missing_exif strips GPS AND timestamp
    # together but leaves the hash -- so require both missing, not either,
    # to avoid colliding with a row that's merely missing one field for an
    # unrelated (non-anomalous) reason.
    gps_missing = pd.isna(work.get("photo_gps_lat")) or pd.isna(work.get("photo_gps_lon"))
    time_missing = pd.isna(work.get("photo_captured_at"))
    if gps_missing and time_missing:
        # Can't be corroborated either way -- treated as suspicious per
        # DATASET-CHANGELOG.md, but weaker than a confirmed mismatch/backdate
        # since nothing here is *positively* contradictory, just unverifiable.
        return 0.55
    return 0.0


def _reused_photo_score(work: dict, all_works: pd.DataFrame) -> float:
    phash = work.get("photo_phash")
    if phash is None or pd.isna(phash):
        return 0.0

    index = _get_phash_index(all_works)
    positions = index.get(phash, [])
    if len(positions) < 2:
        return 0.0

    own_work_id = work.get("work_id")
    own_captured = work.get("photo_captured_at")
    own_captured_ts = pd.to_datetime(own_captured) if not pd.isna(own_captured) else None

    for pos in positions:
        cand = all_works.iloc[pos]
        if cand.get("work_id") == own_work_id:
            continue
        # Same hash, different work: a genuine duplicate progress photo.
        # Corroborate with the +/-2-3 day capture-date window where possible,
        # but an identical hash on two different works is already the core
        # signal (a real pHash collision this way is vanishingly unlikely),
        # so don't zero it out just because one side's date is missing.
        cand_captured = cand.get("photo_captured_at")
        if own_captured_ts is not None and not pd.isna(cand_captured):
            gap_days = abs((pd.to_datetime(cand_captured) - own_captured_ts).days)
            if gap_days <= REUSED_PHOTO_DATE_WINDOW_DAYS:
                return 1.0
            # Same hash but capture dates far apart: still a real hash
            # collision, just weaker corroboration -- score high but not max.
            return 0.75
        return 0.85  # hash match, date unverifiable either way

    return 0.0


def score_photo_forensics(work: dict, all_works) -> float:
    if not work.get("photo_available"):
        return 0.0  # nothing to forensically check for this work (Rules.md: don't invent data)

    scores = [
        _gps_mismatch_score(work),
        _backdated_photo_score(work),
        _missing_exif_score(work),
        _reused_photo_score(work, all_works),
    ]
    return float(np.clip(max(scores), 0.0, 1.0))


def evidence_photo_forensics(work: dict, all_works) -> dict:
    """
    Phase 14 (Evidence Layer) -- additive only. Returns the underlying
    numbers behind score_photo_forensics()'s float (GPS distance, capture
    vs sanction dates, which work_id a reused photo hash matches) -- for
    the detail page's photo/document forensics section. No change to
    score_photo_forensics() itself.
    """
    if not work.get("photo_available"):
        return {"photo_available": False}

    reused_score = _reused_photo_score(work, all_works)
    reused_with = None
    if reused_score > 0:
        phash = work.get("photo_phash")
        index = _get_phash_index(all_works)
        own_work_id = work.get("work_id")
        for pos in index.get(phash, []):
            cand = all_works.iloc[pos]
            if cand.get("work_id") != own_work_id:
                reused_with = cand.get("work_id")
                break

    return {
        "photo_available": True,
        "photo_captured_at": work.get("photo_captured_at"),
        "sanction_date": work.get("sanction_date"),
        "claimed_latitude": work.get("latitude"),
        "claimed_longitude": work.get("longitude"),
        "photo_gps_lat": work.get("photo_gps_lat"),
        "photo_gps_lon": work.get("photo_gps_lon"),
        "gps_distance_km": _haversine_km(
            work.get("photo_gps_lat"), work.get("photo_gps_lon"),
            work.get("latitude"), work.get("longitude"),
        ),
        "backdated_score": _backdated_photo_score(work),
        "missing_exif": bool(pd.isna(work.get("photo_gps_lat")) and pd.isna(work.get("photo_captured_at"))),
        "reused_photo_with_work_id": reused_with,
    }
