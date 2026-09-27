# Pydantic request/response schemas -- filled in alongside each router as
# it's built (Phase 1 onward). Left intentionally empty until a router
# needed one -- Phase 4's reviews router is the first to need a request
# body, so ReviewCreate is the first real schema here.
from pydantic import BaseModel


class ReviewCreate(BaseModel):
    """
    POST body for /projects/{work_id}/reviews (routers/reviews.py).
    action: "comment" | "clear" | "escalate" | "send_back" -- see
      escalation/state_machine.py for what each does to the flag's status.
      "send_back" is Phase 12 item 7's "correct defects & resubmit" path,
      only meaningful on a real (DB-backed) project's flag.
    reason: required (non-empty) for "clear", "escalate", and "send_back" --
      this text is exactly the "justification" escalation/
      reviewer_diligence.py scores. Optional for "comment".
    """
    action: str
    reason: str | None = None


class ProjectCreate(BaseModel):
    """
    POST body for /projects (routers/projects.py, Phase 12 item 3) --
    Member of Parliament only. `mp_name`/`constituency`/`state` are NOT
    part of this body -- they're defaulted from the caller's own
    jurisdiction, never client-supplied (same principle
    auth/permissions.py's docstring already states: jurisdiction is a
    server-side fact, not a client claim). `implementing_district` IS
    client-supplied: an MP's own jurisdiction key is `constituency`, not a
    district (auth/permissions.py), and a constituency can span more than
    one implementing district in the real MPLADS scheme, so the MP has to
    say which district this recommendation is for.
    """
    work_category: str
    work_description: str
    village_or_locality: str
    implementing_district: str
    nodal_district: str | None = None  # defaults to implementing_district if omitted
    estimated_cost: float
    latitude: float | None = None
    longitude: float | None = None
    expected_completion_date: str | None = None  # ISO date string, optional


class ProjectSanction(BaseModel):
    """
    POST body for /projects/{work_id}/sanction (Phase 12 item 3) --
    District Authority only. Per item 2's flagged assumption, bidding/
    tender is deferred -- this sanctions directly to a named
    `executing_agency`, which must already hold Implementing Agency
    credentials in the caller's own district (issued via the existing
    POST /agencies/issue-credentials, Phase 11 item 3).
    """
    sanctioned_amount: float
    executing_agency: str
    expected_completion_date: str | None = None
