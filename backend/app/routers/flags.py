"""
Router: flags -- escalation endpoints.
  GET  /flags/mine                  -- flags in the caller's jurisdiction,
                                        at the stage their role is
                                        currently accountable for.
  GET  /flags/{work_id}             -- one work's current flag status.
  POST /flags/{work_id}/raise       -- creates a flag if the work's
                                        current risk tier is Medium+ and no
                                        open flag already exists. Stands in
                                        for the automatic pipeline trigger
                                        a real deployment would have (see
                                        escalation/state_machine.py's
                                        create_flag() docstring).
  GET  /flags/diligence/{role}      -- reviewer-diligence report for one
                                        role (escalation/reviewer_diligence.py).
  GET  /flags/analysis/public       -- Phase 8 bug fix (PRD.md S7 item 2):
                                        every work that has had a real
                                        detection pass run, regardless of
                                        tier -- lets the frontend show
                                        "pending analysis" vs "clear" vs
                                        "flagged" honestly.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.data_loader import get_all_works, get_work_by_id
from app.auth.identity import get_current_user
from app.auth.permissions import (
    user_can_act_on_project,
    filter_works_for_user,
    require_roles,
    ROLE_DISTRICT_AUTHORITY,
    ROLE_NODAL_STATE_AUTHORITY,
    ROLE_MP,
)
from app.models import Flag, AnalysisResult
from app.escalation.state_machine import ESCALATION_CHAIN, ROLE_FOR_STAGE
from app.escalation.reviewer_diligence import score_reviewer_diligence
from app.scoring.pipeline import score_and_flag_work

router = APIRouter(prefix="/flags", tags=["flags"])


def _serialize_flag(f: Flag) -> dict:
    return {
        "work_id": f.work_id,
        "status": f.status,
        "tier": f.tier,
        "shape": f.shape,
        "driving_signal": f.driving_signal,
        "stage_deadline": f.stage_deadline.isoformat(),
        "created_at": f.created_at.isoformat(),
        "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
    }


@router.get("/public")
def list_public_flags(db: Session = Depends(get_db)):
    """
    Phase 5 -- public, no-login flag summary. Backs the public dashboard's
    ratios and the Registry's "reason" column (Implementation-Guide.md
    Phase 5, items 1 and 2 -- both explicitly "no login required").

    Deliberately exposes only the AI's own output (tier/shape/driving
    signal/escalation stage) and never reviewer identities or free-text
    review reasons -- PRD.md's transparency pitch is about visible
    OUTCOMES, not exposing internal deliberation to the public. Declared
    ahead of GET /{work_id} below so "public" is never swallowed as a
    work_id path parameter.
    """
    flags = db.query(Flag).order_by(Flag.created_at.desc()).all()
    return [_serialize_flag(f) for f in flags]


@router.get("/mine")
def list_my_flags(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """
    A role's own queue: unresolved flags, within their jurisdiction, sitting
    at the stage their role is accountable for right now. (Implementing
    Agency has no accountable stage of its own -- it isn't a reviewing
    tier -- so it sees every unresolved flag on its own works instead,
    since PRD.md S3 has agencies "respond to flags.")
    """
    accountable_stage = next((s for s, r in ROLE_FOR_STAGE.items() if r == user.role), None)

    all_works = get_all_works()
    my_work_ids = set(filter_works_for_user(user, all_works)["work_id"])

    query = db.query(Flag).filter(Flag.status.in_(ESCALATION_CHAIN))
    if accountable_stage:
        query = query.filter(Flag.status == accountable_stage)
    flags = [f for f in query.all() if f.work_id in my_work_ids]

    return [_serialize_flag(f) for f in flags]


@router.get("/diligence/{role}")
def get_diligence(
    role: str,
    executing_agency: str | None = None,
    user=Depends(require_roles(ROLE_DISTRICT_AUTHORITY, ROLE_NODAL_STATE_AUTHORITY, ROLE_MP)),
    db: Session = Depends(get_db),
):
    """
    SIMPLIFICATION FLAGGED: gated to District/State/MP -- the tiers with a
    tier below them to audit (Local Authority has none). Any of the three
    can currently view diligence for ANY role, rather than each tier being
    restricted to only the one immediately below it -- a stricter "who
    audits whom" mapping isn't spelled out in PRD.md; worth tightening with
    the team before the pitch if judges push on it.

    Declared ahead of GET /{work_id:path} below -- :path is greedy and
    would otherwise swallow "diligence/<role>" whole as a work_id.
    """
    work_ids = None
    if executing_agency:
        all_works = get_all_works()
        work_ids = all_works[all_works["executing_agency"] == executing_agency]["work_id"].tolist()
    return score_reviewer_diligence(db, role, work_ids)


@router.get("/analysis/public")
def list_public_analysis_results(db: Session = Depends(get_db)):
    """
    Phase 8 bug fix (PRD.md S7 item 2) -- public, no-login list of every
    work that has actually had a detection pass run against it, regardless
    of tier. The Registry view joins this against GET /projects and
    GET /flags/public to render three honest states: pending analysis (no
    row here), clear (row here, no open Flag), flagged (open Flag exists --
    rendered from Flag data). Declared ahead of GET /{work_id:path} below
    for the same reason /public, /mine and /diligence/{role} are -- :path
    is greedy and would otherwise swallow "analysis/public" whole as a
    work_id.
    """
    results = db.query(AnalysisResult).all()
    return [
        {
            "work_id": r.work_id,
            "tier": r.tier,
            "shape": r.shape,
            "driving_signal": r.driving_signal,
            "scored_at": r.scored_at.isoformat(),
        }
        for r in results
    ]


@router.get("/{work_id:path}")  # :path -- see projects.py's note on work_id containing "/"
def get_flag(work_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{user.role}' does not have jurisdiction over work_id '{work_id}'",
        )
    flag = db.query(Flag).filter(Flag.work_id == work_id).order_by(Flag.created_at.desc()).first()
    if flag is None:
        return {"work_id": work_id, "status": "no_flag"}
    return _serialize_flag(flag)


@router.post("/{work_id:path}/raise")  # :path -- same reason as get_flag() above
def raise_flag(work_id: str, db: Session = Depends(get_db)):
    all_works = get_all_works()
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")

    # Shared with the bulk /debug/run-full-scoring pipeline (app/scoring/
    # pipeline.py) so a single work scored here and 5,940 works scored in
    # bulk always go through identical logic.
    return score_and_flag_work(db, work_id, work, all_works)
