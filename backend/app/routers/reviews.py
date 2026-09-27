"""
Router: reviews -- GET/POST /projects/{work_id}/reviews. The append-only
audit trail for a work's flag: comments, clears, escalations, and the
system's own flagged/routed/auto_escalate events, all as Review rows
(Rules.md -- never a delete/overwrite, always a new insert).

Visibility cascade (PRD.md S4.8), as implemented here: any role whose
jurisdiction already covers this work can see its FULL review history --
since jurisdiction is already hierarchical (state jurisdiction covers a
district's works, a district covers a local area's, a constituency covers
an MP's), gating GET by user_can_act_on_project() alone reproduces the
cascade (District already sees Local's conversation with the agency,
State already sees District's, MP already sees everything in their
constituency) without needing separate per-tier-segmented threads -- there
is one shared conversation per work, not one per tier. Flagging this as
the specific interpretation used, since PRD.md describes the cascade in
prose, not as a concrete filtering rule.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from datetime import datetime

from app.database import get_db
from app.data_loader import get_work_by_id
from app.auth.identity import get_current_user
from app.auth.permissions import user_can_act_on_project
from app.schemas import ReviewCreate
from app.models import Flag, Review, Project, StageSubmission
from app.escalation.state_machine import apply_action, ESCALATION_CHAIN
from app.modules.collusion import check_and_record

router = APIRouter(prefix="/projects/{work_id:path}/reviews", tags=["reviews"])
# :path -- work_id values contain literal "/" (e.g.
# "MPLADS/MAH/2021/000382"); the default str converter stops at the first
# "/" and 404s. Found while smoke-testing Phase 5 against real work_ids --
# flagged here since this is a fix to already-"stable" Phase-4 code
# (Rules.md), not a rewrite of its logic. FastAPI/Starlette still matches
# the trailing "/reviews" literal correctly with :path in the middle.


def _get_active_flag(db: Session, work_id: str) -> Flag:
    flag = (
        db.query(Flag)
        .filter(Flag.work_id == work_id, Flag.status.in_(ESCALATION_CHAIN))
        .order_by(Flag.created_at.desc())
        .first()
    )
    if flag is None:
        raise HTTPException(status_code=404, detail=f"no active (unresolved) flag for work_id '{work_id}'")
    return flag


def _check_jurisdiction(user, work_id: str):
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{user.role}' does not have jurisdiction over work_id '{work_id}'",
        )


@router.get("")
def list_reviews(work_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _check_jurisdiction(user, work_id)
    reviews = (
        db.query(Review)
        .filter(Review.work_id == work_id)
        .order_by(Review.created_at.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "actor_role": r.actor_role,
            "action": r.action,
            "from_status": r.from_status,
            "to_status": r.to_status,
            "reason": r.reason,
            "recipient_role": r.recipient_role,  # Phase 9 -- see models.py's Review docstring
            "created_at": r.created_at.isoformat(),
        }
        for r in reviews
    ]


def _apply_stage_review_side_effects(db: Session, work_id: str, action: str) -> None:
    """
    Phase 12 item 7 -- "Human Review by Local Officials -> Approve/Reject
    -> Release Payment" maps onto this existing review endpoint (no new
    approval system); this is the ADDITIONAL Project/StageSubmission-side
    effect a clear/send_back action on a real (DB-backed) Project's flag
    has, on top of the flag's own state change (which apply_action()
    above already applied and committed).

    A no-op for a synthetic-CSV work_id (no Project row) -- those never
    had a StageSubmission to begin with.
    """
    project = db.query(Project).filter(Project.work_id == work_id).first()
    if project is None:
        return

    latest_stage = (
        db.query(StageSubmission)
        .filter(StageSubmission.project_id == project.id)
        .order_by(StageSubmission.submitted_at.desc())
        .first()
    )

    if action == "clear":
        if latest_stage is not None:
            latest_stage.accepted = True
        # "Release Payment Installment" (item 7's question 4) -- record-
        # keeping only, and the expenditure running-total is already
        # updated at submission time (routers/projects.py); the only
        # remaining effect of a clear is completion, once all stages are
        # done (100% physical progress).
        if project.physical_progress_pct is not None and project.physical_progress_pct >= 100:
            project.status = "Completed"
            project.actual_completion_date = datetime.utcnow()
        db.commit()
    elif action == "send_back":
        # Flowchart's "Contractor: Correct Defects & Resubmit" -- the
        # agency sees this stage needs a corrected resubmission; no
        # status change to the Project itself (it stays "In Progress",
        # simply awaiting a new StageSubmission from the same agency).
        if latest_stage is not None:
            latest_stage.accepted = False
        db.commit()


@router.post("")
def post_review(
    work_id: str,
    body: ReviewCreate,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _check_jurisdiction(user, work_id)
    flag = _get_active_flag(db, work_id)

    try:
        flag = apply_action(db, flag, user, body.action, body.reason)
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Phase 9 (PRD.md S4.9) -- a "clear" is exactly the event
    # modules/collusion.py watches for (an authority waving off a Medium+
    # flag). Checked AFTER apply_action()'s own commit, never inline with
    # it -- this is a read of the now-committed audit trail, not a second
    # state change on the flag itself. Any other action ("comment",
    # "escalate") isn't a clearance and has nothing for this module to do.
    if body.action == "clear":
        check_and_record(db, work_id, user)

    # Phase 12 item 7 -- clear/send_back's additional effect on a real
    # Project's latest StageSubmission/completion status, same "read the
    # now-committed audit trail" ordering as the collusion check above.
    if body.action in ("clear", "send_back"):
        _apply_stage_review_side_effects(db, work_id, body.action)

    return {"work_id": work_id, "flag_status": flag.status}
