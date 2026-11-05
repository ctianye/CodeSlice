"""Notes, code anchors and command rules."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ProjectDep, TokenDep
from app.core.errors import NotFound
from app.services import anchors, notes_export, notes_index
from app.storage import repo

router = APIRouter(tags=["notes"])
log = logging.getLogger(__name__)


class NoteCreate(BaseModel):
    project_id: int
    title: str = Field(min_length=1)
    content_md: str = ""
    conversation_id: str | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None


def _with_index_state(notes: list[dict[str, Any]], project_id: int) -> list[dict[str, Any]]:
    """Attach ``indexed`` so the UI can show the "已入知识库" badge."""
    try:
        indexed = notes_index.indexed_note_ids(project_id)
    except Exception:  # a missing chunk table must not hide the notes
        log.warning("note index state unavailable", exc_info=True)
        indexed = set()
    return [{**note, "indexed": int(note["id"]) in indexed} for note in notes]


def _index_note(note: dict[str, Any]) -> dict[str, Any]:
    """Keep the retrieval row in step with the saved note; never fail the write."""
    try:
        notes_index.sync_note(note)
        indexed = True
    except Exception:
        log.warning("note index sync failed for %s", note.get("id"), exc_info=True)
        indexed = False
    return {**note, "indexed": indexed}


@router.get("/notes")
def list_notes(_: TokenDep, project_id: int) -> list[dict[str, Any]]:
    return _with_index_state(repo.list_notes(project_id), project_id)


@router.post("/notes")
def create_note(body: NoteCreate, _: TokenDep) -> dict[str, Any]:
    if repo.get_project(body.project_id) is None:
        raise NotFound("project not found")
    note = repo.create_note(body.project_id, body.title, body.content_md, body.conversation_id)
    if note is None:  # pragma: no cover - defensive
        raise NotFound("note not found")
    return _index_note(note)


@router.put("/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdate, _: TokenDep) -> dict[str, Any]:
    note = repo.update_note(note_id, body.title, body.content_md)
    if note is None:
        raise NotFound("note not found")
    return _index_note(note)


@router.delete("/notes/{note_id}")
def delete_note(note_id: int, _: TokenDep) -> dict[str, bool]:
    repo.delete_note(note_id)
    try:
        notes_index.remove_note_chunk(note_id)
    except Exception:  # deleting the note is what the user asked for; index is derived
        log.warning("note chunk removal failed for %s", note_id, exc_info=True)
    return {"ok": True}


@router.post("/notes/{note_id}/export")
def export_note(note_id: int, _: TokenDep) -> dict[str, Any]:
    return notes_export.export_note(note_id)


# --------------------------------------------------------------------------- #
# anchors
# --------------------------------------------------------------------------- #
@router.get("/projects/{project_id}/anchors")
def list_anchors(project: ProjectDep, _: TokenDep, conversation_id: str | None = None) -> list[dict[str, Any]]:
    return repo.list_anchors(project["id"], conversation_id)


@router.post("/projects/{project_id}/anchors/refresh")
def refresh_anchors(project: ProjectDep, _: TokenDep) -> dict[str, int]:
    return anchors.refresh(project["id"])


# --------------------------------------------------------------------------- #
# command rules
# --------------------------------------------------------------------------- #
class RuleCreate(BaseModel):
    pattern: str = Field(min_length=1)
    scope: str = "once"
    needs_confirm: bool = True


class RuleUpdate(BaseModel):
    pattern: str | None = None
    scope: str | None = None
    needs_confirm: bool | None = None


@router.get("/projects/{project_id}/command-rules")
def list_rules(project: ProjectDep, _: TokenDep) -> list[dict[str, Any]]:
    return repo.list_rules(project["id"])


@router.post("/projects/{project_id}/command-rules")
def create_rule(project: ProjectDep, body: RuleCreate, _: TokenDep) -> dict[str, Any]:
    return repo.create_rule(project["id"], body.pattern, body.scope, body.needs_confirm)


@router.put("/projects/{project_id}/command-rules/{rule_id}")
def update_rule(project: ProjectDep, rule_id: int, body: RuleUpdate, _: TokenDep) -> dict[str, Any]:
    rule = repo.update_rule(rule_id, **body.model_dump(exclude_none=True))
    if rule is None:
        raise NotFound("rule not found")
    return rule


@router.delete("/projects/{project_id}/command-rules/{rule_id}")
def delete_rule(project: ProjectDep, rule_id: int, _: TokenDep) -> dict[str, bool]:
    repo.delete_rule(rule_id)
    return {"ok": True}
