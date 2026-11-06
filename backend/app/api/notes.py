"""Notes, code anchors and command rules."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import ProjectDep, TokenDep
from app.core.errors import NotFound
from app.services import anchors, notegen, notes_export, notes_index
from app.storage import repo

router = APIRouter(tags=["notes"])
log = logging.getLogger(__name__)


class NoteCreate(BaseModel):
    project_id: int
    title: str = Field(min_length=1)
    content_md: str = ""
    conversation_id: str | None = None
    folder_id: int | None = None


class NoteUpdate(BaseModel):
    title: str | None = None
    content_md: str | None = None
    folder_id: int | None = None
    move_folder: bool = False


class FolderCreate(BaseModel):
    name: str = Field(min_length=1)
    parent_id: int | None = None


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    move: bool = False


def _with_index_state(notes: list[dict[str, Any]], project_id: int | None = None) -> list[dict[str, Any]]:
    """Attach ``indexed`` so the UI can show the "已入知识库" badge.

    The knowledge base lists notes from several projects at once, so the indexed
    chunk set is resolved per project instead of assuming one.
    """
    project_ids = {project_id} if project_id is not None else {int(n["project_id"]) for n in notes}
    indexed: set[int] = set()
    for pid in project_ids:
        if pid is None:
            continue
        try:
            indexed |= notes_index.indexed_note_ids(int(pid))
        except Exception:  # a missing chunk table must not hide the notes
            log.warning("note index state unavailable", exc_info=True)
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
def list_notes(
    _: TokenDep,
    project_id: int | None = None,
    folder_id: int | None = None,
    unfiled: bool = False,
) -> list[dict[str, Any]]:
    """List notes in the knowledge base.

    Without ``project_id`` this spans **every** project: a note about a library is
    as useful in the next repo as it was in the one that produced it. Passing
    ``project_id`` keeps the old per-project behaviour for the panel's project
    filter. ``folder_id`` / ``unfiled`` narrow to one place in the folder tree.
    """
    if project_id is None:
        notes = repo.list_all_notes(folder_id=folder_id, unfiled=unfiled)
    else:
        notes = [
            note for note in repo.list_notes(project_id) if (folder_id is None or note.get("folder_id") == folder_id)
        ]
    return _with_index_state(notes)


@router.get("/notes/{note_id}")
def get_note(note_id: int, _: TokenDep) -> dict[str, Any]:
    """One note with its index state — the reader pane reloads through this."""
    note = repo.get_note(note_id)
    if note is None:
        raise NotFound("note not found")
    return _with_index_state([note], int(note["project_id"]))[0]


@router.post("/notes/{note_id}/retitle")
def retitle_note(note_id: int, _: TokenDep) -> dict[str, Any]:
    """Rewrite a legacy "关于 X 第 N-M 行" title as a knowledge point.

    A model failure returns the unchanged note with ``retitle_failed`` instead of
    an error: the user still has their note, they just have to title it by hand.
    """
    return notegen.retitle(note_id)


@router.post("/notes")
def create_note(body: NoteCreate, _: TokenDep) -> dict[str, Any]:
    if repo.get_project(body.project_id) is None:
        raise NotFound("project not found")
    note = repo.create_note(
        body.project_id,
        body.title,
        body.content_md,
        body.conversation_id,
        folder_id=body.folder_id,
    )
    if note is None:  # pragma: no cover - defensive
        raise NotFound("note not found")
    return _index_note(note)


@router.put("/notes/{note_id}")
def update_note(note_id: int, body: NoteUpdate, _: TokenDep) -> dict[str, Any]:
    note = repo.update_note(
        note_id,
        body.title,
        body.content_md,
        folder_id=body.folder_id,
        move_folder=body.move_folder,
    )
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
# knowledge-base folders
# --------------------------------------------------------------------------- #
@router.get("/note-folders")
def list_note_folders(_: TokenDep) -> list[dict[str, Any]]:
    """The user's own tree for the knowledge base."""
    return repo.list_note_folders()


@router.post("/note-folders")
def create_note_folder(body: FolderCreate, _: TokenDep) -> dict[str, Any]:
    return repo.create_note_folder(body.name, body.parent_id)


@router.put("/note-folders/{folder_id}")
def update_note_folder(folder_id: int, body: FolderUpdate, _: TokenDep) -> dict[str, Any]:
    folder = repo.rename_note_folder(folder_id, body.name, body.parent_id, move=body.move)
    if folder is None:
        raise NotFound("folder not found")
    return folder


@router.delete("/note-folders/{folder_id}")
def delete_note_folder(folder_id: int, _: TokenDep) -> dict[str, bool]:
    """Notes inside fall back to 未分类 instead of being deleted with the folder."""
    repo.delete_note_folder(folder_id)
    return {"ok": True}


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
