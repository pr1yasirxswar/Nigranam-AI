"""
Phase 3 -- get_current_user(), the single dependency every gated router
uses to resolve "who is calling."

Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6 / Build-Log.md
#19 finding 2): this used to trust a bare `X-User-Id` header as
identity -- any client could claim to be any seeded account with no
credential check at all, so every authority/agency dashboard route was
reachable with no real login. It now requires a session token minted by
a successful `POST /auth/login` (auth/sessions.py) and looks the user up
through that session, never through a client-supplied user id. This is
the server-side half of the fix; main.js/api.js is the frontend half
(login form + redirect-to-login on 401), Rules.md/PRD.md S4.11: the
frontend enforces nothing, it just reflects what the backend allows.

Still a documented pre-production placeholder overall (no refresh
tokens, no rotation, no rate limiting on /auth/login) -- see Rules.md
and Demo-Credentials.md for what's explicitly deferred to a real
deployment.
"""
from fastapi import Header, HTTPException, status, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth.sessions import get_user_for_token


def get_current_user(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
        description="Bearer session token issued by POST /auth/login -- see Demo-Credentials.md",
    ),
    db: Session = Depends(get_db),
) -> User:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[len("bearer "):].strip()

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="login_required: no session token -- log in via POST /auth/login first",
        )

    user = get_user_for_token(db, token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="login_required: session is missing, invalid, or expired -- log in again",
        )
    return user
