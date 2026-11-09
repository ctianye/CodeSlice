"""Query a built graph: pick seeds, expand a neighbourhood, render as text.

This is the piece that replaces grepping. The design follows graphify's approach
because the problem is identical: an agent asks a natural-language question and
needs a small, high-value slice of the graph, not the whole thing.

1. **Score nodes against the question.** Term overlap weighted by IDF, so a rare
   identifier ("DigestAuth") outranks a ubiquitous word ("data"). Label hits are
   weighted far above path hits so a symbol name the user typed ranks first.
2. **Pick seeds** from the high scorers, one per query term where possible, so a
   question naming two concepts starts from both.
3. **Expand** by BFS (broad context) or DFS (follow one chain), bounded by depth.
4. **Render** to text within a token budget, listing each node's edges with their
   confidence label and direction.

Everything here is pure functions over plain dicts, so it runs without a database
and can be tested directly.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

# Defaults mirror what the MCP-style tool schema exposes, so behaviour is
# predictable whether the call arrives from the UI, a tool, or a test.
DEFAULT_DEPTH = 3
DEFAULT_TOKEN_BUDGET = 2000
MAX_DEPTH = 6

# Rough characters per token. Deliberately conservative (English averages ~4);
# overestimating only means the caller gets slightly less text, never a truncated
# answer that silently lost an edge.
CHARS_PER_TOKEN = 4

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fff]")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of", "in",
    "on", "for", "and", "or", "not", "with", "this", "that", "these", "those",
    "how", "what", "why", "when", "where", "which", "who", "does", "do", "did",
    "can", "could", "should", "would", "it", "its", "as", "at", "by", "from",
    "into", "than", "then", "there", "their", "has", "have", "had", "will",
    "的", "了", "是", "在", "和", "与", "或", "有", "对", "从", "到", "把", "被",
    "这", "那", "个", "们", "怎么", "什么", "如何", "哪", "里", "吗", "呢",
}


def tokenize(text: str) -> list[str]:
    """Lowercased search terms, minus stopwords.

    CJK is split per character: there is no segmenter dependency here, and a
    single CJK character is a word, not a letter, so it carries real signal.

    Single Latin characters are dropped. They match almost every node (``x``,
    ``i``, ``e``) and, with IDF, degenerate into pure noise that outranks the
    identifier the user actually typed.
    """
    terms: list[str] = []
    for raw in _TOKEN_RE.findall(text or ""):
        term = raw.lower()
        if term in _STOPWORDS:
            continue
        if _is_cjk(term):
            terms.append(term)
            continue
        if len(term) >= 2:
            terms.append(term)
    return terms


def _is_cjk(text: str) -> bool:
    return bool(text) and all("\u4e00" <= ch <= "\u9fff" for ch in text)


#: Multipliers applied to a node's score by kind.
#:
#: ``external`` is suppressed almost to zero because a third-party module name is
#: not an answer: a question mentioning "pytest" should lead to the project's own
#: test helpers, not to a node that only records the dependency exists. ``rationale``
#: scores below real symbols but above ``external``: it is excellent supporting
#: evidence ("why does this exist") but should never be the seed of a traversal.
_KIND_WEIGHT = {
    "function": 1.0,
    "class": 1.0,
    "file": 0.85,
    "rationale": 0.6,
    "module": 0.5,
    "external": 0.05,
}


def _node_text(node: dict[str, Any]) -> str:
    """Searchable text of one node.

    Label and path carry the real signal; the kind is included so "class" or
    "function" in a question can steer the search.
    """
    parts = [str(node.get("label") or ""), str(node.get("path") or ""), str(node.get("kind") or "")]
    parent = node.get("parent")
    if parent:
        parts.append(str(parent))
    return " ".join(parts).lower()


def score_nodes(
    nodes: list[dict[str, Any]],
    question: str,
    *,
    edges: list[dict[str, Any]] | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """Rank nodes against a question. Returns ``[(score, node)]``, best first."""
    terms = tokenize(question)
    if not terms or not nodes:
        return []

    texts = {node["id"]: _node_text(node) for node in nodes}

    # IDF: a term present in nearly every node cannot discriminate.
    df: Counter[str] = Counter()
    for text in texts.values():
        for term in set(terms):
            if term in text:
                df[term] += 1
    total = max(1, len(nodes))
    idf = {term: math.log((total + 1) / (df[term] + 1)) + 1.0 for term in terms}

    degree: dict[str, int] = defaultdict(int)
    for edge in edges or ():
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    max_degree = max(degree.values(), default=1) or 1

    scored: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        text = texts[node["id"]]
        label = str(node.get("label") or "").lower()
        score = 0.0
        for term in terms:
            if term not in text:
                continue
            weight = idf[term]
            # An exact label hit is the strongest signal available: the user
            # typed a symbol name, so rank that symbol first.
            if label == term:
                score += weight * 6.0
            elif label.startswith(term):
                score += weight * 3.0
            elif term in label:
                score += weight * 2.0
            else:
                score += weight * 0.5
        if score <= 0:
            continue
        # Mild centrality bonus: between two equally-matching nodes, the one the
        # rest of the code actually uses is the better starting point.
        score *= 1.0 + 0.25 * (degree.get(node["id"], 0) / max_degree)
        score *= _KIND_WEIGHT.get(str(node.get("kind") or ""), 1.0)
        if score <= 0:
            continue
        scored.append((score, node))

    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return scored


def pick_seeds(
    nodes: list[dict[str, Any]],
    question: str,
    *,
    edges: list[dict[str, Any]] | None = None,
    limit: int = 3,
) -> list[str]:
    """Best starting node per query term, then the overall best.

    One seed per term matters for two-concept questions ("how does the agent call
    the tool"): a single highest scorer would only ever anchor one side.
    """
    terms = tokenize(question)
    scored = score_nodes(nodes, question, edges=edges)
    if not scored:
        return []
    seeds: list[str] = []
    for term in terms:
        for _, node in scored:
            label = str(node.get("label") or "").lower()
            path = str(node.get("path") or "").lower()
            if term in label or term in path:
                if node["id"] not in seeds:
                    seeds.append(node["id"])
                break
        if len(seeds) >= limit:
            break
    for _, node in scored:
        if len(seeds) >= limit:
            break
        if node["id"] not in seeds:
            seeds.append(node["id"])
    return seeds[: max(1, limit)]


def _adjacency(edges: list[dict[str, Any]]) -> dict[str, list[tuple[str, dict[str, Any], bool]]]:
    """``{node_id: [(other_id, edge_dict, is_outgoing)]}``."""
    adj: dict[str, list[tuple[str, dict[str, Any], bool]]] = defaultdict(list)
    for edge in edges:
        adj[edge["source"]].append((edge["target"], edge, True))
        adj[edge["target"]].append((edge["source"], edge, False))
    return adj


def expand(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    seeds: list[str],
    *,
    mode: str = "bfs",
    depth: int = DEFAULT_DEPTH,
) -> tuple[set[str], list[tuple[str, str, dict[str, Any], bool]]]:
    """Collect a neighbourhood around ``seeds``.

    Returns ``(visited_node_ids, edges_seen)`` where each edge entry is
    ``(from_id, to_id, edge_dict, outgoing_flag)`` oriented as it was traversed,
    so the rendered text can show outgoing / incoming honestly.

    ``external`` nodes are recorded but never expanded through. A file importing
    ``typing`` is a real fact, yet walking out of it goes nowhere and every file
    does it, so expanding them would spend the whole token budget on stdlib names
    that no question is about.
    """
    depth = max(1, min(int(depth or DEFAULT_DEPTH), MAX_DEPTH))
    adj = _adjacency(edges)
    visited: set[str] = set()
    seen_edges: list[tuple[str, str, dict[str, Any], bool]] = []
    seen_edge_keys: set[tuple[str, str, str]] = set()

    def expandable(node_id: str) -> bool:
        node = nodes_by_id.get(node_id)
        return node is not None and node.get("kind") != "external"

    def record(current: str, other: str, edge: dict[str, Any], outgoing: bool) -> None:
        key = (edge["source"], edge["target"], edge.get("relation", ""))
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        seen_edges.append((current, other, edge, outgoing))

    if mode == "dfs":
        # Depth-first: follow the highest-degree branch to its end first. This is
        # what "trace how A reaches B" wants, where BFS would fan out sideways.
        stack: list[tuple[str, int]] = [(seed, 0) for seed in reversed(seeds)]
        while stack:
            current, level = stack.pop()
            if current in visited or level > depth:
                continue
            if current not in nodes_by_id:
                continue
            visited.add(current)
            neighbours = list(adj.get(current, []))
            neighbours.sort(key=lambda item: -nodes_by_id.get(item[0], {}).get("degree", 0))
            for other, edge, outgoing in reversed(neighbours):
                if other in nodes_by_id:
                    record(current, other, edge, outgoing)
                if other not in visited and level + 1 <= depth and expandable(other):
                    stack.append((other, level + 1))
        return visited, seen_edges

    # BFS (default): breadth-first from every seed at once, so a multi-concept
    # question returns a connected context rather than two separate chains.
    frontier = [seed for seed in seeds if seed in nodes_by_id]
    visited.update(frontier)
    for _level in range(depth):
        next_frontier: list[str] = []
        for current in frontier:
            for other, edge, outgoing in adj.get(current, []):
                if other not in nodes_by_id:
                    continue
                record(current, other, edge, outgoing)
                # Record the edge (the dependency is a fact) but do not walk
                # through an external module: it has nothing further to say.
                if other not in visited and expandable(other):
                    visited.add(other)
                    next_frontier.append(other)
        frontier = next_frontier
        if not frontier:
            break
    return visited, seen_edges


def render_subgraph_text(
    nodes_by_id: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    visited: set[str],
    seen_edges: list[tuple[str, str, dict[str, Any], bool]],
    *,
    seeds: list[str] | None = None,
    scores: dict[str, float] | None = None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Render a subgraph as the text block an agent reads.

    Seeds first, then their neighbours ordered by score: the most relevant context
    must survive the budget cut, not be truncated away because it came last.
    """
    if not visited:
        return "图中没有匹配的节点。请在问题里使用代码中出现的符号名或文件名。"

    budget_chars = max(200, int(token_budget) * CHARS_PER_TOKEN)
    seed_set = [s for s in (seeds or []) if s in visited]
    rest = [nid for nid in visited if nid not in seed_set]
    rest.sort(key=lambda nid: (-(scores or {}).get(nid, 0.0), nid))
    ordered = seed_set + rest

    # Group the rendered edges under the node they belong to.
    by_node: dict[str, list[str]] = defaultdict(list)
    for current, other, edge, outgoing in seen_edges:
        rel = edge.get("relation", "")
        conf = edge.get("confidence", "")
        other_node = nodes_by_id.get(other, {})
        label = str(other_node.get("label") or other)
        arrow = "-->" if outgoing else "<--"
        by_node[current].append(f"    {arrow} {label} [{rel}] [{conf}]")

    lines: list[str] = []
    used = 0
    for index, nid in enumerate(ordered):
        node = nodes_by_id.get(nid)
        if node is None:
            continue
        block = [
            f"Node: {node.get('label') or nid}",
            f"  Kind: {node.get('kind')}  Source: {node.get('path')}:L{node.get('start_line')}",
        ]
        community_label = node.get("community_label")
        if community_label:
            block.append(f"  Community: {node.get('community')} ({community_label})")
        if nid in (scores or {}):
            block.append(f"  Relevance: {scores[nid]:.2f}")
        edges_here = by_node.get(nid, [])
        if edges_here:
            block.append(f"  Connections ({len(edges_here)}):")
            block.extend(edges_here)
        text = "\n".join(block)
        if used + len(text) > budget_chars:
            remaining = len(ordered) - index
            lines.append(
                f"... ({remaining} more nodes omitted by the token budget; "
                f"raise token_budget or lower depth)"
            )
            break
        lines.append(text)
        used += len(text) + 2

    header = f"Subgraph: {len(visited)} nodes, {len(seen_edges)} edges (budget {token_budget} tokens)\n"
    return header + "\n\n".join(lines)


def resolve_anchor(nodes: list[dict[str, Any]], anchor: str) -> str | None:
    """Find the node a UI selection or a typed name refers to.

    Tried in order: exact id (what the UI sends), exact label, a *file* node whose
    path matches, then a symbol inside such a file, then a label substring.

    File nodes get their own step because every symbol in a file shares that file's
    path. Matching "helpers.py" by path alone would otherwise return whichever
    function happened to sort first, which is not what clicking a file means.
    """
    raw = (anchor or "").strip()
    if not raw:
        return None
    for node in nodes:
        if node["id"] == raw:
            return node["id"]
    lowered = raw.lower()
    for node in nodes:
        if str(node.get("label") or "").lower() == lowered:
            return node["id"]

    def path_matches(node: dict[str, Any]) -> bool:
        path = str(node.get("path") or "").lower()
        return path == lowered or path.endswith("/" + lowered)

    for node in nodes:
        if node.get("kind") == "file" and path_matches(node):
            return node["id"]
    for node in nodes:
        if path_matches(node):
            return node["id"]
    for node in nodes:
        if lowered in str(node.get("label") or "").lower():
            return node["id"]
    return None


def node_context(
    graph: dict[str, Any],
    anchor: str,
    *,
    depth: int = 2,
    token_budget: int = 1200,
) -> str:
    """Neighbourhood text around one specific node, for the chat panel.

    The anchor is matched by exact id, then by label, then by path suffix, because
    the UI has a node id while the user may type a bare symbol name.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        return ""
    nodes_by_id = {node["id"]: node for node in nodes}
    anchor_id = resolve_anchor(nodes, anchor)
    if anchor_id is None:
        return ""
    visited, seen_edges = expand(nodes_by_id, edges, [anchor_id], mode="bfs", depth=depth)
    return render_subgraph_text(
        nodes_by_id,
        edges,
        visited,
        seen_edges,
        seeds=[anchor_id],
        token_budget=token_budget,
    )


def shortest_path_text(
    graph: dict[str, Any],
    source: str,
    target: str,
    *,
    max_hops: int = 8,
    undirected: bool = True,
) -> str:
    """Shortest connection between two nodes, ending with its weakest hop.

    The overall confidence is the minimum across hops: a chain is only as
    trustworthy as its weakest link, and saying so stops a two-hop path with one
    inferred edge from reading as fact.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    nodes_by_id = {node["id"]: node for node in nodes}
    start = resolve_anchor(nodes, source)
    end = resolve_anchor(nodes, target)
    if start is None:
        return f"node not found: {source}"
    if end is None:
        return f"node not found: {target}"
    if start == end:
        return f"source and target are the same node: {nodes_by_id[start].get('label')}"

    adj: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for edge in edges:
        adj[edge["source"]].append((edge["target"], edge))
        if undirected:
            adj[edge["target"]].append((edge["source"], edge))

    max_hops = max(1, min(int(max_hops or 8), 15))
    # BFS over hops; each queue entry keeps its full path so the chain can be
    # rendered with per-hop relations.
    queue: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [(start, [])]
    seen = {start}
    found: list[tuple[str, dict[str, Any]]] | None = None
    while queue and found is None:
        current, path_edges = queue.pop(0)
        if len(path_edges) >= max_hops:
            continue
        for other, edge in adj.get(current, []):
            if other in seen:
                continue
            step = path_edges + [(current, edge)]
            if other == end:
                found = step
                break
            seen.add(other)
            queue.append((other, step))

    if found is None:
        return f"no path between {source} and {target} within {max_hops} hops"

    ranks = {"EXTRACTED": 3, "INFERRED": 2, "AMBIGUOUS": 1}
    weakest_rank = min(ranks.get(edge.get("confidence", ""), 0) for _, edge in found)
    weakest = {3: "EXTRACTED", 2: "INFERRED", 1: "AMBIGUOUS", 0: "UNKNOWN"}[weakest_rank]

    lines = [f"Shortest path ({len(found)} hops):"]
    for current, edge in found:
        target_id = edge["target"] if edge["source"] == current else edge["source"]
        current_node = nodes_by_id.get(current, {})
        target_node = nodes_by_id.get(target_id, {})
        lines.append(
            f"  {current_node.get('label')} -{edge.get('relation')}-> "
            f"{target_node.get('label')} [{edge.get('confidence')}]"
        )
    lines.append(f"Path confidence: {weakest} (weakest hop)")
    return "\n".join(lines)


def neighbors_text(
    graph: dict[str, Any],
    label: str,
    *,
    relation_filter: str = "",
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """Direct neighbours of one node, grouped by direction."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    nodes_by_id = {node["id"]: node for node in nodes}
    anchor = resolve_anchor(nodes, label)
    if anchor is None:
        return f"node not found: {label}"
    wanted = (relation_filter or "").strip().lower()

    outgoing: list[str] = []
    incoming: list[str] = []
    for edge in edges:
        if wanted and wanted not in str(edge.get("relation", "")).lower():
            continue
        if edge["source"] == anchor:
            other = nodes_by_id.get(edge["target"], {})
            outgoing.append(
                f"  --> {other.get('label')} [{edge.get('relation')}] [{edge.get('confidence')}]"
            )
        elif edge["target"] == anchor:
            other = nodes_by_id.get(edge["source"], {})
            incoming.append(
                f"  <-- {other.get('label')} [{edge.get('relation')}] [{edge.get('confidence')}]"
            )

    node = nodes_by_id[anchor]
    lines = [
        f"Node: {node.get('label')}",
        f"  Source: {node.get('path')}:L{node.get('start_line')}",
        f"  Kind: {node.get('kind')}  Degree: {node.get('degree')}",
    ]
    if outgoing:
        lines.append(f"Outgoing ({len(outgoing)}):")
        lines.extend(outgoing)
    if incoming:
        lines.append(f"Incoming ({len(incoming)}):")
        lines.extend(incoming)
    if not outgoing and not incoming:
        lines.append("  (no connections match the filter)")

    budget_chars = max(200, int(token_budget) * CHARS_PER_TOKEN)
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) > budget_chars:
            out.append("... (truncated by the token budget)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)
