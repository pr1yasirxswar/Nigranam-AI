"""
Phase 4 -- background job for the fixed 2-day response window; auto-
escalates any flag whose current stage's deadline has passed.

No new third-party task-queue library (Celery, APScheduler, etc.) was
added for this -- Rules.md says not to add new services without asking
first. This uses only stdlib asyncio, started as a background task from
FastAPI's own startup event (main.py) -- no new dependency.

For live-demo purposes, waiting on a real 2-day window obviously isn't
practical (Implementation-Guide.md's Phase 4 exit check needs the full
escalation chain to play out in one sitting), so run_escalation_tick() is
also exposed directly via POST /debug/run-escalation-scheduler (main.py) --
call it to force a check immediately rather than waiting for the next
scheduled tick or backdating a flag's deadline in the database.
"""
import asyncio
import logging
from datetime import datetime

from app.models import Flag
from app.escalation.state_machine import auto_escalate, ESCALATION_CHAIN

logger = logging.getLogger("sentinel.escalation.scheduler")

# How often the background loop checks for overdue flags, in seconds. This
# is a polling interval choice, not an MPLADS figure -- PRD.md's 2-day
# response window is unaffected by how often we poll for it.
POLL_INTERVAL_SECONDS = 60 * 60  # 1 hour


def run_escalation_tick(db) -> list[int]:
    """
    One tick: finds every actionable flag whose current stage's deadline
    has passed and auto-escalates it. Returns the ids of flags that were
    escalated, for logging / the debug endpoint's response.
    """
    now = datetime.utcnow()
    overdue = (
        db.query(Flag)
        .filter(Flag.status.in_(ESCALATION_CHAIN))
        .filter(Flag.stage_deadline <= now)
        .all()
    )
    escalated_ids = []
    for flag in overdue:
        before = flag.status
        auto_escalate(db, flag)
        logger.info("flag %s auto-escalated %s -> %s (overdue)", flag.id, before, flag.status)
        escalated_ids.append(flag.id)
    return escalated_ids


async def escalation_background_loop(session_factory):
    """
    Runs run_escalation_tick() every POLL_INTERVAL_SECONDS, forever.
    Started via asyncio.create_task() from main.py's startup event.
    """
    while True:
        try:
            db = session_factory()
            try:
                run_escalation_tick(db)
            finally:
                db.close()
        except Exception:
            logger.exception("escalation scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
