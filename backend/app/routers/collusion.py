"""
Router: collusion -- GET /collusion/alerts (Phase 9, PRD.md S4.9,
Implementation-Guide.md Phase 9 item 3). Gated to Nodal State Authority
only (Rules.md: "gated to Nodal State Authority only") -- this is a
"watch the watchers" signal about OTHER authorities' review patterns
(Local/District/MP could each be the offending authority), so the tier
positioned to see it is the one that already sits above District in the
hierarchy, same framing reviewer_diligence.py already uses for "the next
tier up sees a role's clearance patterns."

SCOPING DESIGN DECISION (not spelled out in PRD.md, flagged here, same
pattern routers/agencies.py already uses for its own state-scoping call):
a Nodal State Authority should only see collusion alerts about authorities
whose OWN jurisdiction sits inside that caller's state -- not every alert
nationwide. _authority_state() below derives the offending authority's
state the same way routers/agencies.py's _caller_state() derives a
caller's state (district/constituency -> state, via the works dataset),
deliberately reimplemented here rather than importing agencies.py's
private helper, so this router doesn't create a dependency on another
router's internals for something this small.
"""
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.data_loader import get_all_works
from app.models import CollusionAlert, User
from app.auth.permissions import require_roles, ROLE_NODAL_STATE_AUTHORITY

router = APIRouter(prefix="/collusion", tags=["collusion"])


def _authority_state(authority: User, all_works) -> str | None:
    if authority.role == "nodal_state_authority":
        return authority.state
    if authority.role in ("district_authority", "local_authority"):
        match = all_works[all_works["implementing_district"] == authority.district]
        return None if match.empty else match["state"].iloc[0]
    if authority.role == "mp":
        match = all_works[all_works["constituency"] == authority.constituency]
        return None if match.empty else match["state"].iloc[0]
    return None


@router.get("/alerts")
def list_alerts(
    user: User = Depends(require_roles(ROLE_NODAL_STATE_AUTHORITY)),
    db: Session = Depends(get_db),
):
    all_works = get_all_works()
    alerts = db.query(CollusionAlert).order_by(CollusionAlert.created_at.desc()).all()

    result = []
    for a in alerts:
        authority = db.query(User).filter(User.id == a.authority_id).first()
        if authority is None:
            continue  # orphaned row -- shouldn't happen, skip defensively
        if _authority_state(authority, all_works) != user.state:
            continue  # not this caller's state -- jurisdiction filtering (Rules.md)

        result.append({
            "id": a.id,
            "authority_name": authority.name,
            "authority_role": authority.role,
            "agency_id": a.agency_id,
            "reason": a.reason,
            "related_work_ids": json.loads(a.related_work_ids),
            "override_count": a.override_count,
            "created_at": a.created_at.isoformat(),
        })
    return result
