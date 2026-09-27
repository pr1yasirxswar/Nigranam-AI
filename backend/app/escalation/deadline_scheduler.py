"""
Phase 12 item 6 -- deadline-based silent-delay auto-flagging.

"If a stage's task_deadline passes with no new StageSubmission on that
project, the system must flag 'time delay' on its own -- no new document,
no new AI score needed to trigger it, just the deadline itself"
(Implementation-Guide.md Phase 12 item 6, confirmed).

ASSUMPTION FLAGGED (per the guide -- proposing this is what "next report
due" means): the deadline checked against is the `task_deadline` the
agency itself set on the Project's most recent StageSubmission. If no
newer stage has been submitted by the time that date passes, it's
overdue. This module never re-scores anything (no AnalysisResult write,
no call into score_and_flag_work()) -- it raises a Flag directly via the
same create_flag() every other Medium+ finding already uses, with
driving_signal="stage_deadline_overdue", so it routes into the exact same
Local -> District -> State -> MP review chain (nothing new to build on
the review/escalation side, item 6).

Same stdlib-asyncio-background-loop pattern as the existing
escalation/scheduler.py -- no new task-queue dependency (Rules.md). Also
exposed via a manual POST /debug/run-deadline-scheduler trigger
(main.py), same "can't wait on a real window in a live demo" reasoning
escalation/scheduler.py's own docstring already gives.
"""
import asyncio
import logging
from datetime import datetime

from app.models import Project, StageSubmission, Flag
from app.escalation.state_machine import create_flag, ESCALATION_CHAIN

logger = logging.getLogger("sentinel.escalation.deadline_scheduler")

POLL_INTERVAL_SECONDS = 60 * 60  # 1 hour -- a polling-interval choice, not an MPLADS figure

DRIVING_SIGNAL = "stage_deadline_overdue"

# Silence looks like an execution problem (missed deadline, no report),
# not fabricated evidence -- same INEFFICIENCY-type bucket delay/progress
# already use (risk_aggregator.py's shape convention), so it renders as
# a triangle, not a circle.
_OVERDUE_RISK = {"tier": "High", "shape": "triangle", "driving_signal": DRIVING_SIGNAL}


def run_deadline_check_tick(db) -> list[str]:
    """
    One tick: finds every `Project` in `In Progress` status whose latest
    `StageSubmission`'s `task_deadline` has passed, and raises a Flag for
    each one that doesn't already have an open flag (same idempotency
    pattern app/scoring/pipeline.py's score_and_flag_work() already uses,
    so re-running this repeatedly never spams duplicate flags on a
    project already sitting in someone's review queue). Returns the
    work_ids flagged this tick.
    """
    now = datetime.utcnow()
    flagged_work_ids = []

    in_progress = db.query(Project).filter(Project.status == "In Progress").all()
    for project in in_progress:
        latest_stage = (
            db.query(StageSubmission)
            .filter(StageSubmission.project_id == project.id)
            .order_by(StageSubmission.submitted_at.desc())
            .first()
        )
        if latest_stage is None:
            continue  # no stage ever submitted -- outside this check's scope (item 6's flagged assumption)
        if latest_stage.task_deadline > now:
            continue  # not overdue yet

        existing = (
            db.query(Flag)
            .filter(Flag.work_id == project.work_id, Flag.status.in_(ESCALATION_CHAIN))
            .first()
        )
        if existing is not None:
            continue  # already has an open flag -- don't raise a second one

        create_flag(db, project.work_id, _OVERDUE_RISK)
        logger.info(
            "project %s auto-flagged: stage '%s' deadline %s has passed with no new submission",
            project.work_id, latest_stage.stage_label, latest_stage.task_deadline,
        )
        flagged_work_ids.append(project.work_id)

    return flagged_work_ids


async def deadline_background_loop(session_factory):
    """Runs run_deadline_check_tick() every POLL_INTERVAL_SECONDS, forever.
    Started via asyncio.create_task() from main.py's startup event,
    alongside the existing escalation_background_loop()."""
    while True:
        try:
            db = session_factory()
            try:
                run_deadline_check_tick(db)
            finally:
                db.close()
        except Exception:
            logger.exception("deadline scheduler tick failed")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
