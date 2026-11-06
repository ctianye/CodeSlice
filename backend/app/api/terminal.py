"""Integrated terminal endpoints.

``POST /run`` streams ``text/event-stream``: ``start`` → many ``output`` →
``exit`` (or ``error``), always terminated by ``done``. Nothing runs before the
stream opens — a sandbox rejection or a bad ``cwd`` arrives as an ``error`` frame
inside an HTTP 200 stream, which is what the panel renders as a red line.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.conversations import SSE_HEADERS
from app.api.deps import ProjectDep, TokenDep
from app.services import paths, sandbox, terminal

log = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/terminal", tags=["terminal"])


class RunBody(BaseModel):
    command: str = Field(min_length=1)
    # Relative to the project root; omitted means the root itself.
    cwd: str | None = None


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _wrap(iterator: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Serialize terminal frames and guarantee a closing ``done``."""
    try:
        for event in iterator:
            yield _sse(event)
    except GeneratorExit:  # client disconnected or pressed 停止; nothing to flush
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("terminal stream failed")
        yield _sse({"type": "error", "detail": f"{type(exc).__name__}: {exc}"[:400]})
        yield _sse({"type": "done"})
        return
    yield _sse({"type": "done"})


def _root(project: dict[str, Any]) -> Path:
    return paths.resolve_root(project["path"])


@router.post("/run")
def run_command(project: ProjectDep, body: RunBody, _: TokenDep) -> StreamingResponse:
    root = _root(project)
    frames = terminal.run_stream(
        root=root,
        command=body.command,
        cwd=body.cwd,
        project_id=int(project["id"]),
    )
    return StreamingResponse(_wrap(frames), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/history")
def get_history(project: ProjectDep, _: TokenDep, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"items": sandbox.history(int(project["id"]), limit)}


@router.post("/cancel")
def cancel(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    """Kill whatever is still running in this project's terminal panel."""
    return {"ok": True, "cancelled": terminal.cancel(int(project["id"]))}


__all__ = ["router"]
