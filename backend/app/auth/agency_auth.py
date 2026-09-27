"""
Phase 11 item 3 -- District Authority action "assign work to agency"
generates a Work ID + password pair, stored against that agency
(Implementation-Guide.md Phase 11 item 3 / PRD.md S5.2's "Work ID +
password login, issued by District Authority at assignment").

DESIGN DECISION FLAGGED (not spelled out further in PRD.md): the guide
calls this "separate from the X-User-Id authority scheme -- deliberately,
since agencies are external parties, not internal authorities." That is
true at the credential-issuance level -- an agency has no standing
account until a District Authority issues one, unlike the five
authority-tier accounts which are all pre-seeded -- but the underlying
login/session/permissions pipeline (auth/identity.py, auth/sessions.py,
auth/permissions.py) already works and is marked stable (Rules.md: don't
rewrite a module that's passed its exit check). Rather than build a
second, parallel identity system, this issues the Work ID as that
account's `x_user_id` in the same `users` table -- POST /auth/login
already accepts any x_user_id + password pair, so an issued agency
account logs in through the exact same endpoint the frontend already
calls (frontend/src/views/agency_dashboard.js's own comment already
anticipated this). "Work ID" is just the label this credential's
x_user_id takes, matching the WORK-<STATE>-<NNNNNN> shape used throughout
Demo-Credentials.md.

Generated passwords are returned once, in plaintext, to the issuing
District Authority -- same as any real credential-issuance flow (only
the hash is ever stored, auth/security.py). It is the District
Authority's responsibility to relay it to the agency out of band; this
system never displays it again after the issuing call returns.
"""
import secrets

from sqlalchemy.orm import Session as DBSession

from app.models import User
from app.auth.security import hash_password
from app.auth.permissions import ROLE_IMPLEMENTING_AGENCY

# ISO-ish 3-letter state code fallback used only to keep generated Work IDs
# readable (e.g. WORK-KER-483920) -- purely cosmetic, not a validated code
# list, since the dataset's own `state` column has no official-code column
# to draw from and inventing one would be exactly what Rules.md warns
# against ("do not invent MPLADS domain rules ... that aren't already
# specified").
def _state_prefix(state: str | None) -> str:
    if not state:
        return "GEN"
    letters = "".join(ch for ch in state.upper() if ch.isalpha())
    return (letters[:3] or "GEN").ljust(3, "X")


def _generate_work_id(state: str | None, db: DBSession) -> str:
    prefix = _state_prefix(state)
    while True:
        candidate = f"WORK-{prefix}-{secrets.randbelow(900_000) + 100_000}"
        if db.query(User).filter(User.x_user_id == candidate).first() is None:
            return candidate


def issue_agency_credentials(
    db: DBSession,
    *,
    executing_agency: str,
    implementing_district: str,
    state: str | None,
    issued_by: User,
    display_name: str | None = None,
) -> tuple[User, str]:
    """Creates a new Implementing Agency account scoped to
    (executing_agency, implementing_district) -- the same operating-unit
    key auth/permissions.py already uses for this role's jurisdiction
    (Build-Log.md #13) -- and returns (user, plain_password). Callers are
    responsible for a 403 check that `issued_by` is actually a District
    Authority; this function itself doesn't re-check that (kept a plain
    data operation, same separation routers/*.py already uses elsewhere:
    permissions logic lives in auth/permissions.py's require_roles(), not
    scattered into each helper it's called from)."""
    work_id = _generate_work_id(state, db)
    plain_password = secrets.token_urlsafe(9)  # readable-enough, ~12 chars

    user = User(
        x_user_id=work_id,
        name=display_name or f"{executing_agency} ({implementing_district})",
        role=ROLE_IMPLEMENTING_AGENCY,
        password_hash=hash_password(plain_password),
        executing_agency=executing_agency,
        district=implementing_district,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, plain_password
