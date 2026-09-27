"""
Router: auth.
  POST /auth/login         -- Phase 8 bug fix (Implementation-Guide.md
                               Phase 8 item 6): the ONLY way any session
                               token is issued. Checks x_user_id + password
                               against the seeded account and its
                               password_hash; wrong/missing credentials get
                               a plain 401, nothing else happens.
  POST /auth/logout        -- deletes the caller's session server-side.
  GET  /auth/me            -- who the caller's session resolves to
                               (requires a valid session, same as every
                               other gated endpoint).
  GET  /auth/demo-accounts -- convenience list of x_user_id/name/role for a
                               frontend login form's account picker.
                               Deliberately does NOT include passwords --
                               those are demo-published in
                               Demo-Credentials.md, not served by the API.

Phase 8 bug fix note: there used to be no real login form "by design" --
Build-Log.md #19 found that this let every dashboard route bypass login
entirely, so Implementation-Guide.md Phase 8 item 6 treats it as a bug,
not a design choice, going forward. Full 4-accounts-per-role credentials
are Phase 11 scope (auth/agency_auth.py, seed_users.py expansion); this
router only adds the login/session mechanics needed to make the existing
one-account-per-role seed set actually gate access.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models import User
from app.auth.identity import get_current_user
from app.auth.security import verify_password
from app.auth.sessions import create_session, delete_session
from app.auth.seed_users import DEMO_USERS

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    x_user_id: str
    password: str


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.x_user_id == payload.x_user_id).first()
    # Same generic error whether the account doesn't exist or the password
    # is wrong -- don't let /auth/login be used to enumerate valid
    # x_user_id values.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect account or password.",
        )
    session = create_session(db, user)
    return {
        "token": session.token,
        "expires_at": session.expires_at.isoformat(),
        "user": {"x_user_id": user.x_user_id, "name": user.name, "role": user.role},
    }


@router.post("/logout")
def logout(
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
):
    if authorization and authorization.lower().startswith("bearer "):
        delete_session(db, authorization[len("bearer "):].strip())
    return {"status": "logged_out"}


@router.get("/me")
def whoami(user=Depends(get_current_user)):
    return {
        "x_user_id": user.x_user_id,
        "name": user.name,
        "role": user.role,
        "jurisdiction": {
            "state": user.state,
            "district": user.district,
            "loc": user.loc,
            "executing_agency": user.executing_agency,
            "constituency": user.constituency,
        },
    }


@router.get("/demo-accounts")
def list_demo_accounts():
    """
    Convenience list of x_user_id/name/role, originally for a frontend
    login form's account picker (Phase 5, revised Phase 8 item 6).

    Phase 12 (user-flagged practicality issue): the frontend no longer
    calls this -- main.js's login form now only takes a typed ID +
    password (no dropdown of valid ids), because a real login form should
    never let a visitor browse which account ids exist. This endpoint is
    kept, unused by the UI, purely as a reference for the team/demo docs
    (Demo-Credentials.md already publishes the same list) -- not deleted
    per Rules.md, but should not be reintroduced into any login UI, and a
    real deployment would remove or auth-gate it entirely.
    """
    return [
        {"x_user_id": u["x_user_id"], "name": u["name"], "role": u["role"]}
        for u in DEMO_USERS
    ]
