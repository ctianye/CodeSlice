"""Per-occurrence symbol index: definitions and references for editor navigation.

The knowledge graph deliberately keeps one edge per ``(caller, callee)`` pair, so
it cannot answer "what is the identifier under the cursor?" when the same function
is called twenty times. This module stores the missing dimension:

* ``symbol_defs`` — one row per definition (file/class/function), with its exact
  span, signature and the docstring/WHY note the graph already collected.
* ``symbol_refs`` — one row per navigable occurrence (call/import/inheritance),
  with the target it resolved to. Unresolved rows are kept too: the query layer
  can offer same-name candidates instead of claiming the identifier is unknown.

Both tables are written from the same ``resolve()`` result as the graph and are
deleted by ``drop_graph_cache``, so navigation never points at a stale corpus.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.db import get_conn, utcnow

# Same normalization the resolver uses for symbol lookup: case and punctuation
# are not part of a Python/JS identifier's identity.
_NON_SYMBOL = re.compile(r"[^a-z0-9_]")


def normalize(name: str) -> str:
    return _NON_SYMBOL.sub("", name.lower())


def _marker_key(project_id: int) -> str:
    return f"symbol_index:{project_id}"


def replace_index(conn: Any, project_id: int, defs: list[dict[str, Any]], refs: list[dict[str, Any]]) -> None:
    """Replace a project's symbol index inside the caller's transaction."""
    conn.execute("DELETE FROM symbol_defs WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM symbol_refs WHERE project_id = ?", (project_id,))
    # The marker lets an empty project (no indexable source) be considered built;
    # otherwise every F12 would force another full rebuild.
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, '1', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_marker_key(project_id), utcnow()),
    )
    if defs:
        conn.executemany(
            """INSERT INTO symbol_defs
                   (project_id, node_id, name, normalized, kind, path,
                    start_line, start_col, end_line, end_col,
                    parent_node_id, signature, doc, language)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    project_id,
                    item["node_id"],
                    item["name"],
                    item.get("normalized") or normalize(item["name"]),
                    item["kind"],
                    item["path"],
                    int(item.get("line") or 1),
                    int(item.get("column") or 1),
                    int(item.get("end_line") or item.get("line") or 1),
                    int(item.get("end_column") or 1),
                    item.get("parent_node_id"),
                    item.get("signature") or "",
                    item.get("doc") or "",
                    item.get("language") or "",
                )
                for item in defs
            ],
        )
    if refs:
        conn.executemany(
            """INSERT INTO symbol_refs
                   (project_id, path, start_line, start_col, end_line, end_col,
                    name, normalized, kind, from_node_id, target_node_id, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    project_id,
                    item["path"],
                    int(item.get("line") or 1),
                    item.get("column"),
                    item.get("end_line"),
                    item.get("end_column"),
                    item["name"],
                    normalize(item["name"]),
                    item["kind"],
                    item.get("from_node_id"),
                    item.get("target_node_id"),
                    item.get("confidence") or "EXTRACTED",
                )
                for item in refs
            ],
        )


def has_index(project_id: int) -> bool:
    with get_conn() as conn:
        marker = conn.execute(
            "SELECT 1 FROM settings WHERE key = ?", (_marker_key(project_id),)
        ).fetchone()
        if marker is not None:
            return True
        row = conn.execute(
            "SELECT 1 FROM symbol_defs WHERE project_id = ? LIMIT 1", (project_id,)
        ).fetchone()
    return row is not None


def ensure_index(project_id: int, root: Path) -> None:
    """Build the index on first navigation, reusing the graph build path.

    A project whose graph was persisted by an older release has no index. The
    schema bump normally retires that row; the fallback force-rebuild covers a
    legacy cache that ``ensure_fresh`` still considered valid.
    """
    if has_index(project_id):
        return
    from app.graph import store

    store.ensure_fresh(project_id, root)
    if not has_index(project_id):
        store.force_rebuild(project_id, root)


def definitions_at(project_id: int, path: str, line: int) -> list[dict[str, Any]]:
    """Definitions that start on ``line`` in ``path``.

    F12 anywhere on a ``def``/``class`` line should land on that definition, so
    the match is line-based. When two definitions share a line (a one-line class
    with a method), the smallest span is the most specific one.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM symbol_defs
               WHERE project_id = ? AND path = ? AND start_line = ?
               ORDER BY (end_line - start_line), (end_col - start_col), start_col""",
            (project_id, path, line),
        ).fetchall()
    return [dict(row) for row in rows]


def definition_by_node(project_id: int, node_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM symbol_defs WHERE project_id = ? AND node_id = ?",
            (project_id, node_id),
        ).fetchone()
    return dict(row) if row is not None else None


def refs_at(
    project_id: int,
    path: str,
    line: int,
    *,
    word: str | None = None,
    column: int | None = None,
) -> list[dict[str, Any]]:
    """Occurrences on ``line``, preferring the identifier/column under the cursor.

    Import statements only carry statement-level columns, so a word match is the
    reliable selector there. Calls carry the exact name span; the column check
    makes two calls on one line (``f(g())``) resolve to the right one.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM symbol_refs
               WHERE project_id = ? AND path = ? AND start_line = ?
               ORDER BY (COALESCE(end_col, start_col) - COALESCE(start_col, 0)), id""",
            (project_id, path, line),
        ).fetchall()
    items = [dict(row) for row in rows]
    if word:
        items = [item for item in items if item["name"] == word]
    if column is not None:
        contained = [
            item
            for item in items
            if item.get("start_col") is not None
            and int(item["start_col"]) <= column <= int(item.get("end_col") or item["start_col"])
        ]
        # When the cursor is on an identifier we did not index (a local variable,
        # for example), returning an unrelated call on the same line would be a
        # confident wrong jump. Only fall back when the name matched.
        items = contained if contained else []
    return items


def refs_to(project_id: int, node_id: str, limit: int = 500) -> tuple[list[dict[str, Any]], bool]:
    """Every occurrence that resolved to ``node_id``, with its enclosing symbol."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.*, d.name AS from_name, d.kind AS from_kind
               FROM symbol_refs r
               LEFT JOIN symbol_defs d
                 ON d.project_id = r.project_id AND d.node_id = r.from_node_id
               WHERE r.project_id = ? AND r.target_node_id = ?
               ORDER BY r.path, r.start_line, r.start_col
               LIMIT ?""",
            (project_id, node_id, limit + 1),
        ).fetchall()
    items = [dict(row) for row in rows]
    truncated = len(items) > limit
    return items[:limit], truncated


def definitions_by_name(
    project_id: int,
    name: str,
    *,
    kinds: tuple[str, ...] = ("function", "class"),
    limit: int = 20,
) -> list[dict[str, Any]]:
    normalized = normalize(name)
    if not normalized:
        return []
    placeholders = ",".join("?" for _ in kinds)
    with get_conn() as conn:
        # Case-sensitive names first. The normalized fallback is useful for
        # languages that title-case constructors, but it must never outrank an
        # exact identifier (``settings`` is not the class ``Settings``).
        rows = conn.execute(
            f"""SELECT * FROM symbol_defs
                WHERE project_id = ? AND name = ? AND kind IN ({placeholders})
                ORDER BY path, start_line
                LIMIT ?""",
            (project_id, name, *kinds, limit),
        ).fetchall()
        if not rows:
            rows = conn.execute(
                f"""SELECT * FROM symbol_defs
                    WHERE project_id = ? AND normalized = ? AND kind IN ({placeholders})
                    ORDER BY path, start_line
                    LIMIT ?""",
                (project_id, normalized, *kinds, limit),
            ).fetchall()
    return [dict(row) for row in rows]


def search_definitions(project_id: int, query: str, limit: int = 50) -> list[dict[str, Any]]:
    normalized = normalize(query)
    if not normalized:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM symbol_defs
               WHERE project_id = ? AND kind IN ('function', 'class')
                 AND normalized LIKE ?
               ORDER BY
                 CASE WHEN normalized = ? THEN 0 WHEN normalized LIKE ? THEN 1 ELSE 2 END,
                 length(name), name
               LIMIT ?""",
            (project_id, f"%{normalized}%", normalized, f"{normalized}%", limit),
        ).fetchall()
    return [dict(row) for row in rows]


def index_counts(project_id: int) -> dict[str, int]:
    with get_conn() as conn:
        defs = conn.execute(
            "SELECT COUNT(*) AS n FROM symbol_defs WHERE project_id = ?", (project_id,)
        ).fetchone()
        refs = conn.execute(
            "SELECT COUNT(*) AS n FROM symbol_refs WHERE project_id = ?", (project_id,)
        ).fetchone()
    return {"defs": int(defs["n"]) if defs else 0, "refs": int(refs["n"]) if refs else 0}


__all__ = [
    "definition_by_node",
    "definitions_at",
    "definitions_by_name",
    "ensure_index",
    "has_index",
    "index_counts",
    "normalize",
    "refs_at",
    "refs_to",
    "replace_index",
    "search_definitions",
]
