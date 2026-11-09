"""Community detection over the code graph.

Uses NetworkX's built-in Louvain so the backend gains no second graph library:
``networkx`` is pure Python, ships everywhere and packages cleanly. Leiden would
produce slightly better communities, but it needs ``igraph``/``leidenalg`` (C
extensions), and this graph is small enough that the difference is not worth a
compiled dependency in an Electron-bundled backend.

The rule that matters here: **islands must not each become a community.**
A graph of forty unrelated files would otherwise report forty "communities", which
is true but useless: the UI legend becomes a list of one-member entries. Nodes
with no edges at all are pooled into one explicitly-labelled fallback community.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import networkx as nx

# A singleton community is noise, not a grouping: pool it.
MIN_COMMUNITY_SIZE = 2

# Label for the pooled stragglers. Kept in Chinese because it is rendered as-is in
# the legend; an English "Uncategorised" would read as a category name to a user
# who is reading the rest of the interface in Chinese.
POOLED_LABEL = "未分类"


def _to_nx(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> nx.Graph:
    graph = nx.Graph()
    for node in nodes:
        graph.add_node(node["id"], **node)
    for edge in edges:
        graph.add_edge(edge["source"], edge["target"], relation=edge["relation"])
    return graph


def detect_communities(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    seed: int = 0,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Assign each node a community id; return ``({node_id: cid}, summaries)``.

    ``seed`` keeps the result stable across rebuilds of an unchanged graph, so the
    colours do not reshuffle every time the user reindexes.
    """
    if not nodes:
        return {}, []

    graph = _to_nx(nodes, edges)

    # Isolated nodes carry no grouping information. Separating them up front is
    # what keeps a project with many standalone files (configs, small scripts)
    # from drowning the real clusters in one-member communities.
    connected = graph.subgraph([n for n in graph.nodes() if graph.degree(n) > 0]).copy()
    isolated = [n for n in graph.nodes() if graph.degree(n) == 0]

    if connected.number_of_nodes() == 0:
        communities: list[set[str]] = []
    else:
        try:
            raw = nx.community.louvain_communities(connected, weight=None, seed=seed)
            communities = [set(group) for group in raw]
        except Exception:
            # A degenerate graph (self loops only, or a single node) should
            # degrade to "one big community", not 500 the request.
            communities = [set(connected.nodes())]

    # Pool singletons so the legend stays readable.
    large = [group for group in communities if len(group) >= MIN_COMMUNITY_SIZE]
    pooled: set[str] = set(isolated)
    for group in communities:
        if len(group) < MIN_COMMUNITY_SIZE:
            pooled |= group

    ordered = sorted(large, key=lambda group: (-len(group), min(group)))
    summaries: list[dict[str, Any]] = []
    assignment: dict[str, int] = {}

    for cid, group in enumerate(ordered):
        for nid in group:
            assignment[nid] = cid
        summaries.append(_summarise(cid, group, graph))

    if pooled:
        cid = len(ordered)
        for nid in pooled:
            assignment[nid] = cid
        summaries.append(_summarise(cid, pooled, graph, pooled=True))

    # Two communities can be named after the same dominant file (one large
    # cluster and one small satellite in the same module). The legend would then
    # show two identical rows with different colours, which reads as a bug. Give
    # each duplicate a representative symbol so the rows are distinguishable.
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in summaries:
        by_label[str(summary["label"])].append(summary)
    for label, duplicates in by_label.items():
        if len(duplicates) < 2:
            continue
        for summary in duplicates:
            representative = str(summary.get("representative") or "")
            if representative and representative != label:
                summary["label"] = f"{label} · {representative}"

    return assignment, summaries


def _summarise(
    cid: int,
    group: set[str],
    graph: nx.Graph,
    *,
    pooled: bool = False,
) -> dict[str, Any]:
    """Name a community after the file most of its members live in.

    Naming it after the most-connected *node* reads badly: the hub of a
    backend-storage cluster is often ``NotFound`` or ``get_conn``, so the legend
    ends up labelled with an exception class. The file that owns the majority of
    the members is what the user actually recognises as the subsystem.

    A model call could produce a nicer phrase, but it would make the build
    non-deterministic and cost tokens for a string the file name already conveys.
    """
    members = sorted(group)

    # Count which file each member belongs to; the majority wins.
    file_counts: dict[str, int] = {}
    for nid in members:
        path = str(graph.nodes[nid].get("path") or "").strip()
        if not path:
            continue
        file_counts[path] = file_counts.get(path, 0) + 1

    label = POOLED_LABEL
    kind = ""
    hub_id = max(members, key=lambda nid: (graph.degree(nid), nid))
    if not pooled and file_counts:
        best_path = max(file_counts.items(), key=lambda item: (item[1], item[0]))[0]
        label = best_path.rsplit("/", 1)[-1]
        if len(file_counts) > 1:
            # Cross-file community: saying so avoids the impression that this is
            # one file's internals.
            label = f"{label} +{len(file_counts) - 1}"
        kind = "file"
        # Anchor on that file's own node when it has one, so double-clicking the
        # community lands somewhere meaningful.
        for nid in members:
            node = graph.nodes[nid]
            if node.get("kind") == "file" and str(node.get("path") or "") == best_path:
                hub_id = nid
                break
    if not kind:
        kind = str(graph.nodes[hub_id].get("kind") or "")

    # Highest-degree real symbol, used only to disambiguate duplicate labels.
    symbols = [nid for nid in members if graph.nodes[nid].get("kind") in ("function", "class")]
    representative = ""
    if symbols:
        representative = str(
            graph.nodes[max(symbols, key=lambda nid: (graph.degree(nid), nid))].get("label") or ""
        )

    return {
        "id": cid,
        "label": label,
        "size": len(members),
        "hub": hub_id,
        "kind": kind,
        "pooled": pooled,
        "representative": representative,
    }


def apply_communities(
    nodes: list[dict[str, Any]],
    assignment: dict[str, int],
    summaries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return new node dicts with ``community`` / ``community_label`` filled in."""
    names = {item["id"]: item["label"] for item in summaries}
    out = []
    for node in nodes:
        cid = assignment.get(node["id"], 0)
        out.append({**node, "community": cid, "community_label": names.get(cid, "")})
    return out
