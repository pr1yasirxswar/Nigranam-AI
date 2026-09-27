"""
Phase 3 -- require_roles(), user_can_act_on_project(), jurisdiction-scoped
filtering. Local Authority jurisdiction scopes on the COMPOUND KEY
(district, loc) -- loc values (e.g. "Block 7") repeat across 36+ districts,
so district alone is not enough (Rules.md, PRD.md S3).

Works live in Pandas, not Postgres (Architecture.md), so "the query layer"
here means filter_works_for_user()/user_can_act_on_project() -- every
router MUST filter through these, server-side, rather than returning the
full works list and trusting the frontend to hide rows (Rules.md).
"""
from fastapi import Depends, HTTPException, status
import pandas as pd

from app.auth.identity import get_current_user

ROLE_IMPLEMENTING_AGENCY = "implementing_agency"
ROLE_LOCAL_AUTHORITY = "local_authority"
ROLE_DISTRICT_AUTHORITY = "district_authority"
ROLE_NODAL_STATE_AUTHORITY = "nodal_state_authority"
ROLE_MP = "mp"

# Confirmed hierarchy, lowest to highest (PRD.md S3, Architecture.md S8):
ALL_ROLES = [
    ROLE_IMPLEMENTING_AGENCY,
    ROLE_LOCAL_AUTHORITY,
    ROLE_DISTRICT_AUTHORITY,
    ROLE_NODAL_STATE_AUTHORITY,
    ROLE_MP,
]


def require_roles(*roles):
    """
    FastAPI dependency factory -- gates an entire endpoint to the given
    role(s). Usage: Depends(require_roles(ROLE_DISTRICT_AUTHORITY, ROLE_NODAL_STATE_AUTHORITY))
    """
    def _dependency(user=Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"role '{user.role}' cannot access this endpoint (requires one of {list(roles)})",
            )
        return user
    return _dependency


def _work_matches_jurisdiction(user, work: dict) -> bool:
    """
    Row-level jurisdiction check, one work against one user. Shared by
    user_can_act_on_project() (single-work 403 gate) and
    filter_works_for_user() (list-endpoint filtering) so the two can never
    drift out of sync with each other.
    """
    if user.role == ROLE_IMPLEMENTING_AGENCY:
        # Same (executing_agency, implementing_district) operating-unit key
        # agency_network.py already uses for its graph nodes (Phase 2) --
        # the dataset only carries 7 generic agency-type categories, not
        # distinct company identities, so this is the most granular
        # "own works" scoping the data actually supports. Flagging this as
        # a design choice, not something PRD.md spells out directly.
        return (
            work.get("executing_agency") == user.executing_agency
            and work.get("implementing_district") == user.district
        )
    if user.role == ROLE_LOCAL_AUTHORITY:
        return (
            work.get("implementing_district") == user.district
            and work.get("village_or_locality") == user.loc
        )
    if user.role == ROLE_DISTRICT_AUTHORITY:
        return work.get("implementing_district") == user.district
    if user.role == ROLE_NODAL_STATE_AUTHORITY:
        return work.get("state") == user.state
    if user.role == ROLE_MP:
            wc = work.get("constituency")
            uc = getattr(user, "constituency", None)
            if wc is None or uc is None:
                return False
            return str(wc).strip().upper() == str(uc).strip().upper()
    return False


def user_can_act_on_project(user, work: dict) -> bool:
    """
    True if this user's jurisdiction covers this work. Used to gate
    single-work endpoints -- e.g. a 403 when one MP tries to act on a
    project in a different constituency (Implementation-Guide.md Phase 3
    exit check).
    """
    return _work_matches_jurisdiction(user, work)


def filter_works_for_user(user, all_works: pd.DataFrame) -> pd.DataFrame:
    """
    Server-side jurisdiction filtering over the works dataset -- the
    query-layer equivalent of a WHERE clause for a role's "mine" view.
    Never perform this filtering in the frontend (Rules.md).
    """
    if all_works.empty:
        return all_works
    mask = all_works.apply(lambda row: _work_matches_jurisdiction(user, row.to_dict()), axis=1)
    return all_works[mask]
