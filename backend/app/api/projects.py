"""Projects, file tree, file content and git endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import ProjectDep, TokenDep
from app.core.config import settings
from app.core.errors import InvalidInput, NotFound
from app.indexing.indexer import clear_index
from app.indexing.languages import detect_language
from app.services import git_service, paths
from app.storage import repo

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[])


class ProjectCreate(BaseModel):
    path: str = Field(min_length=1)
    name: str | None = None


@router.post("")
def create_project(body: ProjectCreate, _: TokenDep) -> dict[str, Any]:
    root = paths.resolve_root(body.path)
    existing = repo.find_project_by_path(str(root))
    if existing:
        repo.touch_project(existing["id"])
        return existing
    name = (body.name or root.name or str(root)).strip()
    return repo.create_project(name, str(root))


@router.get("")
def list_projects(_: TokenDep) -> list[dict[str, Any]]:
    return repo.list_projects()


@router.get("/{project_id}")
def get_project(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    return project


@router.delete("/{project_id}")
def delete_project(project: ProjectDep, _: TokenDep) -> dict[str, bool]:
    clear_index(project["id"])
    repo.delete_project(project["id"])
    return {"ok": True}


def _build_tree(directory: Path, root: Path, depth: int, level: int = 0) -> list[dict[str, Any]]:
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError as exc:
        raise InvalidInput(f"cannot read directory: {exc}") from None

    out: list[dict[str, Any]] = []
    for entry in entries:
        if len(out) >= settings.max_tree_entries:
            break
        if entry.name in paths.DEFAULT_IGNORES or entry.name.startswith(".git"):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        rel = entry.relative_to(root).as_posix()
        node: dict[str, Any] = {"name": entry.name, "path": rel, "is_dir": is_dir, "children": None}
        if is_dir and level + 1 < depth:
            node["children"] = _build_tree(entry, root, depth, level + 1)
        out.append(node)
    return out


@router.get("/{project_id}/tree")
def get_tree(
    project: ProjectDep,
    _: TokenDep,
    path: str = Query(default=""),
    depth: int = Query(default=2, ge=0, le=4),
) -> list[dict[str, Any]]:
    root = paths.resolve_root(project["path"])
    directory = paths.resolve_in_project(root, path)
    if not directory.is_dir():
        raise InvalidInput(f"not a directory: {path}")
    if depth <= 0:
        return []
    return _build_tree(directory, root, depth)


@router.get("/{project_id}/file")
def get_file(project: ProjectDep, _: TokenDep, path: str = Query(min_length=1)) -> dict[str, Any]:
    root = paths.resolve_root(project["path"])
    target = paths.resolve_in_project(root, path)
    if not target.is_file():
        raise NotFound(f"file not found: {path}")
    stat = target.stat()
    truncated = stat.st_size > settings.max_file_bytes
    with target.open("rb") as handle:
        raw = handle.read(settings.max_file_bytes)
    text = raw.decode("utf-8", errors="replace")
    rel = target.relative_to(root).as_posix()
    return {
        "path": rel,
        "language": detect_language(rel, text),
        "content": text,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "truncated": truncated,
    }


@router.get("/{project_id}/git/status")
def git_status(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    root = paths.resolve_root(project["path"])
    try:
        return git_service.current_state(root)
    except Exception:
        return {
            "is_repo": False,
            "branch": None,
            "head": None,
            "dirty": False,
            "staged": [],
            "modified": [],
            "untracked": [],
        }


@router.get("/{project_id}/git/diff")
def git_diff(project: ProjectDep, _: TokenDep, path: str = Query(default="")) -> dict[str, Any]:
    root = paths.resolve_root(project["path"])
    rel = None
    if path:
        rel = paths.resolve_in_project(root, path).relative_to(root).as_posix()
    return {"path": rel, "diff": git_service.diff(root, rel)}


@router.post("/{project_id}/open")
def open_project(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    repo.touch_project(project["id"])
    return repo.get_project(project["id"]) or project
