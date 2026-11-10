"""Persist the knowledge graph in SQLite and serve it without re-reading source.

SQLite (the ``graphs`` table, see ``app.core.db``) is the primary store. The old
JSON file (``<root>/.codeslice/graph.json``) is now a *legacy* format: it is still
read once to import a graph an older release wrote, and it is still written when
no project row exists (the preview script and ad-hoc fixtures), but it is never
consulted while a database row is present.

Why this module exists
----------------------
The graph used to be re-validated on every read with a SHA-256 walk of the whole
source tree, and rebuilt whenever that hash moved. The walk alone dominated a
click on a large project, and the rebuild made the first click after any edit
worse. The graph is now built once, persisted, and served from one primary-key
lookup.

Invalidation rules (the invariant this module guarantees)
---------------------------------------------------------
A persisted code graph is served as fact only while *all* of these hold. Each
rule is cheap to check; together they make a stale graph impossible to serve
through the normal lifecycle.

1. **Schema version.** ``graphs.schema_version`` must equal
   ``app.graph.SCHEMA_VERSION``. Bumping the builder's rules therefore retires
   every stored graph, so no read can cite relationships the current builder no
   longer produces. Checked with the row fetch.
2. **Marker.** ``graphs.built_hash`` must equal :func:`freshness_token` — a
   string of the graph-format version, ``SCHEMA_VERSION`` and a digest of the
   project's ``excluded`` globs. All inputs are columns of the ``projects`` row,
   so the check reads no source bytes and walks no directories.
3. **Source content.** The *indexer* is the single authority for "the corpus
   changed". When a run re-chunks or prunes any file it deletes the row
   (:func:`invalidate`, called from ``app.indexing.indexer``),
   so the next read rebuilds. A run that skipped every file leaves the row
   alone: opening a project must not throw away a perfectly good graph.
4. **Epoch.** Every invalidation also bumps ``settings['graph_epoch:<id>']``.
   :func:`build_and_store` samples it *before* reading the tree and re-checks it
   inside the write transaction: a build that overlaps an index run is discarded
   instead of persisted, so a graph computed from a corpus that has already
   changed can never carry a fresh marker. Without this, the seconds-long build
   window was the one way to end up serving stale data as fact forever.
5. **Explicit rebuild.** ``POST /projects/{id}/graph/build`` (``force_rebuild``)
   replaces the row; deleting a project / clearing its index drops it.

Read-path contract: a warm read issues **one** statement (``_row_and_marker``,
which also fetches the marker's inputs) and one ``json.loads``. It never calls
``builder.collect_sources``, ``Path.read_text``/``read_bytes``, or
``index_fingerprint``. ``tests/test_graph_cache.py`` asserts this directly by
instrumenting those calls.

One-time upgrade path
---------------------
A row written by an older release (or a legacy ``graph.json`` import) may carry a
full content hash in ``built_hash`` instead of a marker. Rather than forcing every
existing user through one re-index, :func:`ensure_fresh` verifies such a row once
against :func:`index_fingerprint` (the old, expensive full-tree hash), and if it
still matches, re-stamps the marker. That walk happens at most once per project
and only on the path that would otherwise have rebuilt from scratch. The legacy
file is then renamed aside (``graph.json.stale``) so no later start can repeat
that walk. A legacy file that no longer matches is simply rebuilt, reusing the
sources that verification already read.

Every cache hit/miss is logged, and the ``rebuilt`` flag the API returns comes
from this module, so "was this served from cache?" stays observable.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from app.core.db import GRAPH_SCOPE_CODE, bump_graph_epoch, get_conn, utcnow, write_conn
from app.graph import GRAPH_DIRNAME, GRAPH_FILENAME, SCHEMA_VERSION, builder
from app.graph.cluster import apply_communities, detect_communities

log = logging.getLogger(__name__)

# Bumped when the *marker format* changes (not when the builder's output changes:
# that is SCHEMA_VERSION). An unrecognised marker simply never matches, so a bump
# costs one rebuild per project and is the safe direction.
TOKEN_VERSION = 1

# One process-wide lock per project so two concurrent requests cannot both
# rebuild and race on the row. Cheap: rebuilds are seconds, and the same user
# drives both requests.
_BUILD_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()

# Layouts are a client-provided projection of the persisted graph. A wide graph
# is still only a few hundred KB of coordinates; the cap keeps a malformed
# request from filling the business database.
MAX_LAYOUT_BYTES = 8 * 1024 * 1024
MAX_LAYOUT_POSITIONS = 50_000


def topology_key(graph: dict[str, Any]) -> str:
    """Stable fingerprint for one graph topology.

    Node ids and resolved edges are enough: a community recolor or a note title
    edit does not invalidate a perfectly good layout, while an added/removed
    relationship does. The same key is used by code and notes graphs.
    """
    digest = hashlib.sha256()
    for node in sorted(graph.get("nodes") or [], key=lambda item: str(item.get("id") or "")):
        digest.update(str(node.get("id") or "").encode("utf-8"))
        digest.update(b"\x00")
    def edge_key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("source") or ""),
            str(item.get("target") or ""),
            str(item.get("relation") or ""),
        )

    for edge in sorted(graph.get("edges") or [], key=edge_key):
        digest.update(str(edge.get("source") or "").encode("utf-8"))
        digest.update(b"\x01")
        digest.update(str(edge.get("target") or "").encode("utf-8"))
        digest.update(b"\x01")
        digest.update(str(edge.get("relation") or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:32]


def _valid_position(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return x, y


def load_layout(project_id: int, scope: str, layout_key: str) -> dict[str, dict[str, float]]:
    """Coordinates saved for this exact topology, or an empty mapping."""
    if not layout_key:
        return {}
    with get_conn() as conn:
        row = conn.execute(
            "SELECT positions FROM graph_layouts WHERE project_id = ? AND scope = ? AND layout_key = ?",
            (project_id, scope, layout_key),
        ).fetchone()
    if row is None:
        return {}
    try:
        raw = json.loads(row["positions"])
    except (TypeError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, float]] = {}
    for node_id, value in raw.items():
        position = _valid_position(value)
        if position is not None:
            out[str(node_id)] = {"x": position[0], "y": position[1]}
    return out


def save_layout(project_id: int, scope: str, layout_key: str, positions: dict[str, Any]) -> None:
    """Persist one settled layout, replacing older keys of the same graph.

    Only the newest topology is useful once it changes. Keeping the table small
    also makes the next read predictable instead of accumulating every rebuild.
    """
    if not layout_key or not positions:
        return
    clean: dict[str, dict[str, float]] = {}
    for node_id, value in positions.items():
        position = _valid_position(value)
        if position is not None:
            clean[str(node_id)] = {"x": position[0], "y": position[1]}
        if len(clean) >= MAX_LAYOUT_POSITIONS:
            break
    if not clean:
        return
    body = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > MAX_LAYOUT_BYTES:
        raise ValueError("graph layout is too large")
    with write_conn() as conn:
        conn.execute(
            "DELETE FROM graph_layouts WHERE project_id = ? AND scope = ? AND layout_key <> ?",
            (project_id, scope, layout_key),
        )
        conn.execute(
            "INSERT INTO graph_layouts (project_id, scope, layout_key, positions, node_count, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(project_id, scope, layout_key) DO UPDATE SET "
            "positions = excluded.positions, node_count = excluded.node_count, updated_at = excluded.updated_at",
            (project_id, scope, layout_key, body, len(clean), utcnow()),
        )


def _build_lock(project_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _BUILD_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _BUILD_LOCKS[project_id] = lock
        return lock


def graph_path(root: Path) -> Path:
    """Legacy on-disk location, kept for import and for project-less fixtures."""
    return root / GRAPH_DIRNAME / GRAPH_FILENAME


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON via a sibling temp file + replace (legacy file path only).

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


# --------------------------------------------------------------------------- #
# the cheap freshness marker
# --------------------------------------------------------------------------- #
def _project_row(project_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id, path, excluded FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row is not None else None


def freshness_token(project_id: int) -> str | None:
    """The marker stored in ``graphs.built_hash``.

    Deliberately derived from database columns only (format version, schema
    version, the project's extra ignore globs). Source changes are *not* hashed
    here: the indexer deletes the cache row when a run actually touches the
    corpus, which is both cheaper and more accurate than re-hashing every file on
    every click. ``None`` means the project row is gone.
    """
    project = _project_row(project_id)
    if project is None:
        return None
    return _token_from_excluded(project.get("excluded"))


def _sources_digest(sources: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(f"schema:{SCHEMA_VERSION}\x00".encode())
    for rel, data in sources:
        digest.update(rel.encode())
        digest.update(b"\x00")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def index_fingerprint(project_id: int) -> str:
    """Full content hash of the project's source tree.

    **Do not call this on a read path.** It walks every file and hashes its bytes:
    it exists for rebuilding, for the preview script, and for the one-time
    verification of a legacy graph built by an older release (see
    :func:`ensure_fresh`). Content-driven invalidation is the indexer's job.
    """
    from app.services import paths

    project = _project_row(project_id)
    if project is None:
        return "no-project"
    try:
        root = paths.resolve_root(project["path"])
    except Exception:
        return "unresolvable"
    return _sources_digest(builder.collect_sources(root))


# --------------------------------------------------------------------------- #
# the graphs table
# --------------------------------------------------------------------------- #
def _cached_row(project_id: int, scope: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT payload, built_hash, schema_version FROM graphs WHERE project_id = ? AND scope = ?",
            (project_id, scope),
        ).fetchone()


def _row_and_marker(project_id: int, scope: str):
    """One query for both the cached row and the inputs of :func:`freshness_token`.

    Joining here rather than reading ``projects`` separately keeps a warm read to
    a single statement: this is the hot path a graph click takes.
    """
    with get_conn() as conn:
        return conn.execute(
            """SELECT g.payload, g.built_hash, g.schema_version, p.excluded, p.id AS project_exists
                 FROM graphs g
                 LEFT JOIN projects p ON p.id = g.project_id
                WHERE g.project_id = ? AND g.scope = ?""",
            (project_id, scope),
        ).fetchone()


def _token_from_excluded(excluded: Any, *, project_exists: bool = True) -> str:
    if not project_exists:
        # Mirrors freshness_token() returning None for a vanished project row.
        return "no-project"
    excluded_digest = hashlib.sha256(str(excluded or "").encode("utf-8")).hexdigest()[:16]
    return f"gv{TOKEN_VERSION}:s{SCHEMA_VERSION}:x{excluded_digest}"


def _decode(row) -> dict[str, Any] | None:
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        log.warning("discarding unreadable persisted graph (schema v%s)", row["schema_version"], exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def load_cached(project_id: int, scope: str = GRAPH_SCOPE_CODE, version: int | None = None) -> dict[str, Any] | None:
    """The last persisted graph, or ``None``. Never validates, builds or walks.

    Returns a fresh dict per call, so a caller mutating the result cannot corrupt
    what the next reader sees. A row written under a different schema version is
    reported as missing: it describes a graph the current builder would not make.
    """
    expected = SCHEMA_VERSION if version is None else int(version)
    row = _cached_row(project_id, scope)
    if row is None or int(row["schema_version"]) != expected:
        return None
    return _decode(row)


def save_cached(
    project_id: int,
    scope: str,
    payload: dict[str, Any],
    *,
    built_hash: str | None = None,
    version: int | None = None,
) -> None:
    """Persist a graph payload. Replaces any previous row for the same slot.

    Intended for fixtures and non-build callers (``write_graph``): it does not
    consult the epoch, so it can legitimately write a payload the builder would
    have discarded.
    """
    body = {**payload, "built_hash": built_hash if built_hash is not None else payload.get("built_hash")}
    with write_conn() as conn:
        _write_row(conn, project_id, scope, body, version=version)


def invalidate(project_id: int, scope: str = GRAPH_SCOPE_CODE) -> None:
    """Drop one persisted graph so the next read rebuilds it.

    Called by the indexer when a run changed the corpus, by ``clear_index``, and
    by the notes storage layer on any note/folder mutation. Also bumps the epoch
    so a rebuild that is *currently* reading the tree knows its result is already
    obsolete and declines to persist it.
    """
    with write_conn() as conn:
        conn.execute("DELETE FROM graphs WHERE project_id = ? AND scope = ?", (project_id, scope))
        bump_graph_epoch(conn, project_id)
    log.debug("invalidated %s graph for project %s", scope, project_id)


def _restamp(project_id: int, scope: str, token: str) -> None:
    with write_conn() as conn:
        conn.execute(
            "UPDATE graphs SET built_hash = ? WHERE project_id = ? AND scope = ?",
            (token, project_id, scope),
        )


# --------------------------------------------------------------------------- #
# project lookup by path (write_graph's compatibility contract)
# --------------------------------------------------------------------------- #
def _project_id_for_root(root: Path) -> int | None:
    """Resolve the ``projects`` row a graph is being written for.

    Tries the path as given and fully resolved, then case-insensitively: Windows
    paths are case-insensitive and an older project row may have been created
    through a different spelling. ``None`` means "no project row" — the caller
    then falls back to the legacy JSON file so the preview script keeps working.
    """
    from app.storage import repo

    candidates = [str(root), str(Path(root).resolve())]
    for candidate in candidates:
        project = repo.find_project_by_path(candidate)
        if project is not None:
            return int(project["id"])
    with get_conn() as conn:
        for candidate in candidates:
            row = conn.execute("SELECT id FROM projects WHERE path = ? COLLATE NOCASE LIMIT 1", (candidate,)).fetchone()
            if row is not None:
                return int(row["id"])
    return None


# --------------------------------------------------------------------------- #
# legacy JSON compatibility
# --------------------------------------------------------------------------- #
def read_graph(root: Path) -> dict[str, Any] | None:
    """The legacy ``graph.json``, or ``None`` when missing/unreadable/stale."""
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
    """Persist a graph for the project rooted at ``root``.

    Kept as the single write entry point so fixtures (``tests``,
    ``scripts/graph_preview.py``) and the builder all go through the same rule:
    resolve the project row by path and store in SQLite; when there is no project
    row, write the legacy JSON file instead.

    A payload without a ``built_hash`` is stamped with the current marker, so
    "write then read" is a cache hit rather than a surprise rebuild. A payload
    that *does* carry a hash keeps it: that is what a graph written by an older
    release looks like, and it must be verified rather than trusted.
    """
    project_id = _project_id_for_root(root)
    if project_id is None:
        write_json_atomic(graph_path(root), data)
        return
    version = data.get("schema_version")
    marker = data.get("built_hash")
    if not marker:
        marker = freshness_token(project_id) or "no-project"
    save_cached(
        project_id,
        GRAPH_SCOPE_CODE,
        data,
        built_hash=marker,
        version=int(version) if isinstance(version, int) else SCHEMA_VERSION,
    )


def load_chunks(project_id: int) -> list[dict[str, Any]]:
    """Kept for modules that still import it; the AST builder no longer uses it."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path, language, kind, symbol, parent_symbol, start_line, end_line, content "
            "FROM chunks WHERE project_id = ? AND path NOT LIKE 'note://%' "
            "ORDER BY path, start_line",
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# build / refresh
# --------------------------------------------------------------------------- #
def build_and_store(
    project_id: int,
    root: Path,
    *,
    sources: list[tuple[str, bytes]] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Rebuild the graph from source and persist it.

    ``sources`` lets the caller that already read the tree (the legacy-import
    verification path) hand them over instead of re-reading every file.

    The epoch is sampled *before* reading: if an index run invalidates the graph
    while this build is running, the result is discarded rather than persisted
    with a fresh marker (see :func:`_persist_if_current`).
    """
    epoch = _read_epoch_now(project_id)
    graph = builder.build_graph(sources=sources, root=root)
    assignment, summaries = detect_communities(graph["nodes"], graph["edges"])
    nodes = apply_communities(graph["nodes"], assignment, summaries)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "built_hash": token or freshness_token(project_id) or "no-project",
        "nodes": nodes,
        "edges": graph["edges"],
        "communities": summaries,
        "stats": graph["stats"],
    }
    stored = _persist_if_current(project_id, payload, expected_epoch=epoch)
    log.info(
        "%s graph for project %s: %d nodes, %d edges, %d communities, %d rationale",
        "built" if stored else "discarded a stale build of the",
        project_id,
        len(nodes),
        len(graph["edges"]),
        len(summaries),
        graph["stats"].get("rationale", 0),
    )
    return payload


def _persist_if_current(project_id: int, payload: dict[str, Any], *, expected_epoch: int | None) -> bool:
    """Write the built payload unless the corpus changed while we were building.

    Reading the tree takes seconds. If an index run invalidated the graph in that
    window, this payload describes a corpus that no longer exists; persisting it
    (and its marker) would serve stale relationships as fact, because nothing
    would invalidate them again. So the epoch is compared *inside* the write
    transaction, where a concurrent invalidation cannot slip between check and
    insert. Returns whether the payload was stored.
    """
    with write_conn() as conn:
        if expected_epoch is not None:
            current = _read_epoch(conn, project_id)
            if current != expected_epoch:
                log.warning(
                    "discarding a graph built while project %s was re-indexed (epoch %s -> %s); "
                    "the next read rebuilds from the new corpus",
                    project_id,
                    expected_epoch,
                    current,
                )
                return False
        _write_row(conn, project_id, GRAPH_SCOPE_CODE, payload)
    return True


def _read_epoch(conn: Any, project_id: int) -> int:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"graph_epoch:{project_id}",)).fetchone()
    try:
        return int(row["value"]) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def _read_epoch_now(project_id: int) -> int:
    with get_conn() as conn:
        return _read_epoch(conn, project_id)


def _write_row(conn: Any, project_id: int, scope: str, payload: dict[str, Any], *, version: int | None = None) -> None:
    conn.execute(
        """INSERT INTO graphs
               (project_id, scope, payload, built_hash, schema_version,
                node_count, edge_count, built_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(project_id, scope) DO UPDATE SET
               payload = excluded.payload,
               built_hash = excluded.built_hash,
               schema_version = excluded.schema_version,
               node_count = excluded.node_count,
               edge_count = excluded.edge_count,
               built_at = excluded.built_at""",
        (
            project_id,
            scope,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            payload.get("built_hash"),
            SCHEMA_VERSION if version is None else int(version),
            len(payload.get("nodes") or []),
            len(payload.get("edges") or []),
            utcnow(),
        ),
    )


def _import_legacy(project_id: int, root: Path) -> tuple[dict[str, Any] | None, list[tuple[str, bytes]] | None]:
    """Import an older release's ``graph.json`` instead of forcing a rebuild.

    Verified once against the old full-tree fingerprint: if the source moved on
    since that file was written, importing it would serve a stale graph and the
    caller rebuilds instead. Unchanged projects import for free.

    Returns ``(graph, sources)``: the imported payload when it is usable, else
    ``None`` plus the already-read source tree (if the verification had to read
    it) so the rebuild can reuse it rather than walking a second time.
    """
    legacy = read_graph(root)
    if legacy is None:
        return None, None
    stored_hash = legacy.get("built_hash")
    token = freshness_token(project_id) or "no-project"
    sources: list[tuple[str, bytes]] | None = None
    if stored_hash:
        from app.services import paths

        sources = builder.collect_sources(paths.resolve_root(str(_project_row(project_id)["path"])))
        if stored_hash != _sources_digest(sources):
            log.info("legacy graph for project %s is stale (content changed), rebuilding", project_id)
            return None, sources
    imported = {**legacy, "built_hash": token}
    save_cached(project_id, GRAPH_SCOPE_CODE, imported, built_hash=token)
    log.info(
        "imported legacy graph.json for project %s: %d nodes, %d edges",
        project_id,
        len(imported.get("nodes") or []),
        len(imported.get("edges") or []),
    )
    return imported, None


def _retire_legacy_file(root: Path) -> None:
    """Delete the legacy ``graph.json`` once SQLite holds the authoritative copy.

    It is a derived cache, never user data, and leaving a stale one behind would
    make every future read re-walk the tree to re-verify it. Renaming keeps the
    old bytes around for a postmortem without any code path ever reading them.
    """
    path = graph_path(root)
    if not path.is_file():
        return
    with contextlib.suppress(OSError):
        path.replace(path.with_name(path.name + ".stale"))
        log.info("retired legacy graph file at %s", path)


def ensure_fresh(project_id: int, root: Path) -> tuple[dict[str, Any], bool]:
    """Return ``(graph, rebuilt)`` for a project, rebuilding only when needed.

    The hit path is **one** primary-key lookup and one ``json.loads`` — no
    directory walk, no source bytes, no second statement for the project row (the
    marker's inputs come back in the same joined row). See the module docstring
    for the full invalidation contract.
    """
    with _build_lock(project_id):
        row = _row_and_marker(project_id, GRAPH_SCOPE_CODE)
        if row is not None and int(row["schema_version"]) == SCHEMA_VERSION:
            token = _token_from_excluded(row["excluded"], project_exists=row["project_exists"] is not None)
            payload = _decode(row)
            if payload is not None:
                stored = str(row["built_hash"] or "")
                if stored == token:
                    log.debug(
                        "graph cache hit for project %s (%d nodes, %d edges)",
                        project_id,
                        len(payload.get("nodes") or []),
                        len(payload.get("edges") or []),
                    )
                    return payload, False
                # A row written by an older release records the full content
                # hash. Verify it once, then re-stamp so later reads are O(1).
                if stored and stored == index_fingerprint(project_id):
                    _restamp(project_id, GRAPH_SCOPE_CODE, token)
                    log.debug("graph cache hit for project %s (re-stamped legacy marker)", project_id)
                    return payload, False
                log.info("graph marker changed for project %s (%s -> %s), rebuilding", project_id, stored, token)

        token = freshness_token(project_id) or "no-project"
        if row is None:
            # Nothing persisted yet: an existing legacy file may spare a rebuild.
            imported, sources = _import_legacy(project_id, root)
            if imported is not None:
                _retire_legacy_file(root)
                return imported, False
            if sources is not None:
                rebuilt_payload = build_and_store(project_id, root, sources=sources, token=token)
                _retire_legacy_file(root)
                return rebuilt_payload, True

        log.info("graph cache miss for project %s, rebuilding", project_id)
        payload = build_and_store(project_id, root, token=token)
        _retire_legacy_file(root)
        return payload, True


def force_rebuild(project_id: int, root: Path) -> dict[str, Any]:
    with _build_lock(project_id):
        return build_and_store(project_id, root, token=freshness_token(project_id) or "no-project")
