from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import asyncio
import logging
import os
from app.data_loader import get_all_works, get_work_by_id, load_works
from app.modules.duplicate import score_duplicate
from app.modules.money import score_money
from app.modules.progress import score_progress
from app.modules.delay import score_delay
from app.modules.agency_network import score_agency_network
from app.modules.photo_forensics import score_photo_forensics
from app.modules.weather_check import score_weather_check
from app.scoring.isolation_forest import score_work_isolation_forest
from app.scoring.risk_aggregator import aggregate_risk
from app.database import Base, engine, SessionLocal, get_db
from app.auth.seed_users import seed_users
from app.routers import projects, auth, flags, reviews, messages, agencies, stats, collusion, mp_compare, public
from app.escalation.scheduler import escalation_background_loop, run_escalation_tick
from app.escalation.deadline_scheduler import deadline_background_loop, run_deadline_check_tick
from app.scoring.pipeline import run_full_scoring
from app.guards import require_debug_access
from sqlalchemy import text

app = FastAPI(title="NIGRANAM-AI API")

# Bug fix: CORSMiddleware used to be added TWICE, both hardcoded to
# localhost:5173, which blocks a deployed frontend entirely. One middleware
# now; origins come from CORS_ORIGINS (comma-separated, e.g. your Vercel
# production URL). Trailing slashes are stripped because browsers send the
# Origin header without one and an exact-match list would otherwise never
# match. CORS_ORIGIN_REGEX optionally allows Vercel preview deployments,
# e.g. https://d-mplads-.*\\.vercel\\.app
_default_origins = "http://localhost:5173,https://localhost:5173"
allowed_origins = [
    o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", _default_origins).split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=os.environ.get("CORS_ORIGIN_REGEX") or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _run_full_scoring_sync():
    """Blocking work (pandas + sklearn + Postgres writes over ~5,940 rows)
    -- run off the event loop via run_in_executor below, never awaited
    directly, so it doesn't stall every other request while it runs."""
    df = get_all_works()
    db = SessionLocal()
    try:
        summary = run_full_scoring(db, df, skip_analyzed=True)
        logging.getLogger("sentinel.startup").info(
            "startup full-scoring pass: %s", summary
        )
        return summary
    finally:
        db.close()


@app.on_event("startup")
async def on_startup():
    # Phase 3: create Postgres tables if they don't exist yet, then seed
    # demo accounts (idempotent -- safe on every boot).
    Base.metadata.create_all(bind=engine)
    seed_users()
    # Phase 4: start the background auto-escalation loop. stdlib asyncio
    # only -- no new task-queue dependency (Rules.md).
    asyncio.create_task(escalation_background_loop(SessionLocal))
    # Phase 12 item 6: start the silent-delay auto-flagging loop, same
    # stdlib-asyncio pattern, no new task-queue dependency.
    asyncio.create_task(deadline_background_loop(SessionLocal))

    # Root-cause fix: nothing was ever calling the detection pipeline for
    # any work -- POST /flags/{work_id}/raise (routers/flags.py) only ever
    # scored one work_id at a time, and nothing looped it over the whole
    # dataset, so every dashboard's flag queries were correctly returning
    # zero rows against an empty `flags` table. This is the "automatic
    # pipeline trigger a real deployment would have" that
    # escalation/state_machine.py's create_flag() docstring already says
    # is missing. score_and_flag_work() skips any work that already has an
    # open flag (app/scoring/pipeline.py), so re-running this on every
    # restart is idempotent -- it won't spam duplicate flags.
    # Runs in a thread (run_in_executor) so the ~5,940-row pass doesn't
    # block the event loop / incoming requests while it scores.
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_full_scoring_sync)

@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Bug fix: this used to call get_all_works(), which queries Postgres and
    concatenates ~19.5k rows on EVERY hit -- and Render's health check plus
    the frontend's startup ping both call it. Now: one cheap `SELECT 1` (also
    keeps a Supabase project from idling) + the cached CSV row count.
    Returns 503 if the database is unreachable so Render can restart."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "rows_loaded": len(load_works())}

