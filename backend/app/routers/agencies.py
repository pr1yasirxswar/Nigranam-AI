"""
Router: agencies -- Phase 5 Network view.
  GET /agencies/network  -- agency-district risk-concentration graph
                             (modules/agency_network.py), gated to Local
                             Authority/District/State/MP (not Implementing
                             Agency -- Implementation-Guide.md Phase 5,
                             item 3).

SCOPING DESIGN DECISION (not spelled out in PRD.md, flagged here): the
graph's edges only ever connect same-state units (agency_network.py), so
"regional risk concentration" is inherently a state-level picture. Rather
than inventing a finer per-district slice PRD.md doesn't specify, this
scopes the whole response to the caller's own state -- District/Local
Authority see their state's full risk-concentration picture (useful
context for a District deciding whether to allot work to a flagged
agency), State Authority and MP see the same for their own state.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.data_loader import get_all_works
from app.database import get_db
from app.auth.identity import get_current_user
from app.auth.permissions import ROLE_IMPLEMENTING_AGENCY, require_roles, ROLE_DISTRICT_AUTHORITY
from app.auth.agency_auth import issue_agency_credentials
from app.modules.agency_network import get_graph_and_centrality, get_reasoned_units

router = APIRouter(prefix="/agencies", tags=["agencies"])


def _caller_state(user, all_works) -> str | None:
    if user.role == "nodal_state_authority":
        return user.state
    if user.role in ("district_authority", "local_authority"):
        match = all_works[all_works["implementing_district"] == user.district]
        return None if match.empty else match["state"].iloc[0]
    if user.role == "mp":
        match = all_works[all_works["constituency"] == user.constituency]
        return None if match.empty else match["state"].iloc[0]
    return None


@router.get("/network")
def get_network(user=Depends(get_current_user)):
    if user.role == ROLE_IMPLEMENTING_AGENCY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Implementing Agency does not have network-view access",
        )

    all_works = get_all_works()
    state = _caller_state(user, all_works)
    if state is None:
        return {"state": None, "nodes": [], "edges": []}

    graph, centrality = get_graph_and_centrality(all_works)
    reasons = get_reasoned_units(all_works)  # Phase 8 bug fix (PRD.md S7 item 3)

    # district -> state, computed once rather than per-node, so this stays
    # cheap even with many elevated units.
    district_state = (
        all_works.dropna(subset=["implementing_district", "state"])
        .drop_duplicates("implementing_district")
        .set_index("implementing_district")["state"]
        .to_dict()
    )

    units_in_state = [u for u in graph.nodes if district_state.get(u[1]) == state]

    # Phase 8 bug fix (PRD.md S7 item 3) -- only units with a genuine,
    # evidenced reason are returned at all. A unit that only cleared the
    # graph's elevated-MEAN threshold with no High+ work of its own is
    # dropped here rather than shown with a bare centrality number.
    nodes = []
    for agency, district in units_in_state:
        reason_info = reasons.get((agency, district))
        if reason_info is None:
            continue
        nodes.append({
            "agency": agency,
            "district": district,
            "centrality": round(centrality.get((agency, district), 0.0), 3),
            "works_count": reason_info["works_count"],
            "high_plus_count": reason_info["high_plus_count"],
            "reason": reason_info["reason"],
        })
    nodes.sort(key=lambda n: -n["centrality"])

    node_set = {(n["agency"], n["district"]) for n in nodes}

    edges = [
        {
            "source": {"agency": a1, "district": d1},
            "target": {"agency": a2, "district": d2},
        }
        for (a1, d1), (a2, d2) in graph.edges
        if (a1, d1) in node_set and (a2, d2) in node_set
    ]

    return {"state": state, "nodes": nodes, "edges": edges}


class IssueCredentialsBody(BaseModel):
    executing_agency: str
    display_name: str | None = None


# Phase 11 item 3 -- "District Authority action 'assign work to agency'
# generates a Work ID + password pair, stored against that agency."
# Scoped to the calling District Authority's own district -- a District
# Authority can only assign work within its own jurisdiction, same
# principle auth/permissions.py already enforces for reads.
@router.post("/issue-credentials")
def issue_credentials(
    body: IssueCredentialsBody,
    user=Depends(require_roles(ROLE_DISTRICT_AUTHORITY)),
    db: Session = Depends(get_db),
):
    if not body.executing_agency.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="executing_agency is required.")

    all_works = get_all_works()
    state_match = all_works[all_works["implementing_district"] == user.district]
    state = None if state_match.empty else state_match["state"].iloc[0]

    new_user, plain_password = issue_agency_credentials(
        db,
        executing_agency=body.executing_agency.strip(),
        implementing_district=user.district,
        state=state,
        issued_by=user,
        display_name=body.display_name,
    )
    return {
        "work_id": new_user.x_user_id,
        "password": plain_password,  # shown once -- relay to the agency out of band
        "executing_agency": new_user.executing_agency,
        "implementing_district": new_user.district,
        "note": "This password is shown only once. It is not recoverable -- issue a new credential if it's lost.",
    }
