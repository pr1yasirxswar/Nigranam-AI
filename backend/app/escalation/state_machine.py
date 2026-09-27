"""
Phase 4 -- flag lifecycle: open -> local_pending -> escalated_district ->
escalated_state -> escalated_mp (final/apex). Confirmed order:
Implementing Agency -> Local Authority (first review) -> District Authority
-> Nodal State Authority -> Member of Parliament (Rules.md, PRD.md S3).
This order was corrected twice in earlier drafts -- do not regress it.

"open" is the instantaneous, not-yet-routed moment the AI raises a flag --
every flag is routed to Local Authority immediately, so create_flag()
below lands a flag in STAGE_LOCAL_PENDING. The literal "open" state is
still written to the audit log as its own Review row (to_status="open"),
immediately followed by the routing Review row(s) (to_status=
"local_pending"), so the exact chain the docs describe is genuinely
present in the log, even though all of it happens inside the same call.
Phase 9 (PRD.md S5.4) made that "routing Review row" plural -- Local
Authority (accountable) and District Authority (copied for early
visibility) each get their own recipient_role-tagged row now, instead of
District only learning about a flag once it later escalates to them.

Flag.status is a denormalized "current stage" cache -- the real source of
truth is the append-only Review log (models.py). Never update or delete a
Review row (Rules.md); Flag.status changes only ever happen as a side
effect of inserting a new Review here.
"""
from datetime import datetime, timedelta

from app.models import Flag, Review

STAGE_OPEN = "open"
STAGE_LOCAL_PENDING = "local_pending"
STAGE_ESCALATED_DISTRICT = "escalated_district"
STAGE_ESCALATED_STATE = "escalated_state"
STAGE_ESCALATED_MP = "escalated_mp"
STAGE_RESOLVED = "resolved"  # terminal -- cleared by whichever tier held it

# Phase 12 item 7 -- a SEPARATE terminal-ish status from STAGE_RESOLVED,
# for the flowchart's "Contractor: Correct Defects & Resubmit" path. Not a
# delete (Rules.md forbids deleting flags/clearances) and not the same
# thing as a genuine "resolved" clearance -- a sent-back flag means the
# reviewing tier found a real problem and is bouncing it back to the
# agency for a corrected resubmission, which routers/reviews.py surfaces
# by flipping the corresponding StageSubmission.accepted to False (see
# its docstring). Like STAGE_RESOLVED, it's excluded from
# ESCALATION_CHAIN, so apply_action()'s "not in ESCALATION_CHAIN" guard
# already stops any further comment/clear/escalate on it for free.
STAGE_SENT_BACK = "sent_back"

# Ordered escalation chain: the stages a flag can actually sit "pending"
# in, each with a deadline and an accountable role. Excludes the
# transient STAGE_OPEN and the terminal STAGE_RESOLVED.
ESCALATION_CHAIN = [
    STAGE_LOCAL_PENDING,
    STAGE_ESCALATED_DISTRICT,
    STAGE_ESCALATED_STATE,
    STAGE_ESCALATED_MP,
]

# Which role is accountable (can clear / manually escalate) at each stage.
ROLE_FOR_STAGE = {
    STAGE_LOCAL_PENDING: "local_authority",
    STAGE_ESCALATED_DISTRICT: "district_authority",
    STAGE_ESCALATED_STATE: "nodal_state_authority",
    STAGE_ESCALATED_MP: "mp",
}

# Fixed 2-day response window per stage -- this figure IS already
# specified in PRD.md S4.7, not invented here.
RESPONSE_WINDOW = timedelta(days=2)

# Tiers that get a flag raised at all -- matches the Medium+ bar
# scripts/validate_phase2.py already uses, so "flagged" means the same
# thing everywhere in the codebase.
FLAGGED_TIERS = {"Critical", "High", "Medium"}


