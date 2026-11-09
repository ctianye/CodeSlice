"""Read/write the per-project ``graph.json`` and keep it fresh.

The graph is stored next to the project (``<root>/.codeslice/graph.json``) rather
than in the app data directory, for two reasons: the project root is already the
one path every service resolves against, and a graph copied with the project keeps
working. ``.codeslice`` is in ``paths.DEFAULT_IGNORES`` so indexing never walks it.

Freshness is a content hash of the index, not a clock. ``built_hash`` records what
the graph was derived from; if the indexed content changes at all the next read
rebuilds. That costs a rebuild on any edit rather than a subtle stale graph, which
is the right trade for a structure the agent cites as fact.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.graph import GRAPH_DIRNAME, GRAPH_FILENAME, SCHEMA_VERSION, builder
from app.graph.cluster import apply_communities, detect_communities

log = logging.getLogger(__name__)

# One process-wide lock per project so two concurrent requests cannot both
# rebuild and race on the file. Cheap: rebuilds are seconds, and the same user
# drives both requests.
_BUILD_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _build_lock(project_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _BUILD_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _BUILD_LOCKS[project_id] = lock
        return lock


def graph_path(root: Path) -> Path:
    return root / GRAPH_DIRNAME / GRAPH_FILENAME


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON via a sibling temp file + replace.

    ``graph.json`` is read by the API and by the agent while a rebuild may be
    running; a half-written file would parse as invalid and read as "no graph".
    Replacing the file guarantees readers see either the old graph or the new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a temp file behind on failure; the caller reports the error.
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def index_fingerprint(project_id: int) -> str:
    """Hash of the project's *source* files, used to decide when to rebuild.

    Switched from a hash of the ``chunks`` table to a hash of the source tree: the
    graph is now derived from the AST, not from chunk rows, so the chunk table is
    no longer the thing it depends on. Hashing ``path + mtime + size`` is what makes
    an edit to one file trigger a rebuild while an untouched project stays cached.

    Read-only on the database and cheap (one filesystem walk), which matters because
    this runs on every graph read.
    """
    from app.core.db import get_conn
    from app.services import paths

    with get_conn() as conn:
        row = conn.execute("SELECT path FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        return "no-project"
    try:
        root = paths.resolve_root(row["path"])
    except Exception:
        return "unresolvable"

    digest = hashlib.sha256()
    digest.update(f"schema:{SCHEMA_VERSION}\x00".encode())
    for rel, data in builder.collect_sources(root):
        digest.update(rel.encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def load_chunks(project_id: int) -> list[dict[str, Any]]:
    """Kept for modules that still import it; the AST builder no longer uses it."""
    from app.core.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path, language, kind, symbol, parent_symbol, start_line, end_line, content "
            "FROM chunks WHERE project_id = ? AND path NOT LIKE 'note://%' "
            "ORDER BY path, start_line",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def read_graph(root: Path) -> dict[str, Any] | None:
    """The stored graph, or ``None`` when missing/unreadable/schema-stale."""
    path = graph_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("ignoring unreadable graph at %s", path, exc_info=True)
        return None
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        # A graph built by older rules must not be served: it would describe
        # relationships the current builder no longer produces.
        return None
    return data


def write_graph(root: Path, data: dict[str, Any]) -> None:
    write_json_atomic(graph_path(root), data)


def build_and_store(project_id: int, root: Path) -> dict[str, Any]:
    """Rebuild the graph from source and persist it."""
    graph = builder.build_graph(root=root)
    assignment, summaries = detect_communities(graph["nodes"], graph["edges"])
    nodes = apply_communities(graph["nodes"], assignment, summaries)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "built_hash": index_fingerprint(project_id),
        "nodes": nodes,
        "edges": graph["edges"],
        "communities": summaries,
        "stats": graph["stats"],
    }
    write_graph(root, payload)
    log.info(
        "built graph for project %s: %d nodes, %d edges, %d communities, %d rationale",
        project_id,
        len(nodes),
        len(graph["edges"]),
        len(summaries),
        graph["stats"].get("rationale", 0),
    )
    return payload


def ensure_fresh(project_id: int, root: Path) -> tuple[dict[str, Any], bool]:
    """Load the stored graph, rebuilding when the index moved on.

    Returns ``(graph, rebuilt)``. The hash check is cheap (one query) because the
    alternative — an agent answering from a graph that no longer matches the code —
    is the failure this whole module exists to prevent.
    """
    fingerprint = index_fingerprint(project_id)
    with _build_lock(project_id):
        stored = read_graph(root)
        if stored is not None and stored.get("built_hash") == fingerprint:
            return stored, False
        return build_and_store(project_id, root), True


def force_rebuild(project_id: int, root: Path) -> dict[str, Any]:
    with _build_lock(project_id):
        return build_and_store(project_id, root)
