"""
Router: messages -- the chat thread + formal notices on one work, and
file previews for what an agency uploaded.

  GET  /projects/{work_id}/messages            -- thread (chat + notices)
  POST /projects/{work_id}/messages            -- send chat (any role with
                                                   jurisdiction) or notice
                                                   (authority roles only)
  POST /projects/{work_id}/messages/{id}/ack   -- agency acknowledges a notice
  GET  /projects/{work_id}/files/{kind}/{id}   -- serve an uploaded photo/document
  GET  /notices/pending                        -- unacknowledged notices in
                                                   the caller's jurisdiction
                                                   (drives the alerts banner)

Same jurisdiction gate as every other single-work endpoint
(user_can_act_on_project). Registered ahead of projects.router in main.py
because projects' GET /{work_id:path} is greedy.
"""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.identity import get_current_user
from app.auth.permissions import (
    ROLE_IMPLEMENTING_AGENCY,
    filter_works_for_user,
    user_can_act_on_project,
)
from app.data_loader import get_all_works, get_work_by_id
from app.database import get_db
from app.models import Project, ProjectDocument, ProjectMessage, ProjectPhoto

router = APIRouter(tags=["messages"])

MAX_BODY_CHARS = 2000


class MessageCreate(BaseModel):
    kind: str = "chat"  # "chat" | "notice"
    body: str


def _gate(user, work_id: str) -> dict:
    work = get_work_by_id(work_id)
    if work is None:
        raise HTTPException(404, f"work_id '{work_id}' not found")
    if not user_can_act_on_project(user, work):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"'{user.role}' does not have jurisdiction over work_id '{work_id}'",
        )
    return work


def _ser(m: ProjectMessage) -> dict:
    return {
        "id": m.id,
        "work_id": m.work_id,
        "kind": m.kind,
        "sender_name": m.sender_name,
        "sender_role": m.sender_role,
        "body": m.body,
        "created_at": m.created_at.isoformat(),
        "acknowledged_at": m.acknowledged_at.isoformat() if m.acknowledged_at else None,
    }


@router.get("/notices/pending")
def pending_notices(user=Depends(get_current_user), db: Session = Depends(get_db)):
    all_works = get_all_works()
    my_ids = set(filter_works_for_user(user, all_works)["work_id"])
    rows = (
        db.query(ProjectMessage)
        .filter(ProjectMessage.kind == "notice", ProjectMessage.acknowledged_at.is_(None))
        .order_by(ProjectMessage.created_at.desc())
        .all()
    )
    return [_ser(m) for m in rows if m.work_id in my_ids]


@router.get("/projects/{work_id:path}/files/{kind}/{file_id}")
def get_file(work_id: str, kind: str, file_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(user, work_id)
    project = db.query(Project).filter(Project.work_id == work_id).first()
    if project is None or kind not in ("photos", "documents"):
        raise HTTPException(404, "file not found")
    model = ProjectPhoto if kind == "photos" else ProjectDocument
    row = db.query(model).filter(model.id == file_id, model.project_id == project.id).first()
    if row is None or not Path(row.file_path).exists():
        raise HTTPException(404, "file not found")
    return FileResponse(row.file_path, filename=row.original_filename or None)


@router.get("/projects/{work_id:path}/messages")
def list_messages(work_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(user, work_id)
    rows = (
        db.query(ProjectMessage)
        .filter(ProjectMessage.work_id == work_id)
        .order_by(ProjectMessage.created_at.asc())
        .all()
    )
    return [_ser(m) for m in rows]


@router.post("/projects/{work_id:path}/messages/{message_id}/ack")
def ack_notice(work_id: str, message_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(user, work_id)
    if user.role != ROLE_IMPLEMENTING_AGENCY:
        raise HTTPException(403, "Only the implementing agency can acknowledge a notice.")
    m = db.query(ProjectMessage).filter(
        ProjectMessage.id == message_id, ProjectMessage.work_id == work_id, ProjectMessage.kind == "notice"
    ).first()
    if m is None:
        raise HTTPException(404, "notice not found")
    if m.acknowledged_at is None:
        m.acknowledged_at = datetime.utcnow()
        m.acknowledged_by_user_id = user.id
        db.commit()
        db.refresh(m)
    return _ser(m)


@router.post("/projects/{work_id:path}/messages", status_code=status.HTTP_201_CREATED)
def post_message(work_id: str, body: MessageCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _gate(user, work_id)
    kind = body.kind.strip().lower()
    text = body.body.strip()
    if kind not in ("chat", "notice"):
        raise HTTPException(400, "kind must be 'chat' or 'notice'.")
    if not text:
        raise HTTPException(400, "Message cannot be empty.")
    if len(text) > MAX_BODY_CHARS:
        raise HTTPException(400, f"Message is too long (max {MAX_BODY_CHARS} characters).")
    if kind == "notice" and user.role == ROLE_IMPLEMENTING_AGENCY:
        raise HTTPException(403, "Only authorities can send a notice.")
    m = ProjectMessage(
        work_id=work_id, kind=kind, sender_user_id=user.id, sender_name=user.name,
        sender_role=user.role, body=text,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _ser(m)
