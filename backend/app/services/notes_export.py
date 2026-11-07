"""Export a saved note as a standalone Markdown file.

Notes live in SQLite because the app needs them searchable, but readers also
want a plain ``.md`` they can drop into a wiki or a knowledge-graph importer
(the "沉淀成 md 文档" part of the product idea). Files land under
``<data_dir>/notes/<project>/`` so they never pollute the code checkout.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.errors import InvalidInput, NotFound
from app.storage import repo

# Characters Windows refuses in a file name, plus the ones that make paths
# ambiguous when the note title is used as-is.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MAX_STEM = 80


def _safe_stem(title: str) -> str:
    stem = _BAD_CHARS.sub("_", str(title or "")).strip().strip(".")
    stem = re.sub(r"\s+", " ", stem)
    if not stem:
        stem = "笔记"
    return stem[:MAX_STEM].strip() or "笔记"


def notes_dir(project: dict[str, Any]) -> Path:
    name = _safe_stem(str(project.get("name") or "project"))
    return settings.data_dir / "notes" / name


def _unique_path(target: Path) -> Path:
    """Avoid overwriting an earlier export of a note with the same title."""
    if not target.exists():
        return target
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    candidate = target.with_name(f"{target.stem}-{stamp}{target.suffix}")
    index = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}-{stamp}-{index}{target.suffix}")
        index += 1
    return candidate


def render(note: dict[str, Any], project: dict[str, Any] | None) -> str:
    title = str(note.get("title") or "未命名笔记").strip()
    body = str(note.get("content_md") or "").strip()
    heading = title
    # ``content_md`` already starts with its own ``# 标题`` (the note body is
    # imported into other tools as-is), so drop it here to avoid a duplicate H1.
    if body.startswith("# "):
        heading = body.split("\n", 1)[0][2:].strip() or title
        body = body.split("\n", 1)[1].lstrip("\n") if "\n" in body else ""
    lines = [f"# {heading}", ""]
    if project is not None:
        lines.append(f"> 项目：{project.get('name')}（{project.get('path')}）")
    lines.append(f"> 导出时间：{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    if note.get("conversation_id"):
        lines.append("> 来源：CodeSlice 对话生成的笔记")
    lines.append("")
    if body:
        lines.append(body)
    else:
        lines.append("_（这条笔记还没有内容。）_")
    lines.append("")
    return "\n".join(lines)


def export_note(note_id: int) -> dict[str, Any]:
    note = repo.get_note(note_id)
    if note is None:
        raise NotFound("note not found")
    project = repo.get_project(int(note["project_id"]))

    directory = notes_dir(project or {"name": "project"})
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InvalidInput(f"cannot create export directory: {exc}") from None

    target = _unique_path(
        directory / f"{_safe_stem(note.get('file_name') or note.get('title') or '笔记')}.md"
    )
    try:
        target.write_text(render(note, project), encoding="utf-8")
    except OSError as exc:
        raise InvalidInput(f"cannot write export file: {exc}") from None

    return {
        "path": str(target),
        "directory": str(directory),
        "filename": target.name,
        "bytes": target.stat().st_size,
    }


__all__ = ["export_note", "notes_dir", "render"]
