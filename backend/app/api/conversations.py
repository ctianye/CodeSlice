"""Conversations and the agent SSE stream.

Two POST endpoints stream ``text/event-stream``: ``messages`` (start a turn) and
``resume`` (answer an approval request). Everything that can fail before the
stream starts is raised as a normal HTTP error so the UI can show it directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent import graph as agent_graph
from app.api.deps import ProjectDep, TokenDep
from app.core.errors import InvalidInput, NotFound
from app.models import config as model_config
from app.services import anchors
from app.storage import repo

log = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ConversationCreate(BaseModel):
    project_id: int
    title: str | None = None
    file_path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    selected_code: str | None = None
    mode: str = "snippet"


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)


class ResumeBody(BaseModel):
    interrupt_id: str
    decision: str
    remember_pattern: str | None = None
    note: str | None = None


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _require_model() -> None:
    try:
        model_config.build_chat_model()
    except Exception as exc:  # ConfigMissing or provider construction error
        raise InvalidInput(str(getattr(exc, "detail", exc))) from None


def _wrap(iterator: Iterator[dict[str, Any]], conversation_id: str) -> Iterator[str]:
    """Serialize agent events and persist anchors for emitted citations."""
    citations: dict[tuple[str, int, int], dict[str, Any]] = {}
    try:
        for event in iterator:
            etype = event.get("type")
            if etype == "citation":
                key = (
                    event.get("path", ""),
                    int(event.get("start_line") or 1),
                    int(event.get("end_line") or 1),
                )
                citations.setdefault(key, event)
            elif etype == "done" and citations:
                _persist_anchors(conversation_id, list(citations.values()))
            yield _sse(event)
    except GeneratorExit:  # client disconnected
        raise
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("sse stream failed")
        yield _sse({"type": "error", "detail": f"{type(exc).__name__}: {exc}"[:400]})
        yield _sse({"type": "done"})


def _persist_anchors(conversation_id: str, citations: list[dict[str, Any]]) -> None:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        return
    for citation in citations[:6]:
        try:
            anchors.create_for_citation(
                project_id=int(conversation["project_id"]),
                conversation_id=conversation_id,
                file_path=str(citation.get("path") or ""),
                start_line=int(citation.get("start_line") or 1),
                end_line=int(citation.get("end_line") or 1),
                symbol=citation.get("symbol"),
            )
        except Exception:  # anchors are best effort; never break the stream
            log.debug("anchor create failed", exc_info=True)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.post("/conversations")
def create_conversation(body: ConversationCreate, _: TokenDep) -> dict[str, Any]:
    project = repo.get_project(body.project_id)
    if project is None:
        raise NotFound("project not found")
    if body.mode not in ("snippet", "project"):
        raise InvalidInput("mode must be 'snippet' or 'project'")
    title = (body.title or "").strip()
    if not title:
        if body.mode == "snippet" and body.file_path:
            span = f":{body.start_line}-{body.end_line}" if body.start_line else ""
            from pathlib import PurePath

            title = f"{PurePath(body.file_path).name}{span}"
        else:
            title = "项目问答"
    conversation = repo.create_conversation(
        project_id=body.project_id,
        title=title,
        mode=body.mode,
        file_path=body.file_path,
        start_line=body.start_line,
        end_line=body.end_line,
        selected_code=body.selected_code,
    )
    return repo.conversation_public(conversation)


@router.get("/conversations")
def list_conversations(_: TokenDep, project_id: int | None = None) -> list[dict[str, Any]]:
    return [repo.conversation_public(c) for c in repo.list_conversations(project_id)]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, _: TokenDep) -> dict[str, Any]:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")
    return {
        "conversation": repo.conversation_public(conversation),
        "messages": repo.list_messages(conversation_id),
        "pending_interrupt": agent_graph.pending_interrupt(conversation_id),
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, _: TokenDep) -> dict[str, bool]:
    if repo.get_conversation(conversation_id) is None:
        raise NotFound("conversation not found")
    repo.delete_conversation(conversation_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# agent turns
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/messages")
def post_message(conversation_id: str, body: MessageCreate, _: TokenDep) -> StreamingResponse:
    if repo.get_conversation(conversation_id) is None:
        raise NotFound("conversation not found")
    _require_model()
    pending = agent_graph.pending_interrupt(conversation_id)
    if pending:
        raise InvalidInput("waiting for approval on the previous step; call /resume first")
    stream = agent_graph.start_turn(conversation_id, body.content)
    return StreamingResponse(_wrap(stream, conversation_id), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/conversations/{conversation_id}/resume")
def resume(conversation_id: str, body: ResumeBody, _: TokenDep) -> StreamingResponse:
    if repo.get_conversation(conversation_id) is None:
        raise NotFound("conversation not found")
    if body.decision not in ("approve", "deny", "approve_session", "approve_always"):
        raise InvalidInput("invalid decision")
    _require_model()
    stream = agent_graph.resume_turn(
        conversation_id,
        body.interrupt_id,
        body.decision,
        remember_pattern=body.remember_pattern,
        note=body.note,
    )
    return StreamingResponse(_wrap(stream, conversation_id), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/conversations/{conversation_id}/interrupt")
def get_interrupt(conversation_id: str, _: TokenDep) -> dict[str, Any]:
    if repo.get_conversation(conversation_id) is None:
        raise NotFound("conversation not found")
    return {"pending_interrupt": agent_graph.pending_interrupt(conversation_id)}


__all__ = ["ProjectDep", "router"]
