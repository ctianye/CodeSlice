"""LangGraph agent workflow for CodeSlice.

Graph shape::

    START → prepare → agent ─┬─(tool calls)──→ run_one ─┬─(more calls)─→ run_one
                             │                          └───────────────→ agent
                             └─(final answer)──→ finalize → END

``prepare`` builds the system prompt and retrieves context for this turn.
``agent`` streams the model answer (tokens go out as custom stream events).
``run_one`` executes exactly **one** tool call per node run so a resumed
approval cannot re-run a previous side effect: for ``write_file`` /
``run_command`` it calls ``interrupt()`` *before* touching the filesystem, so
the re-run on resume reaches the side effect exactly once.

Events written through ``get_stream_writer`` are forwarded verbatim by the SSE
endpoint (see ``app/api/conversations.py``).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Iterator
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from app.agent import checkpoints, prompts
from app.agent import tools as agent_tools
from app.core.errors import ConfigMissing, NotFound
from app.models import config as model_config
from app.retrieval import hybrid
from app.services import git_service, paths
from app.storage import repo

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 10
MAX_HISTORY_MESSAGES = 30


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    project_id: int
    conversation_id: str
    project_name: str
    mode: str
    file_path: str | None
    start_line: int | None
    end_line: int | None
    selected_code: str | None
    query: str
    system: str
    context: str
    citations: list[dict[str, Any]]
    pending_calls: list[dict[str, Any]]
    session_approvals: list[str]
    tool_rounds: int
    last_ai: dict[str, Any] | None
    error: str | None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _writer():
    """Return a callable for custom stream events (no-op outside a stream)."""
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except Exception:  # pragma: no cover - direct invoke() has no writer

        def _noop(payload: dict[str, Any]) -> None:
            return None

        return _noop


def _emit(event: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):  # pragma: no cover - invoke() has no writer
        _writer()(event)


def _text_of(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content or "")


MAX_SNIPPET_LINES = 200

# ``interrupt()`` re-executes the node from the top on resume, so anything emitted
# before it would be sent twice. Remember which approval requests the client has
# already seen and stay silent the second time.
_emitted_interrupts: set[tuple[str, str]] = set()
_emitted_lock = threading.Lock()


def _emit_interrupt(conversation_id: str, payload: dict[str, Any]) -> None:
    key = (conversation_id, str(payload.get("id") or ""))
    with _emitted_lock:
        if key in _emitted_interrupts:
            return
        _emitted_interrupts.add(key)
    _emit({"type": "approval_required", **payload})


def _forget_interrupt(conversation_id: str, interrupt_id: str) -> None:
    with _emitted_lock:
        _emitted_interrupts.discard((conversation_id, str(interrupt_id)))


def _read_snippet(root, rel_path: str, start: int | None, end: int | None) -> str | None:
    """Load the bound line range so a line-only anchor still shows real code."""
    if not rel_path:
        return None
    try:
        path = paths.resolve_in_project(root, rel_path)
    except Exception:
        return None
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    first = max(1, int(start or 1))
    last = int(end or (first + MAX_SNIPPET_LINES - 1))
    last = min(last, first + MAX_SNIPPET_LINES - 1, len(lines))
    if first > len(lines):
        return None
    return "\n".join(lines[first - 1 : last])


def _citation_from_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": chunk["path"],
        "start_line": int(chunk["start_line"]),
        "end_line": int(chunk["end_line"]),
        "symbol": chunk.get("symbol"),
    }


def _dedupe_citations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int, int]] = set()
    out = []
    for item in items:
        key = (item["path"], int(item["start_line"]), int(item["end_line"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# nodes
# --------------------------------------------------------------------------- #
def prepare(state: AgentState) -> dict[str, Any]:
    project = repo.get_project(int(state["project_id"]))
    if project is None:
        raise NotFound("project not found")
    root = paths.resolve_root(project["path"])
    query = state.get("query") or ""

    _emit({"type": "status", "stage": "retrieve", "detail": "检索相关代码"})

    chunks: list[dict[str, Any]] = []
    if query.strip():
        if state.get("mode") == "snippet" and state.get("file_path"):
            found = hybrid.search(int(state["project_id"]), query, top_k=10)
            same_file = [c for c in found if c["path"] == state["file_path"]]
            chunks = same_file or found[:4]
        else:
            chunks = hybrid.search(int(state["project_id"]), query, top_k=8)

    citations = [_citation_from_chunk(c) for c in chunks][:8]
    for citation in citations:
        _emit({"type": "citation", **citation})

    branch = None
    try:
        status = git_service.current_state(root)
        if status.get("is_repo"):
            branch = status.get("branch")
    except Exception:  # pragma: no cover - git is optional
        branch = None

    selected_code = state.get("selected_code")
    if not selected_code and state.get("mode") == "snippet" and state.get("file_path"):
        selected_code = _read_snippet(root, state["file_path"], state.get("start_line"), state.get("end_line"))

    system = prompts.build_system_prompt(
        mode=state.get("mode") or "project",
        file_path=state.get("file_path"),
        start_line=state.get("start_line"),
        end_line=state.get("end_line"),
        selected_code=selected_code,
        context=hybrid.context_for_prompt(chunks),
        project_name=project["name"],
        git_branch=branch,
    )
    return {
        "system": system,
        "context": hybrid.context_for_prompt(chunks),
        "citations": citations,
        "project_name": project["name"],
        "pending_calls": [],
        "tool_rounds": 0,
    }


def agent(state: AgentState) -> dict[str, Any]:
    _emit({"type": "status", "stage": "plan", "detail": "模型生成回答"})
    model = model_config.build_chat_model()
    bound = model.bind_tools(agent_tools.bound_tools())

    history: list[AnyMessage] = list(state.get("messages") or [])
    if len(history) > MAX_HISTORY_MESSAGES:
        history = history[-MAX_HISTORY_MESSAGES:]
    payload: list[AnyMessage] = [
        SystemMessage(content=state.get("system") or prompts.BASE),
        *history,
    ]

    accumulated: AIMessageChunk | None = None
    try:
        for chunk in bound.stream(payload):
            accumulated = chunk if accumulated is None else accumulated + chunk
            delta = _text_of(chunk)
            if delta:
                _emit({"type": "token", "delta": delta})
    except Exception as exc:
        log.exception("model call failed")
        return {"error": f"{type(exc).__name__}: {exc}", "pending_calls": []}

    if accumulated is None:
        return {"error": "model returned no output", "pending_calls": []}

    message = AIMessage(
        content=accumulated.content,
        tool_calls=list(getattr(accumulated, "tool_calls", []) or []),
        additional_kwargs=getattr(accumulated, "additional_kwargs", {}) or {},
        id=accumulated.id,
    )
    calls = [
        {
            "name": call["name"],
            "args": dict(call.get("args") or {}),
            "id": call.get("id") or f"call-{i}",
        }
        for i, call in enumerate(message.tool_calls)
    ]
    if calls:
        _emit({"type": "status", "stage": "execute", "detail": f"准备执行 {len(calls)} 个工具"})

    rounds = int(state.get("tool_rounds") or 0) + 1
    if rounds > MAX_TOOL_ROUNDS:
        calls = []

    return {
        "messages": [message],
        "pending_calls": calls,
        "tool_rounds": rounds,
        "last_ai": {"content": _text_of(message), "tool_calls": calls},
    }


def run_one(state: AgentState) -> dict[str, Any]:
    calls = list(state.get("pending_calls") or [])
    if not calls:
        return {"messages": []}
    call = calls[0]
    rest = calls[1:]
    name = call["name"]
    args = call.get("args") or {}
    call_id = call.get("id") or "call-0"

    project = repo.get_project(int(state["project_id"]))
    if project is None:
        raise NotFound("project not found")
    root = paths.resolve_root(project["path"])
    session_approvals = list(state.get("session_approvals") or [])

    approved = name not in agent_tools.DANGEROUS_TOOLS or name in session_approvals
    if name in ("run_command", "write_file") and not approved:
        # "总是允许" rules persist per project: a command glob for run_command,
        # a path glob for write_file (both are matched by match_rule).
        subject = str(args.get("command") or "") if name == "run_command" else str(args.get("path") or "")
        try:
            rule = agent_tools.match_rule(subject, repo.list_rules(int(state["project_id"])))
            approved = rule is not None
        except Exception:  # pragma: no cover
            approved = False

    if not approved:
        payload = agent_tools.preview_for_approval(name, args, root)
        interrupt_payload = {
            "id": call_id,
            "kind": "write_file" if name == "write_file" else "run_command",
            "payload": {**payload, "tool_call_id": call_id},
        }
        _emit_interrupt(state["conversation_id"], interrupt_payload)
        decision = interrupt(interrupt_payload)
        _forget_interrupt(state["conversation_id"], call_id)
        action = (decision or {}).get("decision", "deny")
        note = (decision or {}).get("note")
        repo.add_approval(state["conversation_id"], call_id, interrupt_payload["kind"], action, note)
        if action in ("deny", "cancelled"):
            text = (
                "用户拒绝了这次操作"
                + (f"（{note}）" if note else "")
                + "。请换一种不需要该操作的方式，或说明需要用户手动完成。"
            )
            _emit({"type": "tool_result", "call_id": call_id, "ok": False, "summary": "用户拒绝"})
            return {
                "messages": [ToolMessage(content=text, tool_call_id=call_id)],
                "pending_calls": rest,
                "session_approvals": session_approvals,
            }
        if action == "approve_session":
            # Session-scoped: every later call of the same tool skips the prompt.
            session_approvals.append(name)
        if action == "approve_always":
            # Persisted rule for this exact subject. Deliberately NOT added to
            # session_approvals: "总是允许" is narrower than "本会话允许" and must
            # not silently unlock unrelated paths/commands.
            pattern = (decision or {}).get("remember_pattern")
            if not pattern:
                pattern = str(args.get("command") or "") if name == "run_command" else str(args.get("path") or "")
            if pattern:
                with contextlib.suppress(Exception):  # pragma: no cover - rule is a convenience
                    repo.create_rule(
                        int(state["project_id"]),
                        pattern=pattern,
                        scope="always",
                        needs_confirm=False,
                    )

    _emit(
        {
            "type": "tool_call",
            "name": name,
            "args": args,
            "call_id": call_id,
        }
    )
    result = agent_tools.run_tool(name, args, root, int(state["project_id"]))
    ok = not result.startswith(("工具执行失败", "工具执行异常", "已拒绝执行"))
    summary = result.splitlines()[0][:200] if result else ""
    _emit({"type": "tool_result", "call_id": call_id, "ok": ok, "summary": summary})

    citations = list(state.get("citations") or [])
    if name == "read_file" and ok:
        try:
            start = int(args.get("start_line") or 1)
            end = int(args.get("end_line") or start)
            citations.append({"path": str(args["path"]), "start_line": start, "end_line": end, "symbol": None})
            _emit({"type": "citation", **citations[-1]})
        except Exception:  # pragma: no cover
            pass

    return {
        "messages": [ToolMessage(content=result, tool_call_id=call_id)],
        "pending_calls": rest,
        "session_approvals": session_approvals,
        "citations": _dedupe_citations(citations),
    }


def finalize(state: AgentState) -> dict[str, Any]:
    messages = list(state.get("messages") or [])
    last_ai = None
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            last_ai = message
            break
    text = _text_of(last_ai) if last_ai is not None else ""
    citations = _dedupe_citations(list(state.get("citations") or []))
    meta: dict[str, Any] = {"stages": ["retrieve", "plan", "execute", "summarize"]}
    if not text.strip():
        text = "抱歉，这一轮没有生成有效内容，请重试或把问题描述得更具体一些。"
        _emit({"type": "error", "detail": "empty model output"})
    saved = repo.add_message(state["conversation_id"], "assistant", text, citations=citations or None, meta=meta)
    _emit({"type": "message", "message": saved})
    return {"citations": citations}


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #
def _after_agent(state: AgentState) -> str:
    if state.get("error"):
        return "finalize"
    if state.get("pending_calls"):
        return "run_one"
    return "finalize"


def _after_tool(state: AgentState) -> str:
    if state.get("pending_calls"):
        return "run_one"
    return "agent"


_compiled = None


def build_graph():
    global _compiled
    if _compiled is not None:
        return _compiled
    graph = StateGraph(AgentState)
    graph.add_node("prepare", prepare)
    graph.add_node("agent", agent)
    graph.add_node("run_one", run_one)
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "agent")
    graph.add_conditional_edges("agent", _after_agent, {"run_one": "run_one", "finalize": "finalize"})
    graph.add_conditional_edges("run_one", _after_tool, {"run_one": "run_one", "agent": "agent"})
    graph.add_edge("finalize", END)
    _compiled = graph.compile(checkpointer=checkpoints.get_saver())
    return _compiled


def _config(conversation_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": conversation_id}}


# --------------------------------------------------------------------------- #
# public API used by the SSE routes
# --------------------------------------------------------------------------- #
def start_turn(conversation_id: str, text: str) -> Iterator[dict[str, Any]]:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")
    try:
        model_config.build_chat_model()
    except ConfigMissing:
        raise

    repo.add_message(conversation_id, "user", text)
    user_message = HumanMessage(content=text)

    snapshot = build_graph().get_state(_config(conversation_id))
    has_history = bool(snapshot.values.get("messages")) if snapshot.values else False

    inputs: dict[str, Any]
    if has_history:
        inputs = {"messages": [user_message], "query": text, "citations": [], "error": None}
    else:
        inputs = {
            "messages": [user_message],
            "query": text,
            "project_id": int(conversation["project_id"]),
            "conversation_id": conversation_id,
            "mode": conversation.get("mode") or "snippet",
            "file_path": conversation.get("file_path"),
            "start_line": conversation.get("start_line"),
            "end_line": conversation.get("end_line"),
            "selected_code": conversation.get("selected_code"),
            "citations": [],
            "session_approvals": [],
            "tool_rounds": 0,
            "error": None,
        }
    yield from _stream(inputs, conversation_id)


def resume_turn(
    conversation_id: str,
    interrupt_id: str,
    decision: str,
    remember_pattern: str | None = None,
    note: str | None = None,
) -> Iterator[dict[str, Any]]:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")
    payload = {"decision": decision, "remember_pattern": remember_pattern, "note": note}
    yield from _stream(Command(resume=payload), conversation_id)


def _stream(inputs: Any, conversation_id: str) -> Iterator[dict[str, Any]]:
    graph = build_graph()
    config = _config(conversation_id)
    try:
        for event in graph.stream(inputs, config=config, stream_mode="custom"):
            if isinstance(event, dict):
                yield event
    except Exception as exc:
        log.exception("agent stream failed")
        yield {"type": "error", "detail": f"{type(exc).__name__}: {exc}"[:400]}
    yield {"type": "done"}


def pending_interrupt(conversation_id: str) -> dict[str, Any] | None:
    """Return the approval request waiting for this thread, if any."""
    try:
        snapshot = build_graph().get_state(_config(conversation_id))
    except Exception:  # pragma: no cover
        return None
    tasks = getattr(snapshot, "tasks", None) or ()
    for task in tasks:
        for item in getattr(task, "interrupts", ()) or ():
            value = getattr(item, "value", None)
            if isinstance(value, dict) and value.get("id"):
                return value
    return None


def reset_thread(conversation_id: str) -> None:
    with contextlib.suppress(Exception):  # pragma: no cover - best effort cleanup
        checkpoints.delete_thread(conversation_id)
