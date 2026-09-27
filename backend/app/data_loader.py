import pandas as pd
from app.config import settings

_works_df = None

def load_works() -> pd.DataFrame:
    """Loads the synthetic works CSV once, keeps it in memory, read-only.
    This is intentionally NOT written into Postgres -- Postgres holds only
    mutable data (users, reviews, flags, and, since Phase 12, Project/
    StageSubmission rows -- see get_all_works() below)."""
    global _works_df
    if _works_df is None:
        _works_df = pd.read_csv(settings.csv_path)
    return _works_df


def _load_db_projects_df() -> pd.DataFrame:
    """
    Phase 12 item 1 -- the mutable, Postgres-backed counterpart to the
    read-only CSV. Rebuilt fresh from the `Project` table on every call
    (small table, cheap to rebuild -- no caching correctness risk,
    Implementation-Guide.md Phase 12 item 1).

    Only the columns the 8 scoring modules and jurisdiction filters
    actually need are populated here; pd.concat() in get_all_works()
    fills every other CSV-only column (BOQ figures, the seeded-anomaly
    ground truth, etc.) with NaN for these rows -- the scoring modules
    already treat a missing/NaN signal as "not available" via pd.isna()
    guards throughout (see photo_forensics.py, duplicate.py), so this
    isn't a new column-shape problem this phase has to solve.
    """
    from app.database import SessionLocal  # local import -- avoids a
    # database.py <-> data_loader.py import cycle at module-load time
    from app.models import Project

    db = SessionLocal()
    try:
        rows = db.query(Project).all()
        if not rows:
            return pd.DataFrame()
        records = [{
            "work_id": p.work_id,
            "mp_name": p.mp_name,
            "state": p.state,
            "constituency": p.constituency,
            "nodal_district": p.nodal_district,
            "implementing_district": p.implementing_district,
            "work_category": p.work_category,
            "work_description": p.work_description,
            "executing_agency": p.executing_agency,
            "village_or_locality": p.village_or_locality,
            "estimated_cost": p.estimated_cost,
            "sanctioned_amount": p.sanctioned_amount,
            "expenditure": p.expenditure,
            "recommendation_date": p.recommendation_date,
            "sanction_date": p.sanction_date,
            "expected_completion_date": p.expected_completion_date,
            "actual_completion_date": p.actual_completion_date,
            "physical_progress_pct": p.physical_progress_pct,
            "status": p.status,
            "latitude": p.latitude,
            "longitude": p.longitude,
            "data_type": p.data_type,
            "photo_available": p.latest_photo_available,
            "photo_gps_lat": p.latest_photo_gps_lat,
            "photo_gps_lon": p.latest_photo_gps_lon,
            "photo_captured_at": p.latest_photo_captured_at,
            "photo_phash": p.latest_photo_phash,
        } for p in rows]
        return pd.DataFrame.from_records(records)
    finally:
        db.close()


def get_all_works() -> pd.DataFrame:
    """
    Phase 12 item 1 -- every router/module that used to call load_works()
    switches to this instead (one-line change per call site, per the
    Implementation Guide). Additive only: CSV rows still flow through
    exactly as before; DB-backed `Project` rows (real, independently
    progressed projects) are appended fresh on every call so newly
    created/sanctioned/progressed projects show up immediately in every
    existing dashboard/endpoint with zero changes to those endpoints'
    own code.
    """
    csv_df = load_works()
    db_df = _load_db_projects_df()
    if db_df.empty:
        return csv_df
    return pd.concat([csv_df, db_df], ignore_index=True, sort=False)


def get_work_by_id(work_id: str):
    df = get_all_works()
    row = df[df["work_id"] == work_id]
    return row.iloc[0].to_dict() if not row.empty else None
