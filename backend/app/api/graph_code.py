"""Knowledge-graph endpoints: read the graph, rebuild it, query one node.

The graph endpoint returns the whole payload because the UI needs it to draw. A
project with a few thousand symbols is a megabyte or two of JSON, which is fine
over localhost (and GZip'd by ``main.py``) and far simpler than paginating a
structure the layout algorithm needs end to end. It is read from the persisted
copy in SQLite, so a click is one primary-key lookup.

Only the node-detail endpoint is intentionally small: clicking a node must not
re-send the graph, and it must not trigger a rebuild either — it serves whatever
is persisted and only builds when nothing is.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.conversations import SSE_HEADERS
from app.api.deps import ProjectDep, TokenDep
from app.core.db import GRAPH_SCOPE_CODE, GRAPH_SCOPE_NOTES, NOTES_GRAPH_PROJECT_ID
from app.core.errors import AppError, NotFound
from app.graph import notes, query, relationships, service, store
from app.services import paths
from app.storage import repo

router = APIRouter(tags=["graph"])
log = logging.getLogger(__name__)


class SummaryRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=2048)


class GraphLayoutRequest(BaseModel):
    layout_key: str = Field(min_length=1, max_length=128)
    positions: dict[str, dict[str, float]]


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _summary_frames(iterator: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Serialize summary frames, turning a failure into a readable error frame.

    The stream has already started as HTTP 200 when the model fails, so this is
    the only channel that can tell the panel what happened. A partial answer is
    preserved on the client; the frame simply explains why it stopped.
    """
    try:
        for event in iterator:
            yield _sse(event)
    except GeneratorExit:  # client disconnected / pressed 取消
        raise
    except AppError as exc:
        yield _sse({"type": "error", "detail": exc.detail})
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("graph summary stream failed")
        yield _sse({"type": "error", "detail": f"AI 解读失败，请重试（{type(exc).__name__}）。"})


def _layout_payload(graph: dict[str, Any], project_id: int, scope: str) -> dict[str, Any]:
    key = store.topology_key(graph)
    return {"layout_key": key, "layout": store.load_layout(project_id, scope, key)}


@router.get("/graphs/notes")
def get_notes_graph(_: TokenDep) -> dict[str, Any]:
    graph = notes.build()
    return {**graph, **_layout_payload(graph, NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES)}


@router.put("/graphs/notes/layout")
def save_notes_layout(body: GraphLayoutRequest, _: TokenDep) -> dict[str, bool]:
    store.save_layout(NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES, body.layout_key, body.positions)
    return {"ok": True}


@router.post("/graphs/notes/summary")
def summarize_note(body: SummaryRequest, _: TokenDep) -> dict[str, Any]:
    return relationships.summarize(notes.build(), body.node_id, "notes")


