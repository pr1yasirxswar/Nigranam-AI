"""
Router: stats -- public, no-login aggregate figures.
  GET /stats/public-summary -- Phase 8 bug fix (PRD.md S5.1/S7 item 1):
                          sanctioned amount, completed count, ongoing
                          count. This is what the actual no-login public
                          dashboard renders now.
  GET /stats/public   -- Implementation-Guide.md Phase 5, item 1: total
                          works, flag/review ratios, and statewise
                          flagged-project counts. Kept, but no longer
                          rendered on the no-login public view (that was
                          the S7 item 1 scope-leak bug) -- unused by any
                          view right now until Phase 10 gives it a proper
                          authority-only home.

Deliberately exposes only aggregate counts and severity tiers -- never
individual review text or reviewer identities (see routers/flags.py's
/public docstring for the same reasoning).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.data_loader import get_all_works
from app.models import Flag, Review
from app.escalation.state_machine import ESCALATION_CHAIN

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/public-summary")
def public_summary():
    """
    Phase 8 bug fix (PRD.md S5.1, S7 item 1) -- the ONLY aggregate figures
    the true public/no-login dashboard is allowed to render: sanctioned
    amount, completed count, ongoing count (matches eSAKSHI's own public
    dashboard style). No flag/tier/reviewer data here -- that's GET
    /stats/public below, kept but no longer wired into the no-login view.

    ASSUMPTION FLAGGED (not spelled out in PRD.md): "sanctioned amount"
    is summed only over works whose status shows a sanction actually
    happened (Sanctioned / In Progress / Completed) -- the dataset's
    sanctioned_amount column is populated even for Recommended/Rejected
    rows (a planning-stage estimate), which would overstate a "sanctioned"
    figure if included. "Ongoing" maps to the dataset's "In Progress"
    status value; there is no literal "Ongoing" status in the CSV.
    """
    all_works = get_all_works()
    sanctioned_statuses = {"Sanctioned", "In Progress", "Completed"}
    sanctioned_rows = all_works[all_works["status"].isin(sanctioned_statuses)]

    return {
        "total_works": int(len(all_works)),
        "sanctioned_amount_total": float(sanctioned_rows["sanctioned_amount"].fillna(0).sum()),
        "completed_count": int((all_works["status"] == "Completed").sum()),
        "ongoing_count": int((all_works["status"] == "In Progress").sum()),
    }


@router.get("/public")
def public_stats(db: Session = Depends(get_db)):
    all_works = get_all_works()
    total_works = len(all_works)
    work_state = dict(zip(all_works["work_id"], all_works["state"]))

    flags = db.query(Flag).all()
    total_flags = len(flags)
    open_flags = sum(1 for f in flags if f.status in ESCALATION_CHAIN)
    resolved_flags = total_flags - open_flags
    resolution_rate = round(resolved_flags / total_flags, 3) if total_flags else None

    total_reviews = db.query(func.count(Review.id)).scalar() or 0

    tier_counts: dict[str, int] = {}
    statewise: dict[str, int] = {}
    for f in flags:
        tier_counts[f.tier] = tier_counts.get(f.tier, 0) + 1
        state = work_state.get(f.work_id) or "Unknown"
        statewise[state] = statewise.get(state, 0) + 1

    return {
        "total_works": total_works,
        "total_flags": total_flags,
        "open_flags": open_flags,
        "resolved_flags": resolved_flags,
        "resolution_rate": resolution_rate,
        "total_reviews": total_reviews,
        "tier_counts": tier_counts,
        "statewise_flagged_counts": sorted(
            [{"state": s, "flagged_count": c} for s, c in statewise.items()],
            key=lambda row: -row["flagged_count"],
        ),
    }