def next_stage(current_stage: str):
    """Next stage in the chain, or None if current_stage is already the
    last one (escalated_mp -- the apex; nothing above it, PRD.md S3)."""
    if current_stage not in ESCALATION_CHAIN:
        return None
    idx = ESCALATION_CHAIN.index(current_stage)
    if idx + 1 < len(ESCALATION_CHAIN):
        return ESCALATION_CHAIN[idx + 1]
    return None


def create_flag(db, work_id: str, risk: dict) -> Flag:
    """
    Raises a new flag for a work whose risk_aggregator tier is Medium+.
    In production this would run automatically as part of the scoring
    pipeline; it's exposed as POST /flags/{work_id}/raise (routers/flags.py)
    only because this build has no real background pipeline worker
    (Rules.md: no new third-party task-queue service added just for this).
    """
    now = datetime.utcnow()
    flag = Flag(
        work_id=work_id,
        status=STAGE_LOCAL_PENDING,
        tier=risk.get("tier"),
        shape=risk.get("shape"),
        driving_signal=risk.get("driving_signal"),
        stage_deadline=now + RESPONSE_WINDOW,
        created_at=now,
    )
    db.add(flag)
    db.flush()  # assigns flag.id before the Review rows below reference it

    db.add(Review(
        flag_id=flag.id, work_id=work_id, actor_user_id=None, actor_role="system",
        action="flagged", from_status=None, to_status=STAGE_OPEN,
        reason=f"AI risk aggregator flagged this work: tier={risk.get('tier')}, "
               f"driving_signal={risk.get('driving_signal')}",
        created_at=now,
    ))
    # Phase 9 (PRD.md S5.4's alert-routing note, Implementation-Guide.md
    # Phase 9 item 4) -- a Medium+ finding now writes TWO recipient rows
    # instead of one sequential relay: Local Authority (accountable for
    # first review, as before) AND District Authority (copied in for early
    # visibility), both landing the instant the flag is raised. Through
    # Phase 8 this was a single row, and District only learned of a flag
    # once/if it escalated to them days later. Neither new row changes
    # Flag.status or who is ACCOUNTABLE right now -- ROLE_FOR_STAGE still
    # says only Local Authority can clear/escalate at STAGE_LOCAL_PENDING;
    # District's copy here is read-only visibility, not an accountability
    # change (that still only happens via next_stage()/apply_action()
    # below when the flag genuinely escalates).
    db.add(Review(
        flag_id=flag.id, work_id=work_id, actor_user_id=None, actor_role="system",
        action="routed", from_status=STAGE_OPEN, to_status=STAGE_LOCAL_PENDING,
        recipient_role=ROLE_FOR_STAGE[STAGE_LOCAL_PENDING],
        reason="Routed to Local Authority for first-line review.",
        created_at=now,
    ))
    db.add(Review(
        flag_id=flag.id, work_id=work_id, actor_user_id=None, actor_role="system",
        action="routed", from_status=STAGE_OPEN, to_status=STAGE_LOCAL_PENDING,
        recipient_role=ROLE_FOR_STAGE[STAGE_ESCALATED_DISTRICT],
        reason="Copied to District Authority for early visibility -- not yet accountable "
               "for this flag unless/until it escalates.",
        created_at=now,
    ))
    db.commit()
    db.refresh(flag)
    return flag


