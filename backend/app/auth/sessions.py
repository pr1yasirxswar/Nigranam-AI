"""
Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6) -- real
server-side sessions. Created only by routers/auth.py's POST /auth/login
after a password check; checked on every request by
auth/identity.py:get_current_user(). See models.py:Session's docstring
for why this is the fix for Build-Log.md #19 finding 2 (dashboards
bypassing login entirely).
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session as DBSession

from app.models import Session, User

# 12 hours -- long enough for one demo/judging session, short enough that a
# leaked token from an earlier run doesn't linger forever. Not spelled out
# in PRD.md; flagged as a reasonable default, not an invented domain rule.
SESSION_TTL = timedelta(hours=12)


def create_session(db: DBSession, user: User) -> Session:
    token = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    session = Session(token=token, user_id=user.id, created_at=now, expires_at=now + SESSION_TTL)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_user_for_token(db: DBSession, token: str) -> User | None:
    if not token:
        return None
    session = db.query(Session).filter(Session.token == token).first()
    if session is None:
        return None
    if session.expires_at < datetime.utcnow():
        # Expired -- clean it up rather than leaving dead rows around.
        db.delete(session)
        db.commit()
        return None
    return db.query(User).filter(User.id == session.user_id).first()


def delete_session(db: DBSession, token: str) -> None:
    db.query(Session).filter(Session.token == token).delete()
    db.commit()
