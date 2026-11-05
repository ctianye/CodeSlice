"""Keep saved notes inside the retrieval pipeline.

A note is not only UI state: every saved note owns exactly one ``chunks`` row
(``path = note://<id>``, ``kind = 'note'``, ``start_line = end_line = 0``) so the
same hybrid search that finds code also finds what the user already learned
about it. That is what turns "生成笔记" into knowledge the agent can cite back.

A note chunk has no ``file_id``: it does not belong to an indexed file, and the
incremental file scan never touches ``note://`` paths. ``sync_project_notes``
keeps the two sides consistent after a full rebuild, an edit or a deletion.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.core.db import get_conn, utcnow, write_conn
from app.models import embeddings as emb
from app.retrieval import tokenize
from app.storage import repo

log = logging.getLogger(__name__)

NOTE_PATH_PREFIX = "note://"


def note_path(note_id: int) -> str:
    return f"{NOTE_PATH_PREFIX}{int(note_id)}"


def note_id_from_path(path: str) -> int | None:
    """``note://12`` → 12; anything else → None."""
    if not path.startswith(NOTE_PATH_PREFIX):
        return None
    tail = path[len(NOTE_PATH_PREFIX) :]
    return int(tail) if tail.isdigit() else None


def note_text(note: dict[str, Any]) -> str:
    """The text that is stored and searched: title first, then the markdown."""
    title = str(note.get("title") or "未命名笔记").strip()
    body = str(note.get("content_md") or "").strip()
    return f"# {title}\n\n{body}".rstrip()


def _embed(text: str) -> tuple[str | None, bytes | None]:
    """Best-effort single-vector embedding; notes stay searchable without it."""
    if not emb.is_configured():
        return None, None
    model = emb.embedding_model_name()
    if not model:
        return None, None
    try:
        vector = emb.embed_query(text)
    except Exception as exc:  # a broken embedding endpoint must not block saving
        log.warning("note embedding failed: %s", exc)
        return None, None
    if not vector:
        return None, None
    return model, emb.pack(vector)


def _delete_chunk_rows(conn: Any, paths: list[str]) -> None:
    for path in paths:
        ids = [r["id"] for r in conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))]
        for chunk_id in ids:
            conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (chunk_id,))
        conn.execute("DELETE FROM chunks WHERE path = ?", (path,))


def remove_note_chunk(note_id: int) -> None:
    """Drop the retrieval row of a note that was deleted or replaced."""
    with write_conn() as conn:
        _delete_chunk_rows(conn, [note_path(note_id)])


def sync_note(note: dict[str, Any]) -> int | None:
    """Insert or refresh the chunk behind one note. Returns the chunk id."""
    note_id = int(note["id"])
    project_id = int(note["project_id"])
    path = note_path(note_id)
    text = note_text(note)
    title = str(note.get("title") or "未命名笔记")
    digest = hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()
    tokens = max(1, len(text) // 4)
    model, vector = _embed(text)

    with write_conn() as conn:
        _delete_chunk_rows(conn, [path])
        cur = conn.execute(
            """INSERT INTO chunks (project_id, file_id, path, language, kind, symbol, parent_symbol,
                                   start_line, end_line, content, hash, tokens, embed_model, vector, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                None,
                path,
                "markdown",
                "note",
                title,
                None,
                0,
                0,
                text,
                digest,
                tokens,
                model,
                vector,
                utcnow(),
            ),
        )
        chunk_id = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO chunks_fts (rowid, content, path, symbol) VALUES (?,?,?,?)",
            (
                chunk_id,
                tokenize.index_text(text),
                tokenize.index_text(path),
                tokenize.index_text(title),
            ),
        )
    return chunk_id


def indexed_note_ids(project_id: int) -> set[int]:
    """Note ids that currently own a retrieval row (used for the UI badge)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT path FROM chunks WHERE project_id = ? AND kind = 'note'",
            (project_id,),
        ).fetchall()
    ids: set[int] = set()
    for row in rows:
        note_id = note_id_from_path(str(row["path"]))
        if note_id is not None:
            ids.add(note_id)
    return ids


def sync_project_notes(project_id: int) -> dict[str, int]:
    """Re-index every note of a project and prune rows for vanished notes."""
    notes = repo.list_notes(project_id)
    alive: set[str] = set()
    for note in notes:
        try:
            sync_note(note)
        except Exception as exc:  # one broken note must not break a rebuild
            log.warning("note indexing failed for %s: %s", note.get("id"), exc)
            continue
        alive.add(note_path(int(note["id"])))

    removed = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, path FROM chunks WHERE project_id = ? AND path LIKE ?",
            (project_id, f"{NOTE_PATH_PREFIX}%"),
        ).fetchall()
    orphans = [str(r["path"]) for r in rows if str(r["path"]) not in alive]
    if orphans:
        with write_conn() as conn:
            _delete_chunk_rows(conn, orphans)
        removed = len(orphans)
    return {"notes": len(alive), "removed": removed}
