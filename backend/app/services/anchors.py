"""Code anchors: bind a conversation to the exact code it was discussing, and
keep that binding meaningful after the file changes.

Status semantics (from docs/ARCHITECTURE.md):
  unchanged  the recorded line range still exists and the content hash matches
  modified   the file exists but the recorded snippet no longer matches
  moved      the same content hash was found elsewhere in the file
  deleted    the file is gone (the snippet snapshot is preserved)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from app.core.errors import InvalidInput, NotFound
from app.services import paths
from app.storage import repo


def _hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def extract_snippet(root: Path, rel: str, start_line: int, end_line: int) -> str | None:
    try:
        path = paths.resolve_in_project(root, rel)
    except InvalidInput:
        return None
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    start = max(1, int(start_line))
    end = min(len(lines), int(end_line))
    if start > len(lines):
        return None
    return "\n".join(lines[start - 1 : end])


def create_for_citation(
    project_id: int,
    conversation_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
    symbol: str | None = None,
) -> dict[str, Any] | None:
    project = repo.get_project(project_id)
    if project is None:
        return None
    root = Path(project["path"])
    snippet = extract_snippet(root, file_path, start_line, end_line)
    return repo.create_anchor(
        project_id=project_id,
        conversation_id=conversation_id,
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        symbol=symbol,
        snippet=snippet,
        code_hash=_hash(snippet) if snippet else None,
    )


def refresh(project_id: int) -> dict[str, int]:
    project = repo.get_project(project_id)
    if project is None:
        raise NotFound("project not found")
    root = paths.resolve_root(project["path"])
    stats = {"updated": 0, "deleted": 0, "moved": 0, "unchanged": 0}

    for anchor in repo.list_anchors(project_id):
        rel = anchor["file_path"]
        try:
            path = paths.resolve_in_project(root, rel)
        except InvalidInput:
            path = None

        if path is None or not path.is_file():
            stats["deleted"] += 1
            if anchor["status"] != "deleted":
                repo.update_anchor(anchor["id"], status="deleted", checked_at=_now())
            continue

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        start, end = int(anchor["start_line"]), int(anchor["end_line"])
        current = "\n".join(lines[start - 1 : min(end, len(lines))]) if start <= len(lines) else ""
        current_hash = _hash(current) if current else ""

        if anchor["code_hash"] and current_hash == anchor["code_hash"]:
            # Count every verified anchor, not only the ones that changed state:
            # the UI shows "N 个锚点已核对" and a 0 there looks broken.
            stats["unchanged"] += 1
            if anchor["status"] != "unchanged":
                repo.update_anchor(anchor["id"], status="unchanged", checked_at=_now())
            continue

        # Try to find the original content elsewhere in the file.
        snippet = anchor["snippet"] or ""
        lowered = [line.strip() for line in lines]
        target = [line.strip() for line in snippet.splitlines() if line.strip()]
        found_at: int | None = None
        if target:
            head = target[0]
            for idx, line in enumerate(lowered):
                if line != head:
                    continue
                window = lowered[idx : idx + len(target)]
                if window == target:
                    found_at = idx + 1
                    break

        if found_at is not None and anchor["code_hash"]:
            repo.update_anchor(
                anchor["id"],
                status="moved",
                start_line=found_at,
                end_line=found_at + len(target) - 1,
                checked_at=_now(),
            )
            stats["moved"] += 1
        else:
            repo.update_anchor(anchor["id"], status="modified", checked_at=_now())
            stats["updated"] += 1
    return stats


def _now() -> str:
    from app.core.db import utcnow

    return utcnow()