@router.post("/graphs/notes/summary/stream")
def summarize_note_stream(body: SummaryRequest, _: TokenDep) -> StreamingResponse:
    return StreamingResponse(
        _summary_frames(relationships.summarize_stream(notes.build(), body.node_id, "notes")),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


def _cached_or_build(project: dict, root) -> dict[str, Any]:
    """Persisted graph for read-only callers, building only when absent.

    Node detail, summary and conversation targets already know which project
    they want; the user has not asked for a refresh, so a stale-by-edges graph is
    still preferable to stalling a click. ``store.ensure_fresh`` is the endpoint
    that actually opens the graph view and therefore does validate.
    """
    cached = store.load_cached(project["id"], GRAPH_SCOPE_CODE)
    if cached is not None:
        return cached
    graph, _rebuilt = store.ensure_fresh(project["id"], root)
    return graph


@router.post("/projects/{project_id}/graph/summary")
def summarize_code(project: ProjectDep, body: SummaryRequest, _: TokenDep) -> dict[str, Any]:
    graph = _cached_or_build(project, paths.resolve_root(project["path"]))
    return relationships.summarize(graph, body.node_id, "code")


@router.post("/projects/{project_id}/graph/summary/stream")
def summarize_code_stream(project: ProjectDep, body: SummaryRequest, _: TokenDep) -> StreamingResponse:
    graph = _cached_or_build(project, paths.resolve_root(project["path"]))
    return StreamingResponse(
        _summary_frames(relationships.summarize_stream(graph, body.node_id, "code")),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/conversations/{conversation_id}/graph-targets")
def conversation_graph_targets(conversation_id: str, _: TokenDep) -> dict[str, Any]:
    conversation = repo.get_conversation(conversation_id)
    if conversation is None:
        raise NotFound("conversation not found")
    project = repo.get_project(conversation["project_id"])
    if project is None:
        raise NotFound("project not found")
    graph = _cached_or_build(project, paths.resolve_root(project["path"]))
    nodes = graph.get("nodes", [])
    by_id = {node["id"]: node for node in nodes}
    targets: dict[str, dict] = {}
    messages = list(reversed(repo.list_messages(conversation_id)))
    for message in messages:
        anchor = (message.get("meta") or {}).get("graph_anchor")
        if anchor in by_id:
            targets.setdefault(anchor, by_id[anchor])

    locations = [conversation] if conversation.get("file_path") else []
    locations += [citation for message in messages for citation in (message.get("citations") or [])]
    for location in locations:
        path = str(location.get("file_path") or location.get("path") or "").replace("\\", "/").removeprefix("./")
        line = int(location.get("start_line") or 1)
        candidates = [
            node for node in nodes if node.get("path") == path and node.get("kind") not in {"external", "rationale"}
        ]
        enclosing = [node for node in candidates if node.get("start_line", 1) <= line <= node.get("end_line", 1)]
        enclosing.sort(
            key=lambda node: (
                node.get("kind") == "file",
                node.get("end_line", 1) - node.get("start_line", 1),
                node["id"],
            )
        )
        target = next(iter(enclosing), None) or next(
            (node for node in candidates if node.get("kind") == "file"),
            None,
        )
        if target:
            targets.setdefault(target["id"], target)
    return {"project_id": project["id"], "targets": list(targets.values())}


class GraphBuildResult(BaseModel):
    nodes: int
    edges: int
    communities: int
    rebuilt: bool
    stats: dict[str, Any]


@router.get("/projects/{project_id}/graph")
def get_graph(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    """The project's knowledge graph, served from the persisted copy.

    This is the one endpoint that validates freshness, because it is the "open
    the graph" action: if the corpus changed since the last build, the rebuild
    happens here and ``stats.rebuilt`` says so. Everything else reads the cache.
    """
    root = paths.resolve_root(project["path"])
    graph, rebuilt = store.ensure_fresh(project["id"], root)
    return {
        "nodes": graph.get("nodes") or [],
        "edges": graph.get("edges") or [],
        "communities": graph.get("communities") or [],
        "stats": {**service.summary(graph), "rebuilt": rebuilt},
        **_layout_payload(graph, project["id"], GRAPH_SCOPE_CODE),
    }


@router.put("/projects/{project_id}/graph/layout")
def save_code_layout(project: ProjectDep, body: GraphLayoutRequest, _: TokenDep) -> dict[str, bool]:
    store.save_layout(project["id"], GRAPH_SCOPE_CODE, body.layout_key, body.positions)
    return {"ok": True}


@router.post("/projects/{project_id}/graph/build")
def rebuild_graph(project: ProjectDep, _: TokenDep) -> GraphBuildResult:
    """Force a rebuild, for when the user changed ignore rules or wants a fresh view."""
    root = paths.resolve_root(project["path"])
    graph = store.force_rebuild(project["id"], root)
    return GraphBuildResult(
        nodes=len(graph.get("nodes") or []),
        edges=len(graph.get("edges") or []),
        communities=len(graph.get("communities") or []),
        rebuilt=True,
        stats=service.summary(graph),
    )


def _location_node(graph: dict[str, Any], path: str, line: int) -> dict[str, Any] | None:
    """Most specific node that covers a file position.

    A cursor inside a method must resolve to the method, not to the file node that
    also contains it. ``rationale`` is included only when it starts exactly on the
    requested line: a docstring explains the definition below it, but a click on
    the ``def`` line means the function.
    """
    wanted = path.replace("\\", "/").removeprefix("./")
    candidates = [node for node in graph.get("nodes") or [] if node.get("path") == wanted]
    enclosing = [
        node
        for node in candidates
        if node.get("kind") != "external"
        and int(node.get("start_line") or 1) <= line <= int(node.get("end_line") or 1)
        and not (node.get("kind") == "rationale" and int(node.get("start_line") or 1) != line)
    ]
    kind_rank = {"function": 0, "class": 0, "file": 2, "rationale": 3}
    enclosing.sort(
        key=lambda node: (
            kind_rank.get(str(node.get("kind")), 1),
            int(node.get("end_line") or 1) - int(node.get("start_line") or 1),
            node["id"],
        )
    )
    if enclosing:
        return enclosing[0]
    before = [
        node
        for node in candidates
        if node.get("kind") not in {"external", "rationale"}
        and int(node.get("start_line") or 1) <= line
    ]
    before.sort(key=lambda node: (-int(node.get("start_line") or 1), node["id"]))
    return before[0] if before else (candidates[0] if candidates else None)


@router.get("/projects/{project_id}/graph/locate")
def locate_file_position(
    project: ProjectDep,
    _: TokenDep,
    path: str = Query(min_length=1),
    line: int = Query(default=1, ge=1),
) -> dict[str, Any]:
    """Resolve a code position to graph node, for "locate in graph" in the editor.

    Served from the persisted copy: this runs while the user is reading code and
    must never walk the source tree. The caller opens the returned node.
    """
    graph = _cached_or_build(project, paths.resolve_root(project["path"]))
    return {"node": _location_node(graph, path, line)}


@router.get("/projects/{project_id}/graph/node")
def get_node_detail(
    project: ProjectDep,
    _: TokenDep,
    node_id: str = Query(min_length=1),
    depth: int = Query(default=1, ge=1, le=3),
) -> dict[str, Any]:
    """One node plus its immediate connections.

    Depth stays at 1 by default: the UI shows a focused neighbourhood on click,
    and a depth-2 walk on a hub node can return most of the graph. The graph comes
    from the persisted copy — clicking a node never re-walks the source tree.
    """
    root = paths.resolve_root(project["path"])
    graph = _cached_or_build(project, root)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    nodes_by_id = {node["id"]: node for node in nodes}

    node = nodes_by_id.get(node_id)
    if node is None:
        # Fall back to a label/path match so a caller holding a bare symbol name
        # still gets an answer instead of a bare 404.
        resolved = query.resolve_anchor(nodes, node_id)
        if resolved is None:
            return {"node": None, "edges": [], "neighbors": []}
        node_id = resolved
        node = nodes_by_id[node_id]

    related: list[dict[str, Any]] = []
    for edge in edges:
        if edge["source"] == node_id:
            other_id = edge["target"]
            direction = "out"
        elif edge["target"] == node_id:
            other_id = edge["source"]
            direction = "in"
        else:
            continue
        other = nodes_by_id.get(other_id)
        if other is None:
            continue
        related.append(
            {
                "direction": direction,
                "relation": edge.get("relation"),
                "confidence": edge.get("confidence"),
                "id": other_id,
                "label": other.get("label"),
                "kind": other.get("kind"),
                "path": other.get("path"),
                "start_line": other.get("start_line"),
                "community": other.get("community"),
            }
        )

    # Outgoing first, then by confidence: "what this calls" is the question the
    # user usually has when they click a function.
    order = {"out": 0, "in": 1}
    rank = {"EXTRACTED": 0, "INFERRED": 1, "AMBIGUOUS": 2}
    related.sort(
        key=lambda item: (
            order.get(item["direction"], 9),
            rank.get(item["confidence"], 9),
            item["label"] or "",
        )
    )

    return {"node": node, "edges": related, "neighbors": related}
