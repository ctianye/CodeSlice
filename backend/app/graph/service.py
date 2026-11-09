"""Convenience entry point for the agent's graph tools.

The tool layer wants one call per question ("give me context for this text",
"give me the path between A and B") and does not care where the graph came from.
This module binds a project to its freshest graph and forwards to ``query``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.graph import query, store


def load(project_id: int, root: Path) -> dict[str, Any]:
    """Freshest graph for a project (rebuilding if the index moved on)."""
    graph, _rebuilt = store.ensure_fresh(project_id, root)
    return graph


def load_fresh(project_id: int, root: Path) -> tuple[dict[str, Any], bool]:
    """Freshest graph plus whether this call had to rebuild it."""
    return store.ensure_fresh(project_id, root)


def context_for_question(
    project_id: int,
    root: Path,
    question: str,
    *,
    mode: str = "bfs",
    depth: int = query.DEFAULT_DEPTH,
    token_budget: int = query.DEFAULT_TOKEN_BUDGET,
) -> str:
    """Subgraph text for a natural-language question."""
    graph = load(project_id, root)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return "项目还没有知识图谱（可能尚未建立索引）。"
    nodes_by_id = {node["id"]: node for node in nodes}
    seeds = query.pick_seeds(nodes, question, edges=edges)
    if not seeds:
        return "图中没有匹配的节点。请使用代码中出现的符号名或文件名提问。"
    scored = {node["id"]: score for score, node in query.score_nodes(nodes, question, edges=edges)}
    visited, seen_edges = query.expand(nodes_by_id, edges, seeds, mode=mode, depth=depth)
    return query.render_subgraph_text(
        nodes_by_id,
        edges,
        visited,
        seen_edges,
        seeds=seeds,
        scores=scored,
        token_budget=token_budget,
    )


def context_for_node(
    project_id: int,
    root: Path,
    anchor: str,
    *,
    depth: int = 2,
    token_budget: int = 1200,
) -> str:
    """Neighbourhood of one node, for the chat panel's selected-node prompt."""
    if anchor.startswith("note://"):
        from app.graph import notes, relationships

        return relationships.context(notes.build(), anchor)
    graph = load(project_id, root)
    return query.node_context(graph, anchor, depth=depth, token_budget=token_budget)


def path_between(
    project_id: int,
    root: Path,
    source: str,
    target: str,
    *,
    max_hops: int = 8,
    undirected: bool = True,
) -> str:
    graph = load(project_id, root)
    return query.shortest_path_text(graph, source, target, max_hops=max_hops, undirected=undirected)


def neighbors(
    project_id: int,
    root: Path,
    label: str,
    *,
    relation_filter: str = "",
    token_budget: int = query.DEFAULT_TOKEN_BUDGET,
) -> str:
    graph = load(project_id, root)
    return query.neighbors_text(
        graph,
        label,
        relation_filter=relation_filter,
        token_budget=token_budget,
    )


def summary(graph: dict[str, Any]) -> dict[str, Any]:
    """The bits the UI header shows without shipping every node."""
    stats = dict(graph.get("stats") or {})
    stats["communities"] = len(graph.get("communities") or [])
    stats["built_hash"] = graph.get("built_hash")
    stats["schema_version"] = graph.get("schema_version")
    return stats


def rationale_for(project_id: int, root: Path, anchor: str, *, limit: int = 8) -> str:
    """The recorded explanations attached to a symbol or file.

    This is the query the whole rationale mechanism exists for: "why is this like
    this". The answer comes from the author's docstring or ``# WHY:`` comment, not
    from the model's guess, so it can be quoted as fact.
    """
    graph = load(project_id, root)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    nodes_by_id = {node["id"]: node for node in nodes}
    anchor_id = query.resolve_anchor(nodes, anchor)
    if anchor_id is None:
        return f"node not found: {anchor}"
    target = nodes_by_id[anchor_id]

    lines = [
        f"Node: {target.get('label')}",
        f"  Source: {target.get('path')}:L{target.get('start_line')}",
    ]
    found = 0
    for edge in edges:
        if edge.get("relation") != "rationale_for":
            continue
        if edge.get("target") != anchor_id:
            continue
        source_node = nodes_by_id.get(edge.get("source")) or {}
        label = str(source_node.get("label") or "").strip()
        if not label:
            continue
        found += 1
        if found > limit:
            break
        origin = source_node.get("origin") or "comment"
        lines.append(f"  [{origin}] {label}")
    if found == 0:
        lines.append("  （这个节点没有记录下来的说明）")
    return "\n".join(lines)
