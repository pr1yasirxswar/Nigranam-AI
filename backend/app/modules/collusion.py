"""
Phase 9 -- Authority-Agency Collusion Detection (PRD.md S4.9).

NOT a claim that any two named companies are colluding -- same caveat
agency_network.py already documents for Module C (PRD.md S4.2): the
dataset only carries 7 generic agency-TYPE categories, not distinct
company identities. What this module actually flags is a REVIEWER
pattern -- one specific authority ACCOUNT repeatedly clearing the same
recurring anomaly on the same agency category, in their own jurisdiction,
without it ever genuinely resolving. Same "watch the watchers" spirit as
escalation/reviewer_diligence.py, but narrower: reviewer_diligence.py
scores one role's overall clearance-rate/justification quality; this
module looks for a specific repeat-offender (authority, agency) PAIR.

Interface:
    check_and_record(db, work_id: str, user: User) -> CollusionAlert | None

    Call this right after a "clear" Review has been committed
    (routers/reviews.py) for the flag on work_id, passing the user who
    just cleared it. Recomputes this authority's own clearance history on
    the same agency category from the append-only Flag/Review log (same
    "derive from the audit trail, don't keep a second competing copy"
    pattern Flag.status already uses relative to Review, models.py) --
    no separate running-count table. If the just-cleared flag is the Nth
    consecutive recurrence of the same driving_signal by this same
    authority on this same agency (N >= OVERRIDE_THRESHOLD), inserts one
    new CollusionAlert row. Never updates/deletes a prior alert for the
    pair -- if one already reflects at least this much evidence, does
    nothing (idempotent re-checks are safe to call more than once).
"""
import json
from datetime import datetime

from app.models import Flag, Review, CollusionAlert
from app.data_loader import get_all_works
from app.escalation.state_machine import FLAGGED_TIERS

# Implementation-Guide.md Phase 9 item 1: "Validate the override-frequency
# threshold against the dataset/demo-walkthrough data before hardcoding it
# -- start from 2-3 consecutive overrides as a working default, confirm it
# doesn't over/under-fire, then lock it." Locked at 3 here to match the
# Phase 9 exit check's own walkthrough ("the same authority clears the
# same agency's flagged work 3 times in a row"). This has NOT yet been
# re-validated end-to-end against the live dataset/demo walkthrough (this
# sandbox has no way to run the Postgres-backed API -- see
# scripts/validate_phase9_collusion.py's standalone, DB-free check
# instead) -- flagging this explicitly rather than silently claiming it's
# locked for good (Rules.md: don't invent unvalidated domain figures).
OVERRIDE_THRESHOLD = 3


def _agency_for_work(work_id: str) -> str | None:
    all_works = get_all_works()
    row = all_works[all_works["work_id"] == work_id]
    if row.empty:
        return None
    value = row.iloc[0].get("executing_agency")
    return None if value is None or str(value) == "nan" else str(value)


def _clearance_history(db, authority_user_id: int, agency: str):
    """
    Every past 'clear' Review this SAME authority account has made on a
    Medium+ flag belonging to this SAME agency category, oldest first.
    Each entry is (review, flag) so callers can read both the clearance
    timestamp and the flag's driving_signal/tier/work_id.
    """
    all_works = get_all_works()
    agency_work_ids = set(all_works[all_works["executing_agency"] == agency]["work_id"])
    if not agency_work_ids:
        return []

    rows = (
        db.query(Review, Flag)
        .join(Flag, Review.flag_id == Flag.id)
        .filter(
            Review.action == "clear",
            Review.actor_user_id == authority_user_id,
            Flag.work_id.in_(agency_work_ids),
            Flag.tier.in_(FLAGGED_TIERS),
        )
        .order_by(Review.created_at.asc())
        .all()
    )
    return list(rows)


def _current_streak(history):
    """
    Length of the unbroken run of same-driving_signal clearances ending at
    the LAST entry in history (the clearance that was just made -- see
    check_and_record()'s call-order note). A different signal appearing
    anywhere in the run means that anomaly genuinely got resolved/changed
    at that point, so the streak restarts from there -- this is the "same
    or worse anomaly TYPE recurs" wording from Implementation-Guide.md
    Phase 9 item 1, read as: the recurring signal has to be the same
    TYPE each time (a "worse" tier on that same type still counts, a
    tier floor alone with no signal in common does not).
    """
    if not history:
        return 0
    streak = 1
    current_signal = history[-1][1].driving_signal
    for _review, flag in reversed(history[:-1]):
        if flag.driving_signal == current_signal:
            streak += 1
        else:
            break
    return streak


def check_and_record(db, work_id: str, user) -> CollusionAlert | None:
    flag = (
        db.query(Flag)
        .filter(Flag.work_id == work_id)
        .order_by(Flag.created_at.desc())
        .first()
    )
    if flag is None or flag.tier not in FLAGGED_TIERS:
        return None  # not a Medium+ flag -- outside this module's scope

    agency = _agency_for_work(work_id)
    if agency is None:
        return None

    history = _clearance_history(db, user.id, agency)
    streak = _current_streak(history)
    if streak < OVERRIDE_THRESHOLD:
        return None

    existing = (
        db.query(CollusionAlert)
        .filter(CollusionAlert.authority_id == user.id, CollusionAlert.agency_id == agency)
        .order_by(CollusionAlert.override_count.desc())
        .first()
    )
    if existing is not None and existing.override_count >= streak:
        return None  # already have an alert reflecting at least this much evidence

    related_work_ids = [f.work_id for _r, f in history[-streak:]]
    signal_label = history[-1][1].driving_signal or "unspecified anomaly"

    alert = CollusionAlert(
        authority_id=user.id,
        agency_id=agency,
        reason=(
            f"{user.name} ({user.role.replace('_', ' ')}) has cleared {agency}'s flagged work "
            f"{streak} times in a row on the same '{signal_label}' signal, without it being resolved."
        ),
        related_work_ids=json.dumps(related_work_ids),
        override_count=streak,
        created_at=datetime.utcnow(),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert
