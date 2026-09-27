"""
Router: projects.
  GET /projects           -- public, unfiltered (Phase 5's public
                              dashboard/registry views read from this;
                              explicitly "no login required", PRD.md S5).
  GET /projects/mine       -- Phase 3: jurisdiction-scoped to the logged-in
                              user, filtered server-side (Rules.md).
  GET /projects/{work_id}  -- Phase 3: one work; 403 if the caller's
                              jurisdiction doesn't cover it.

Phase 12 item 1/3 adds (all new code, additive only -- see each
endpoint's own docstring):
  POST /projects                    -- MP creates a real, DB-backed
                                        Project (status "Recommended").
  POST /projects/{work_id}/sanction -- District Authority sanctions it.
  POST /projects/{work_id}/stages   -- Implementing Agency submits a
                                        stage report; scored immediately
                                        by the existing 8-signal pipeline.
  GET  /projects/{work_id}/stages   -- jurisdiction-gated stage history.

The only touch to the THREE existing endpoints above is item 1's
data-source swap: load_works() -> get_all_works(), so a real Project
created here shows up in them with zero other code changes.
"""
import json
import logging
import secrets
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import settings
from app.data_loader import get_all_works, get_work_by_id
from app.database import get_db
from app.auth.identity import get_current_user
from app.auth.permissions import (
    filter_works_for_user,
    require_roles,
    user_can_act_on_project,
    ROLE_DISTRICT_AUTHORITY,
    ROLE_IMPLEMENTING_AGENCY,
    ROLE_MP,
)
from app.models import AnalysisResult, Project, ProjectDocument, ProjectPhoto, StageSubmission, User
from app.schemas import ProjectCreate, ProjectSanction
from app.modules.live_photo_check import check_and_score, extract_photo_metadata
from app.scoring.pipeline import score_and_flag_work

router = APIRouter(prefix="/projects", tags=["projects"])
logger = logging.getLogger("sentinel.routers.projects")

# Public/registry-appropriate fields -- deliberately excludes dev-only
# columns like seeded_anomaly_type/is_seeded_anomaly (those are for
# /debug/* endpoints only, never a public response).
_PUBLIC_FIELDS = [
    "work_id", "state", "constituency", "mp_name", "implementing_district",
    "village_or_locality", "executing_agency", "work_category",
    "work_description", "status", "physical_progress_pct",
    "sanctioned_amount", "expenditure",
]


def _serialize(work: dict) -> dict:
    return {k: work.get(k) for k in _PUBLIC_FIELDS}


@router.get("")
def list_all_projects():
    all_works = get_all_works()
    return [_serialize(row) for row in all_works.to_dict(orient="records")]


@router.get("/mine")
def list_my_projects(user=Depends(get_current_user)):
    all_works = get_all_works()
    scoped = filter_works_for_user(user, all_works)
    return [_serialize(row) for row in scoped.to_dict(orient="records")]


@router.get("/{work_id:path}/stages")  # declared ahead of the bare
# GET /{work_id:path} catch-all below -- see the NOTE further down this
# file (where this route used to live) for why: :path is greedy/anchored,
# so a more specific suffix route must be registered before the catch-all
# or it's silently unreachable.
def list_stages(work_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Phase 12 item 3 -- jurisdiction-gated, same user_can_act_on_project()
    check as GET /projects/{work_id} below. A synthetic-CSV work_id
    (no live Project row) simply has no stage history -- returns []
    rather than 404, since the work_id itself is valid."""
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{user.role}' does not have jurisdiction over work_id '{work_id}'",
        )

    project = db.query(Project).filter(Project.work_id == work_id).first()
    if project is None:
        return []
    stages = (
        db.query(StageSubmission)
        .filter(StageSubmission.project_id == project.id)
        .order_by(StageSubmission.submitted_at.asc())
        .all()
    )
    return [_serialize_stage(s, db) for s in stages]


@router.get("/{work_id:path}/evidence")  # same reason as /stages above --
# must be declared ahead of the bare catch-all.
def get_project_evidence(work_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Phase 14 (Evidence Layer) -- combines the raw work fields, the AI
    pipeline's per-module evidence (the money/delay/duplicate/photo-
    forensics numbers behind this work's tier + driving_signal, persisted
    by scoring/pipeline.py's persist_analysis_result()), and this work's
    stage submissions (photos/documents, via list_stages() above) into one
    payload for the detail-analysis page. Same jurisdiction gate as
    GET /projects/{work_id} -- no new access rule.

    Returns tier/shape/driving_signal/evidence as null/{} (not a 404) for
    a work_id that's valid but has never been scored -- same "pending
    analysis vs clear vs flagged" honesty principle
    GET /flags/analysis/public already established (PRD.md S7 item 2).
    """
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{user.role}' does not have jurisdiction over work_id '{work_id}'",
        )

    analysis = db.query(AnalysisResult).filter(AnalysisResult.work_id == work_id).first()
    evidence = json.loads(analysis.evidence) if analysis and analysis.evidence else {}

    return {
        "work_id": work_id,
        "work": _serialize(work),
        "tier": analysis.tier if analysis else None,
        "shape": analysis.shape if analysis else None,
        "driving_signal": analysis.driving_signal if analysis else None,
        "scored_at": analysis.scored_at.isoformat() if analysis else None,
        "evidence": evidence,
        "stages": list_stages(work_id, user, db),
    }


@router.get("/{work_id:path}")  # :path -- work_id values contain literal
# "/" (e.g. "MPLADS/MAH/2021/000382"); the default str converter stops at
# the first "/" and 404s. Found while smoke-testing Phase 5's Review Panel
# against real work_ids -- flagged here since this is a fix to
# already-"stable" Phase-3 code (Rules.md), not a rewrite of its logic.
# Declared AFTER /stages and /evidence above -- see their own comments.
def get_project(work_id: str, user=Depends(get_current_user)):
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(status_code=404, detail=f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{user.role}' user '{user.x_user_id}' does not have jurisdiction over work_id '{work_id}'",
        )
    return _serialize(work)


# ---------------------------------------------------------------------------
# Phase 12 item 1/3 -- new code below this line.
# ---------------------------------------------------------------------------

_PROJECT_FIELDS = _PUBLIC_FIELDS + [
    "nodal_district", "recommendation_date", "sanction_date",
    "expected_completion_date", "actual_completion_date",
    "latitude", "longitude", "data_type",
]


def _serialize_project(project: Project) -> dict:
    out = {}
    for f in _PROJECT_FIELDS:
        v = getattr(project, f)
        out[f] = v.isoformat() if isinstance(v, (datetime, date)) else v
    return out


def _serialize_stage(stage: StageSubmission, db: Session) -> dict:
    photos = db.query(ProjectPhoto).filter(ProjectPhoto.stage_submission_id == stage.id).all()
    documents = db.query(ProjectDocument).filter(ProjectDocument.stage_submission_id == stage.id).all()
    return {
        "id": stage.id,
        "stage_label": stage.stage_label,
        "task_deadline": stage.task_deadline.isoformat(),
        "submitted_at": stage.submitted_at.isoformat(),
        "physical_progress_pct": stage.physical_progress_pct,
        "expenditure_this_stage": stage.expenditure_this_stage,
        "ai_tier": stage.ai_tier,
        "ai_driving_signal": stage.ai_driving_signal,
        "accepted": stage.accepted,
        "photo_count": len(photos),
        "document_count": len(documents),
        "photos": [{"id": p.id, "filename": p.original_filename} for p in photos],
        "documents": [{"id": d.id, "filename": d.original_filename} for d in documents],
    }


def _get_db_project_or_404(db: Session, work_id: str) -> Project:
    project = db.query(Project).filter(Project.work_id == work_id).first()
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"work_id '{work_id}' is not a live (user-submitted) project -- "
                   "it may be a read-only synthetic-CSV work_id instead",
        )
    return project


def _generate_project_work_id(db: Session) -> str:
    """MPLADS/USR/<year>/<seq> -- "USR" marks this as a user-submitted,
    real project (Project.data_type already says the same thing on the
    row itself; this is just so the id is recognizable at a glance,
    matching the CSV's own MPLADS/<STATE>/<year>/<seq> convention). Random
    suffix + uniqueness retry, same pattern auth/agency_auth.py's
    _generate_work_id() already uses for issued agency credentials."""
    year = datetime.utcnow().year
    while True:
        candidate = f"MPLADS/USR/{year}/{secrets.randbelow(900_000) + 100_000:06d}"
        if db.query(Project).filter(Project.work_id == candidate).first() is None:
            return candidate


def _mp_state(user, all_works) -> str | None:
    """An MP's own jurisdiction key is `constituency` (auth/permissions.py),
    not `state` directly -- looked up the same way agencies.py's
    _caller_state() already does for other roles: via any existing work
    (CSV or DB) that shares this constituency."""
    match = all_works[all_works["constituency"] == user.constituency]
    return None if match.empty else match["state"].iloc[0]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    user=Depends(require_roles(ROLE_MP)),
    db: Session = Depends(get_db),
):
    """
    Phase 12 item 3 -- Member of Parliament recommends a new, real
    project. `mp_name`/`constituency`/`state` are defaulted from the
    caller's own jurisdiction (never client-supplied -- ProjectCreate's
    docstring). Status starts at "Recommended" (item 2's role mapping);
    a District Authority sanctions it next (sanction_project() below).
    """
    if not body.work_category.strip() or not body.work_description.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "work_category and work_description are required.")
    if body.estimated_cost <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "estimated_cost must be positive.")

    all_works = get_all_works()
    state = _mp_state(user, all_works)

    expected_completion = None
    if body.expected_completion_date:
        try:
            expected_completion = date.fromisoformat(body.expected_completion_date)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected_completion_date must be an ISO date (YYYY-MM-DD).")

    project = Project(
        work_id=_generate_project_work_id(db),
        mp_name=user.name,
        state=state,
        constituency=user.constituency,
        nodal_district=body.nodal_district or body.implementing_district,
        implementing_district=body.implementing_district,
        work_category=body.work_category.strip(),
        work_description=body.work_description.strip(),
        executing_agency=None,
        village_or_locality=body.village_or_locality,
        estimated_cost=body.estimated_cost,
        sanctioned_amount=None,
        expenditure=0.0,
        recommendation_date=datetime.utcnow(),
        sanction_date=None,
        expected_completion_date=expected_completion,
        actual_completion_date=None,
        physical_progress_pct=0.0,
        status="Recommended",
        latitude=body.latitude,
        longitude=body.longitude,
        data_type="USER_SUBMITTED",
        created_by_user_id=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


@router.post("/{work_id:path}/sanction")
def sanction_project(
    work_id: str,
    body: ProjectSanction,
    user=Depends(require_roles(ROLE_DISTRICT_AUTHORITY)),
    db: Session = Depends(get_db),
):
    """
    Phase 12 item 3 -- District Authority sanctions a "Recommended"
    project, naming an executing_agency directly (item 2's flagged
    assumption: bidding/tender is deferred). The named agency must
    already hold Implementing Agency credentials issued in the caller's
    own district (POST /agencies/issue-credentials, Phase 11 item 3) --
    otherwise there'd be no account able to log in and submit stages
    against this project at all.
    """
    project = _get_db_project_or_404(db, work_id)
    if project.implementing_district != user.district:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"District Authority '{user.x_user_id}' does not have jurisdiction over "
            f"implementing_district '{project.implementing_district}'",
        )
    if project.status != "Recommended":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"project status is '{project.status}' -- can only sanction a 'Recommended' project",
        )

    agency_user = (
        db.query(User)
        .filter(
            User.role == ROLE_IMPLEMENTING_AGENCY,
            User.executing_agency == body.executing_agency,
            User.district == user.district,
        )
        .first()
    )
    if agency_user is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"no Implementing Agency credential for '{body.executing_agency}' in district "
            f"'{user.district}' -- issue one first via POST /agencies/issue-credentials",
        )

    if body.sanctioned_amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "sanctioned_amount must be positive.")

    if body.expected_completion_date:
        try:
            project.expected_completion_date = date.fromisoformat(body.expected_completion_date)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected_completion_date must be an ISO date (YYYY-MM-DD).")

    project.executing_agency = body.executing_agency
    project.sanctioned_amount = body.sanctioned_amount
    project.sanction_date = datetime.utcnow()
    project.status = "Sanctioned"
    db.commit()
    db.refresh(project)
    return _serialize_project(project)


def _save_upload(work_id: str, stage_label: str, kind: str, filename: str, data: bytes) -> str:
    """Local-disk storage only -- no new paid/third-party service (Rules.md).
    Path: {upload_dir}/{work_id sanitized}/{stage_label sanitized}/{kind}/{filename}."""
    safe_work_id = work_id.replace("/", "_")
    safe_stage = "".join(c for c in stage_label if c.isalnum() or c in (" ", "-", "_")).strip() or "stage"
    safe_filename = Path(filename or "upload").name  # strips any path components
    directory = Path(settings.upload_dir) / safe_work_id / safe_stage / kind
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / f"{secrets.token_hex(4)}_{safe_filename}"
    dest.write_bytes(data)
    return str(dest)


@router.post("/{work_id:path}/stages", status_code=status.HTTP_201_CREATED)
async def submit_stage(
    work_id: str,
    stage_label: str = Form(...),
    task_deadline: date = Form(...),
    physical_progress_pct: float = Form(...),
    expenditure_this_stage: float = Form(...),
    photos: list[UploadFile] = File(default=[]),
    documents: list[UploadFile] = File(default=[]),
    user=Depends(require_roles(ROLE_IMPLEMENTING_AGENCY)),
    db: Session = Depends(get_db),
):
    """
    Phase 12 item 3 -- Implementing Agency submits one stage report
    against a project it's assigned to. On success: creates the
    StageSubmission row, updates the Project's running
    physical_progress_pct/expenditure/status, then immediately calls the
    EXISTING score_and_flag_work() pipeline against the updated project
    row -- no new scoring logic, same call every CSV work already goes
    through (item 3).

    Item 5 -- every photo is pre-checked BEFORE anything is saved; if any
    one of them clears the hard reject threshold, the whole request is
    rejected (422) with nothing persisted at all (agency re-submits).
    """
    project = _get_db_project_or_404(db, work_id)

    if user.executing_agency != project.executing_agency or user.district != project.implementing_district:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Implementing Agency '{user.x_user_id}' is not assigned to project '{work_id}'",
        )
    if project.status not in ("Sanctioned", "In Progress"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"project status is '{project.status}' -- not open for stage submissions",
        )
    if not (0 <= physical_progress_pct <= 100):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "physical_progress_pct must be between 0 and 100.")
    if expenditure_this_stage < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "expenditure_this_stage cannot be negative.")

    # Item 5: read + pre-check every photo BEFORE anything is persisted.
    prior_phashes = [
        p.phash for p in
        db.query(ProjectPhoto).filter(ProjectPhoto.project_id == project.id).all()
        if p.phash
    ]
    photo_payloads = []  # (filename, bytes, metadata) for photos that passed
    for upload in photos:
        data = await upload.read()
        if not data:
            continue
        metadata = extract_photo_metadata(data)
        result = check_and_score(
            metadata=metadata,
            project_lat=project.latitude,
            project_lon=project.longitude,
            sanction_date=project.sanction_date,
            prior_phashes=prior_phashes,
        )
        if result["reject"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=result["reason"])
        photo_payloads.append((upload.filename, data, metadata))
        if metadata["phash"]:
            prior_phashes.append(metadata["phash"])  # so two photos in the SAME upload can't collide either

    document_payloads = []
    for upload in documents:
        data = await upload.read()
        if data:
            document_payloads.append((upload.filename, data))

    # All checks passed -- now persist.
    stage = StageSubmission(
        project_id=project.id,
        stage_label=stage_label.strip() or "Stage",
        task_deadline=datetime.combine(task_deadline, datetime.min.time()),
        physical_progress_pct=physical_progress_pct,
        expenditure_this_stage=expenditure_this_stage,
        submitted_by_user_id=user.id,
    )
    db.add(stage)
    db.flush()  # assigns stage.id before ProjectPhoto/ProjectDocument reference it

    last_photo_metadata = None
    for filename, data, metadata in photo_payloads:
        file_path = _save_upload(work_id, stage.stage_label, "photos", filename, data)
        db.add(ProjectPhoto(
            project_id=project.id, stage_submission_id=stage.id, file_path=file_path,
            original_filename=filename, gps_lat=metadata["gps_lat"], gps_lon=metadata["gps_lon"],
            captured_at=metadata["captured_at"], phash=metadata["phash"],
        ))
        last_photo_metadata = metadata

    for filename, data in document_payloads:
        file_path = _save_upload(work_id, stage.stage_label, "documents", filename, data)
        db.add(ProjectDocument(
            project_id=project.id, stage_submission_id=stage.id,
            file_path=file_path, original_filename=filename,
        ))

    # Update the Project's running totals/status (item 1).
    project.physical_progress_pct = physical_progress_pct
    project.expenditure = (project.expenditure or 0.0) + expenditure_this_stage
    if project.status == "Sanctioned":
        project.status = "In Progress"
    if last_photo_metadata is not None:
        # Denormalized cache get_all_works() reads (models.py's Project docstring).
        project.latest_photo_available = True
        project.latest_photo_gps_lat = last_photo_metadata["gps_lat"]
        project.latest_photo_gps_lon = last_photo_metadata["gps_lon"]
        project.latest_photo_captured_at = last_photo_metadata["captured_at"]
        project.latest_photo_phash = last_photo_metadata["phash"]

    db.commit()
    db.refresh(stage)
    db.refresh(project)

    # Item 3: immediately run the EXISTING scoring+flagging pipeline
    # against the updated project row -- same call path every CSV work
    # already goes through (app/scoring/pipeline.py), no new scoring
    # logic here.
    all_works = get_all_works()
    work = get_work_by_id(work_id)
    # Default force=False, same call every CSV work already goes through
    # (app/scoring/pipeline.py) -- if an earlier stage's flag on this work
    # is still unresolved, this correctly skips raising a second one
    # rather than duplicating it (score_and_flag_work's own idempotency
    # guard, unchanged).
    result = score_and_flag_work(db, work_id, work, all_works)

    # score_and_flag_work()'s own return dict never carries a
    # "driving_signal" key in any of its branches (app/scoring/
    # pipeline.py) -- read it from the AnalysisResult row that call just
    # wrote instead, rather than reaching into pipeline.py to add one
    # (Rules.md: don't rewrite a module marked stable).
    analysis = db.query(AnalysisResult).filter(AnalysisResult.work_id == work_id).first()
    stage.ai_tier = result.get("tier")
    stage.ai_driving_signal = analysis.driving_signal if analysis else None
    db.commit()

    return {
        "stage": _serialize_stage(stage, db),
        "project": _serialize_project(project),
        "scoring_result": result,
    }

# NOTE: GET /{work_id:path}/stages used to be declared here, AFTER the
# bare GET /{work_id:path} catch-all near the top of this file. Bug found
# while wiring Phase 14 (Evidence Layer)'s own new GET .../evidence route:
# :path is greedy and anchors to end-of-string, so the earlier-declared
# catch-all was matching "<work_id>/stages" in FULL as its own work_id
# (get_work_by_id() then 404s, since no such literal work_id exists) --
# this route was silently unreachable. Moved above the catch-all,
# alongside the new evidence route, both now declared before it -- same
# "declare specific routes ahead of the greedy :path catch-all" pattern
# routers/flags.py already documents for its own /public, /mine,
# /diligence/{role}, /analysis/public routes.