# Phase 1 exit check (Implementation-Guide.md): temporary endpoint that runs
# all four independent scoring modules on one work and returns their raw,
# un-aggregated scores. Aggregation into a single severity tier is Phase 2 --
# do not add aggregation logic here.
@app.get("/debug/score/{work_id}", dependencies=[Depends(require_debug_access)])
def debug_score(work_id: str):
    all_works = get_all_works()
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    return {
        "work_id": work_id,
        "status": work.get("status"),
        "seeded_anomaly_type": work.get("seeded_anomaly_type"),  # dev-only, for eyeballing
        "scores": {
            "duplicate": score_duplicate(work, all_works),
            "money": score_money(work, all_works),
            "progress": score_progress(work),
            "delay": score_delay(work, all_works),
        },
    }


# Phase 2 exit check (Implementation-Guide.md): full risk breakdown for one
# work -- all raw signals (four Phase-1 modules + agency_network +
# isolation_forest, plus photo_forensics + weather added in Phase 6), plus
# the aggregated severity tier and shape. This supersedes /debug/score as
# the richer debug endpoint, but /debug/score is left in place since it's
# still useful for eyeballing the raw Phase-1 signals on their own.
@app.get("/debug/risk/{work_id}", dependencies=[Depends(require_debug_access)])
def debug_risk(work_id: str):
    all_works = get_all_works()
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")

    module_scores = {
        "duplicate": score_duplicate(work, all_works),
        "money": score_money(work, all_works),
        "progress": score_progress(work),
        "delay": score_delay(work, all_works),
        "network": score_agency_network(work, all_works),
        "isolation_forest": score_work_isolation_forest(work, all_works),
        "photo_forensics": score_photo_forensics(work, all_works),
        "weather": score_weather_check(work),
    }
    risk = aggregate_risk(module_scores)

    return {
        "work_id": work_id,
        "status": work.get("status"),
        "seeded_anomaly_type": work.get("seeded_anomaly_type"),  # dev-only, for eyeballing
        **risk,
    }

# Phase 3 routers:
# reviews.router is registered ahead of projects.router: projects'
# GET /{work_id:path} is deliberately greedy (fixed while smoke-testing
# Phase 5 -- work_id values contain literal "/"), so it would otherwise
# swallow "/projects/<id>/reviews" whole before reviews.router ever saw
# it. FastAPI/Starlette matches routes in registration order.
app.include_router(reviews.router)
app.include_router(messages.router)  # before projects.router -- greedy {work_id:path}
app.include_router(projects.router)
app.include_router(auth.router)

# Phase 4 routers:
app.include_router(flags.router)

# Phase 5 routers (frontend-dashboard support):
app.include_router(agencies.router)
app.include_router(stats.router)

# Phase 9 router (collusion detection, PRD.md S4.9):
app.include_router(collusion.router)

# Phase 10 router ("MP dashboard 2" cross-MP comparison, PRD.md S5.6):
app.include_router(mp_compare.router)

# Phase 11 router (public OTP-verify + area lookup, PRD.md S4.10):
app.include_router(public.router)

# Phase 4: manual trigger for the escalation scheduler -- a live demo can't
# wait on a real 2-day window (see escalation/scheduler.py's docstring).
@app.post("/debug/run-escalation-scheduler", dependencies=[Depends(require_debug_access)])
def debug_run_escalation_scheduler(db: Session = Depends(get_db)):
    escalated_ids = run_escalation_tick(db)
    return {"escalated_flag_ids": escalated_ids, "count": len(escalated_ids)}


# Phase 12 item 6: manual trigger for the silent-delay auto-flagging
# scheduler -- same "can't wait on a real window in a live demo" reasoning
# as the escalation scheduler's own debug endpoint above.
@app.post("/debug/run-deadline-scheduler", dependencies=[Depends(require_debug_access)])
def debug_run_deadline_scheduler(db: Session = Depends(get_db)):
    flagged_work_ids = run_deadline_check_tick(db)
    return {"flagged_work_ids": flagged_work_ids, "count": len(flagged_work_ids)}


# Root-cause fix: manual trigger for the full detection pipeline, in case
# the startup pass (on_startup above) needs to be re-run on demand -- e.g.
# after the CSV changes, or if you want a fresh count without restarting
# the server. force=true re-scores and re-persists AnalysisResult even for
# works that already have an open flag (it still won't create a SECOND
# open flag on top of an existing one -- score_and_flag_work only ever
# checks "is there already an open flag", it doesn't duplicate).
@app.post("/debug/run-full-scoring", dependencies=[Depends(require_debug_access)])
def debug_run_full_scoring(force: bool = False, db: Session = Depends(get_db)):
    df = get_all_works()
    return run_full_scoring(db, df, force=force)
