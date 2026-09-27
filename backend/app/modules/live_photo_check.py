"""
Phase 12 item 5 -- reject-at-upload for a live Implementing Agency photo
upload (user's instruction: "accept hi nahi karni h aaisi images").

photo_forensics.py (Phase 6, stable, untouched per Rules.md) SCORES a
photo that's already saved -- correct for the read-only synthetic CSV
(nothing to "reject", it's historical data), but wrong for a live upload.
This module runs the same cheap, single-photo checks BEFORE the photo is
persisted or scored by the full 8-signal pipeline, reusing
photo_forensics.py's own sub-check functions directly (imported, not
re-implemented or edited -- Rules.md: "don't rewrite a module that's
passed its exit check").

Reject line: the existing High-tier floor risk_aggregator.py already
defines (TIER_THRESHOLDS), not a new invented number -- per
Implementation-Guide.md Phase 12 item 5's proposal. Below that floor, the
photo is saved normally and still runs through the full pipeline like any
other photo -- Medium-tier issues stay a reviewable Flag, not a hard
block; only the clear-cut cases get rejected outright.

Perceptual hash: implemented here with Pillow ALONE (a simple 8x8
average-hash) rather than adding a separate hashing library -- the
Implementation Guide flagged "Pillow + a hashing lib" as a new dependency
needing sign-off; doing the hash with Pillow only keeps this to one new
dependency (Pillow) instead of two.
"""
from datetime import datetime
from io import BytesIO
import logging

from PIL import Image, ExifTags

from app.modules.photo_forensics import (
    _gps_mismatch_score,
    _backdated_photo_score,
    _missing_exif_score,
    _haversine_km,
)
from app.scoring.risk_aggregator import TIER_THRESHOLDS

logger = logging.getLogger("sentinel.live_photo_check")

# Same "High" floor the full pipeline already uses to raise a flag at all
# -- reused, not a new invented reject threshold (item 5's proposal).
REJECT_SCORE_FLOOR = next(floor for tier, floor in TIER_THRESHOLDS if tier == "High")


def _dms_to_decimal(dms, ref) -> float | None:
    try:
        degrees, minutes, seconds = (float(v) for v in dms)
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    if ref in ("S", "W"):
        value = -value
    return value


def extract_photo_metadata(image_bytes: bytes) -> dict:
    """
    Best-effort EXIF GPS + capture-time + a simple perceptual hash from
    raw uploaded photo bytes. Any missing/malformed EXIF is logged and
    treated as absent (Rules.md's error-handling rule: log failures,
    never crash the pipeline, never fabricate a value).
    """
    result = {"gps_lat": None, "gps_lon": None, "captured_at": None, "phash": None}
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
        if exif:
            tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            captured_raw = tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")
            if captured_raw:
                try:
                    result["captured_at"] = datetime.strptime(str(captured_raw), "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    logger.warning("photo EXIF capture-time malformed, treating as missing: %r", captured_raw)

            gps_ifd = None
            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            except (KeyError, AttributeError):
                pass
            if gps_ifd:
                lat, lat_ref = gps_ifd.get(2), gps_ifd.get(1)
                lon, lon_ref = gps_ifd.get(4), gps_ifd.get(3)
                if lat and lon:
                    result["gps_lat"] = _dms_to_decimal(lat, lat_ref)
                    result["gps_lon"] = _dms_to_decimal(lon, lon_ref)

        # Simple 8x8 average-hash -- Pillow only, no separate hashing lib
        # (see module docstring).
        small = img.convert("L").resize((8, 8))
        pixels = list(small.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        result["phash"] = f"{int(bits, 2):016x}"
    except Exception:
        logger.warning("EXIF/perceptual-hash extraction failed on an uploaded photo, treating as missing", exc_info=True)
    return result


def check_and_score(
    *,
    metadata: dict,
    project_lat,
    project_lon,
    sanction_date,
    prior_phashes: list[str],
) -> dict:
    """
    Runs the same per-photo checks photo_forensics.py already has formulas
    for, against ONE live upload, before it's saved.

    `prior_phashes` is scoped to this project's OWN earlier photos only --
    deliberately narrower than the full pipeline's whole-dataset
    reused-photo check (photo_forensics.py's _reused_photo_score), per
    item 5's "phash collision against this project's OWN prior photos".

    Returns {"reject": bool, "reason": str | None, "scores": {...}}.
    """
    work_like = {
        "photo_gps_lat": metadata["gps_lat"],
        "photo_gps_lon": metadata["gps_lon"],
        "latitude": project_lat,
        "longitude": project_lon,
        "photo_captured_at": metadata["captured_at"],
        "sanction_date": sanction_date,
    }
    gps_score = _gps_mismatch_score(work_like)
    backdated_score = _backdated_photo_score(work_like)
    missing_score = _missing_exif_score(work_like)

    own_phash_hit = metadata["phash"] is not None and metadata["phash"] in prior_phashes
    reused_score = 1.0 if own_phash_hit else 0.0

    scores = {
        "gps_mismatch": gps_score,
        "backdated": backdated_score,
        "missing_exif": missing_score,
        "reused_own_project": reused_score,
    }

    if gps_score >= REJECT_SCORE_FLOOR:
        dist_km = _haversine_km(metadata["gps_lat"], metadata["gps_lon"], project_lat, project_lon)
        reason = (
            f"GPS location is {dist_km:.0f}km from the project site"
            if dist_km is not None else "GPS location does not match the project site"
        )
        return {"reject": True, "reason": reason, "scores": scores}

    if backdated_score >= REJECT_SCORE_FLOOR:
        return {
            "reject": True,
            "reason": "Photo capture date is backdated well before this project's sanction date",
            "scores": scores,
        }

    if reused_score >= REJECT_SCORE_FLOOR:
        return {
            "reject": True,
            "reason": "This photo has already been submitted for this project (duplicate image)",
            "scores": scores,
        }

    # missing_exif is capped at 0.55 in photo_forensics.py -- deliberately
    # below REJECT_SCORE_FLOOR (0.65), so it never triggers a hard reject
    # here; it still becomes a reviewable Medium-tier Flag once the full
    # pipeline scores the saved photo (item 5: "only the clear-cut cases
    # get rejected outright").
    return {"reject": False, "reason": None, "scores": scores}
