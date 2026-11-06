"""Command sandbox for the integrated terminal.

Everything the terminal is allowed to do lives here: the dangerous-fragment
denylist, working-directory resolution and a small in-memory history ring that
both the user terminal and the agent's ``run_command`` tool write to.

The history is deliberately *not* persisted: it is a convenience buffer for the
panel, and a command line can contain secrets (tokens passed with ``-H``), so it
must not survive a restart in a plain SQLite file.
"""

from __future__ import annotations

import collections
import logging
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.errors import InvalidInput
from app.services import paths

log = logging.getLogger(__name__)

# Commands that are never allowed, no matter who asks for them. Shared with the
# agent's ``run_command`` tool so user and AI hit the same denylist.
BLOCKED_FRAGMENTS: tuple[str, ...] = (
    "rm -rf /",
    "format ",
    "shutdown",
    "reboot",
    "diskpart",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    "mkfs",
    ":(){",
)

OUTPUT_TAIL_LINES = 6
OUTPUT_TAIL_CHARS = 2000
HISTORY_LIMIT = 200

_LOCK = threading.Lock()
_HISTORY: collections.deque[dict[str, Any]] = collections.deque(maxlen=HISTORY_LIMIT)


# --------------------------------------------------------------------------- #
# command policy
# --------------------------------------------------------------------------- #
def is_blocked(command: str) -> bool:
    """True when ``command`` contains a denied fragment (case-insensitive)."""
    lowered = (command or "").lower()
    return any(fragment.lower() in lowered for fragment in BLOCKED_FRAGMENTS)


def check_command(command: str) -> None:
    """Reject empty or dangerous commands with a user-facing 400."""
    if not (command or "").strip():
        raise InvalidInput("命令不能为空")
    if is_blocked(command):
        raise InvalidInput("已拒绝执行：该命令命中内置的危险命令黑名单。")


# --------------------------------------------------------------------------- #
# working directory
# --------------------------------------------------------------------------- #
def resolve_cwd(root: Path, cwd: str | None) -> Path:
    """Resolve ``cwd`` under the project root; traversal is rejected."""
    target = paths.resolve_in_project(root, (cwd or "").strip() or None)
    if not target.is_dir():
        raise InvalidInput(f"工作目录不存在：{cwd}")
    return target


def cwd_label(root: Path, workdir: Path) -> str:
    """Human-readable relative cwd for events and history ("." for the root)."""
    try:
        rel = workdir.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "."
    return rel or "."


# --------------------------------------------------------------------------- #
# history ring
# --------------------------------------------------------------------------- #
def format_tail(text: str, lines: int = OUTPUT_TAIL_LINES) -> str:
    """Last ``lines`` non-empty lines of ``text``, clipped to 2000 chars."""
    if not text:
        return ""
    rows = [line.rstrip() for line in text.splitlines() if line.strip()]
    if not rows:
        return ""
    tail = "\n".join(rows[-max(1, lines) :])
    if len(tail) > OUTPUT_TAIL_CHARS:
        tail = tail[-OUTPUT_TAIL_CHARS:]
    return tail.strip()


def record(
    project_id: int,
    command: str,
    cwd: str | None,
    source: str,
    exit_code: int,
    duration_ms: int,
    output_tail: str,
) -> dict[str, Any]:
    """Append one finished command to the ring buffer and return it."""
    duration = max(0, int(duration_ms))
    finished = datetime.now(UTC)
    started = finished - timedelta(milliseconds=duration)
    entry: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "project_id": int(project_id),
        "command": command,
        "cwd": cwd or ".",
        "source": "agent" if source == "agent" else "user",
        "exit_code": int(exit_code),
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_ms": duration,
        "output_tail": format_tail(output_tail),
    }
    with _LOCK:
        _HISTORY.append(entry)
    return entry


def history(project_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent entries for one project, newest first."""
    capped = max(0, min(int(limit), HISTORY_LIMIT))
    with _LOCK:
        rows = [dict(item) for item in _HISTORY if int(item["project_id"]) == int(project_id)]
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        row.pop("project_id", None)
        out.append(row)
    return out[:capped]


def clear_history() -> None:
    """Drop every buffered entry (tests and the panel's "清空" button)."""
    with _LOCK:
        _HISTORY.clear()


__all__ = [
    "BLOCKED_FRAGMENTS",
    "HISTORY_LIMIT",
    "check_command",
    "clear_history",
    "cwd_label",
    "format_tail",
    "history",
    "is_blocked",
    "record",
    "resolve_cwd",
]
