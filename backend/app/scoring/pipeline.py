"""
Phase 11 fix -- shared scoring+flagging pipeline.

Root cause of "no flag cases show up anywhere": the only thing that ever
ran the 8-signal risk aggregator + created a Flag row was
POST /flags/{work_id}/raise (routers/flags.py) -- and nothing called it
for any work, let alone all ~5,940. In production this pipeline would run
automatically (create_flag()'s own docstring says so); this build never
had that automatic trigger, so every dashboard's flag queries were
correctly returning "no rows" against an empty flags table.

This module is the single place that does "score one work, persist the
AnalysisResult, raise a Flag if Medium+" -- moved out of routers/flags.py
so routers/flags.py's /raise endpoint AND the new bulk /debug/run-full-
scoring endpoint (main.py) call the exact same code path and can never
drift out of sync with each other (same principle auth/permissions.py's
docstring already applies to its own single-source-of-truth check).
"""
import json
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import Flag, AnalysisResult
from app.modules.duplicate import score_duplicate, evidence_duplicate
from app.modules.money import score_money, evidence_money
from app.modules.progress import score_progress
from app.modules.delay import score_delay, evidence_delay
from app.modules.agency_network import score_agency_network
from app.modules.photo_forensics import score_photo_forensics, evidence_photo_forensics
from app.modules.weather_check import score_weather_check
from app.scoring.isolation_forest import score_work_isolation_forest
from app.scoring.risk_aggregator import aggregate_risk
from app.escalation.state_machine import create_flag, ESCALATION_CHAIN, FLAGGED_TIERS


def score_work(work: dict, all_works) -> dict:
    """Runs all 8 signals + the aggregator on one work. Same shape flags.py
    used to compute inline (routers/flags.py's old _score_work).

    Phase 14 (Evidence Layer) -- additive: alongside each module's 0-1
    float, also collects the underlying evidence dict for the four
    modules that have concrete, human-readable numbers to show
    (money/delay/duplicate/photo_forensics -- STAGE-ANALYSIS-v5.md's own
    stage list). progress/network/isolation_forest/weather don't get a
    dedicated evidence_x() -- their scores still feed the tier/driving-
    signal exactly as before, this only adds what the detail page can
    additionally display when one of the four evidenced modules is the
    driving signal (or just scored above 0)."""
    scores = {
        "duplicate": score_duplicate(work, all_works),
        "money": score_money(work, all_works),
        "progress": score_progress(work),
        "delay": score_delay(work, all_works),
        "network": score_agency_network(work, all_works),
        "isolation_forest": score_work_isolation_forest(work, all_works),
        "photo_forensics": score_photo_forensics(work, all_works),
        "weather": score_weather_check(work),
    }
    evidence = {
        "duplicate": evidence_duplicate(work, all_works),
        "money": evidence_money(work, all_works),
        "delay": evidence_delay(work, all_works),
        "photo_forensics": evidence_photo_forensics(work, all_works),
    }
    return aggregate_risk(scores, evidence)


def persist_analysis_result(db: Session, work_id: str, risk: dict) -> None:
    """Upserts the latest-scoring cache row -- this is what lets 'no row'
    mean 'never analyzed' instead of looking like a false-negative clean
    result (PRD.md S7 item 2).

    Uses an atomic ON CONFLICT upsert rather than query-then-add/update:
    the bulk pass can re-score the same work_id in quick succession, and
    a plain 'check then insert' has a race window that throws a
    UniqueViolation on ix_analysis_results_work_id under concurrent runs."""
    evidence_json = json.dumps(risk.get("evidence", {}), default=str)
    now = datetime.utcnow()

    stmt = pg_insert(AnalysisResult).values(
        work_id=work_id,
        tier=risk["tier"],
        shape=risk["shape"],
        driving_signal=risk["driving_signal"],
        evidence=evidence_json,
        scored_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[AnalysisResult.work_id],
        set_={
            "tier": risk["tier"],
            "shape": risk["shape"],
            "driving_signal": risk["driving_signal"],
            "evidence": evidence_json,
            "scored_at": now,
        },
    )
    db.execute(stmt)
    db.commit()


def score_and_flag_work(db: Session, work_id: str, work: dict, all_works, force: bool = False) -> dict:
    """
    Scores one work and raises a Flag if warranted. Returns a small result
    dict describing what happened -- used by both the single-work /raise
    endpoint and the bulk runner below.

    force=False (default) skips re-scoring a work that already has an open
    flag, so re-running the bulk pass is idempotent and won't spam
    duplicate flags on works already sitting in someone's queue.
    """
    if not force:
        existing = db.query(Flag).filter(Flag.work_id == work_id, Flag.status.in_(ESCALATION_CHAIN)).first()
        if existing:
            return {"work_id": work_id, "created": False, "tier": existing.tier,
                    "detail": "an open flag already exists for this work"}

    risk = score_work(work, all_works)
    persist_analysis_result(db, work_id, risk)

    if risk["tier"] not in FLAGGED_TIERS:
        return {"work_id": work_id, "created": False, "tier": risk["tier"],
                "detail": f"tier '{risk['tier']}' is below the Medium+ flagging bar"}

    flag = create_flag(db, work_id, risk)
    return {"work_id": work_id, "created": True, "status": flag.status, "tier": flag.tier}


def run_full_scoring(db: Session, all_works, force: bool = False) -> dict:
    """
    The missing piece: scores EVERY work in the dataset and raises Flags
    for every Medium+ one, in one pass. This is what a real deployment's
    automatic pipeline trigger would do continuously (create_flag()'s
    docstring); here it's a single callable both the /debug endpoint
    (manual trigger, same "no new task-queue dependency" pattern as
    escalation/scheduler.py's /debug/run-escalation-scheduler) and, if
    wired up later, a startup/background job can call.

    Returns a summary: how many works were scored, how many new flags were
    created, and a tier breakdown -- so the caller (and whoever's staring
    at the API response) can see at a glance that it actually ran against
    the whole dataset, not just one row.
    """
    tier_counts: dict[str, int] = {}
    flags_created = 0
    scored = 0
    skipped_existing = 0

    for work in all_works.to_dict(orient="records"):
        work_id = work["work_id"]
        result = score_and_flag_work(db, work_id, work, all_works, force=force)

        if result.get("detail") == "an open flag already exists for this work":
            skipped_existing += 1
            continue

        scored += 1
        tier = result.get("tier")
        if tier:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if result.get("created"):
            flags_created += 1

    return {
        "total_works": len(all_works),
        "scored": scored,
        "skipped_existing_flags": skipped_existing,
        "flags_created": flags_created,
        "tier_counts": tier_counts,
    }