def apply_action(db, flag: Flag, user, action: str, reason: str | None) -> Flag:
    """
    Applies a comment/clear/escalate action to a flag. The caller
    (routers/reviews.py) should already have checked the caller's role
    against ROLE_FOR_STAGE for clear/escalate before calling this, but the
    check here is the real enforcement point -- never rely on the router
    alone to gate a state-changing action.

    "comment" is intentionally open to ANY role whose jurisdiction covers
    the work (including Implementing Agency, and tiers above the current
    accountable one) -- it's communication, not a state change; the
    visibility cascade (PRD.md S4.8) is about being able to read/join that
    conversation, not just the currently-accountable tier owning it.
    """
    if flag.status not in ESCALATION_CHAIN:
        raise ValueError(f"flag {flag.id} is not in an actionable stage (status='{flag.status}')")

    if action in ("clear", "escalate", "send_back"):
        accountable_role = ROLE_FOR_STAGE[flag.status]
        if user.role != accountable_role:
            raise PermissionError(
                f"role '{user.role}' is not accountable for this flag's current stage "
                f"'{flag.status}' (requires '{accountable_role}')"
            )
        if not reason or not reason.strip():
            raise ValueError(f"a written justification is required to {action} a flag")

    from_status = flag.status
    now = datetime.utcnow()

    if action == "clear":
        flag.status = STAGE_RESOLVED
        flag.resolved_at = now
        to_status = STAGE_RESOLVED
    elif action == "escalate":
        nxt = next_stage(flag.status)
        if nxt is None:
            raise ValueError("cannot escalate further -- already at the apex tier (Member of Parliament)")
        flag.status = nxt
        flag.stage_deadline = now + RESPONSE_WINDOW
        to_status = nxt
    elif action == "send_back":
        # Phase 12 item 7 -- "Contractor: Correct Defects & Resubmit".
        # Distinct terminal-ish status from STAGE_RESOLVED (see
        # STAGE_SENT_BACK's own comment above) -- the Project-side effect
        # (flipping the corresponding StageSubmission.accepted to False,
        # so the agency sees it needs to resubmit) is applied by
        # routers/reviews.py right after this call, not here -- this
        # module owns Flag/Review only, never Project/StageSubmission
        # (Rules.md: don't blur "stable" modules' own responsibilities).
        flag.status = STAGE_SENT_BACK
        flag.resolved_at = now
        to_status = STAGE_SENT_BACK
    elif action == "comment":
        to_status = flag.status  # no state change
    else:
        raise ValueError(f"unknown action '{action}' (expected 'comment', 'clear', 'escalate', or 'send_back')")

    db.add(Review(
        flag_id=flag.id, work_id=flag.work_id, actor_user_id=user.id, actor_role=user.role,
        action=action, from_status=from_status, to_status=to_status, reason=reason,
        created_at=now,
    ))
    db.commit()
    db.refresh(flag)
    return flag


def auto_escalate(db, flag: Flag) -> Flag:
    """
    System-driven escalation when a stage's deadline has passed with no
    action taken -- called by escalation/scheduler.py, never by a user
    request directly. "Downgrades the previous authority to view/
    report-only on this work" (PRD.md S4.7) falls out for free: once
    flag.status moves past a tier, ROLE_FOR_STAGE no longer matches their
    role, so apply_action()'s accountable-role check naturally blocks them
    from clearing/escalating it further -- they can still read it (that's
    a jurisdiction/visibility question, handled in routers/reviews.py, not
    here).
    """
    if flag.status not in ESCALATION_CHAIN:
        return flag  # already resolved -- nothing to do

    now = datetime.utcnow()
    from_status = flag.status
    nxt = next_stage(flag.status)

    if nxt is None:
        # Already at the apex (MP) -- PRD.md defines nothing above MP, so
        # an overdue MP-tier flag just stays overdue. It still shows up in
        # reviewer_diligence.py's numbers for the "mp" role -- MP is
        # subject to the same "we watch the watchers" scrutiny as anyone
        # else in the hierarchy.
        return flag

    flag.status = nxt
    flag.stage_deadline = now + RESPONSE_WINDOW

    db.add(Review(
        flag_id=flag.id, work_id=flag.work_id, actor_user_id=None, actor_role="system",
        action="auto_escalate", from_status=from_status, to_status=nxt,
        reason=f"No action within the {RESPONSE_WINDOW.days}-day response window -- auto-escalated.",
        created_at=now,
    ))
    db.commit()
    db.refresh(flag)
    return flag
