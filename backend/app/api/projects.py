"""Projects, file tree, file content and git endpoints."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.api.deps import ProjectDep, TokenDep
from app.core.config import settings
from app.core.errors import InvalidInput, NotFound
from app.indexing.indexer import clear_index, start_index_job, stop_project_watcher, watch_project
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
    stop_project_watcher(project["id"])
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


class FileWrite(BaseModel):
    path: str = Field(min_length=1)
    content: str
    # 打开文件时拿到的 mtime；给了就要做冲突检测
    expected_mtime: float | None = None


# Markdown 预览里 <img src> 只能走查询参数鉴权，允许的图片后缀。
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}
MAX_ASSET_BYTES = 8 * 1024 * 1024


@router.put("/{project_id}/file")
def put_file(project: ProjectDep, _: TokenDep, body: FileWrite) -> dict[str, Any]:
    """Write a text file back to disk and return the same shape as ``GET /file``."""
    root = paths.resolve_root(project["path"])
    target = paths.resolve_in_project(root, body.path)
    if not target.exists():
        raise NotFound(f"file not found: {body.path}")
    if target.is_dir():
        raise InvalidInput(f"not a file: {body.path}")

    size = len(body.content.encode("utf-8"))
    if size > settings.max_file_bytes:
        raise InvalidInput(f"file too large: {size} bytes")

    if body.expected_mtime is not None and abs(target.stat().st_mtime - body.expected_mtime) > 0.001:
        raise InvalidInput("file changed on disk since it was opened")

    # newline="" 保证内容原样落盘：不做换行转换、不自动补尾换行。
    target.write_text(body.content, encoding="utf-8", newline="")
    stat = target.stat()
    rel = target.relative_to(root).as_posix()
    repo.touch_project(project["id"])
    return {
        "path": rel,
        "language": detect_language(rel, body.content),
        "content": body.content,
        "size": size,
        "mtime": stat.st_mtime,
        "truncated": False,
    }


@router.get("/{project_id}/asset")
def get_asset(
    project: ProjectDep,
    token: str | None = Query(default=None),
    path: str = Query(min_length=1),
) -> Response:
    """Serve a raw image for Markdown ``<img src>`` tags.

    The renderer cannot attach ``X-Session-Token`` to an image request, so this
    endpoint accepts the token as a query parameter instead.
    """
    if not token or token != settings.token:
        raise HTTPException(status_code=401, detail="invalid token")

    root = paths.resolve_root(project["path"])
    target = paths.resolve_in_project(root, path)
    if not target.is_file():
        raise NotFound(f"file not found: {path}")

    suffix = target.suffix.lower()
    if suffix not in ASSET_SUFFIXES:
        raise InvalidInput(f"unsupported asset type: {suffix}")

    size = target.stat().st_size
    if size > MAX_ASSET_BYTES:
        raise InvalidInput(f"asset too large: {size} bytes")

    raw = target.read_bytes()
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(content=raw, media_type=media_type, headers={"Cache-Control": "no-cache"})


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
    watch_project(project["id"])
    start_index_job(project["id"])
    return repo.get_project(project["id"]) or project
