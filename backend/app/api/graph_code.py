"""Knowledge-graph endpoints: read the graph, rebuild it, query one node.

The graph endpoint returns the whole payload because the UI needs it to draw. A
project with a few thousand symbols is a megabyte or two of JSON, which is fine
over localhost and far simpler than paginating a structure the layout algorithm
needs end to end.

Only the node-detail endpoint is intentionally small: clicking a node must not
re-send the graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.deps import ProjectDep, TokenDep
from app.graph import query, service, store
from app.services import paths

router = APIRouter(tags=["graph"])


class GraphBuildResult(BaseModel):
    nodes: int
    edges: int
    communities: int
    rebuilt: bool
    stats: dict[str, Any]


@router.get("/projects/{project_id}/graph")
def get_graph(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    """The project's knowledge graph, rebuilt first when the index moved on.

    Rebuilding on read rather than on a schedule means the graph is never stale,
    which matters because the agent cites its edges as fact.
    """
    root = paths.resolve_root(project["path"])
    graph, rebuilt = store.ensure_fresh(project["id"], root)
    return {
        "nodes": graph.get("nodes") or [],
        "edges": graph.get("edges") or [],
        "communities": graph.get("communities") or [],
        "stats": {**service.summary(graph), "rebuilt": rebuilt},
    }


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


@router.get("/projects/{project_id}/graph/node")
def get_node_detail(
    project: ProjectDep,
    _: TokenDep,
    node_id: str = Query(min_length=1),
    depth: int = Query(default=1, ge=1, le=3),
) -> dict[str, Any]:
    """One node plus its immediate connections.

    Depth stays at 1 by default: the UI shows a focused neighbourhood on click,
    and a depth-2 walk on a hub node can return most of the graph.
    """
    root = paths.resolve_root(project["path"])
    graph = store.ensure_fresh(project["id"], root)[0]
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
