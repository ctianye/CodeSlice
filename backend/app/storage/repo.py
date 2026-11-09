"""Repository helpers: thin SQL access used by routes, indexing and the agent.

Every function takes/returns plain dicts or sqlite3.Row so callers do not depend
on ORM layers. Timestamps are ISO8601 UTC strings.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from app.core.db import GRAPH_SCOPE_CODE, drop_graph_cache, drop_notes_graph_cache, get_conn, utcnow, write_conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #
def create_project(name: str, path: str) -> dict[str, Any]:
    now = utcnow()
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, path, created_at, last_opened_at) VALUES (?,?,?,?)",
            (name, path, now, now),
        )
        pid = int(cur.lastrowid or 0)
    return get_project(pid)  # type: ignore[return-value]


def list_projects() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY COALESCE(last_opened_at, created_at) DESC")
        return [dict(r) for r in rows]


def get_project(project_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone())


def find_project_by_path(path: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM projects WHERE path = ?", (path,)).fetchone())


def touch_project(project_id: int) -> None:
    with write_conn() as conn:
        conn.execute("UPDATE projects SET last_opened_at = ? WHERE id = ?", (utcnow(), project_id))


def mark_project_indexed(project_id: int) -> None:
    with write_conn() as conn:
        conn.execute("UPDATE projects SET indexed_at = ? WHERE id = ?", (utcnow(), project_id))


def delete_project(project_id: int) -> None:
    old = list_convolution_ids(project_id)
    with write_conn() as conn:
        conn.execute(
            "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE project_id = ?)",
            (project_id,),
        )
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        # Deleting the project cascades to its notes, so the global notes graph
        # (a projection of the notes table) is stale from this moment on, and this
        # project's own graph row is dead weight. Both go in the same transaction.
        drop_notes_graph_cache(conn)
        drop_graph_cache(conn, project_id, GRAPH_SCOPE_CODE)
    for conv_id in old:
        cleanup_thread(conv_id)


def list_convolution_ids(project_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM conversations WHERE project_id = ?", (project_id,))
        return [r["id"] for r in rows]


def cleanup_thread(conversation_id: str) -> None:
    """Remove LangGraph checkpoints for a deleted conversation (best effort)."""
    try:
        from app.agent.checkpoints import delete_thread  # local import: optional module

        delete_thread(conversation_id)
    except Exception:  # pragma: no cover - checkpoint cleanup must never break deletes
        pass


# --------------------------------------------------------------------------- #
# conversations & messages
# --------------------------------------------------------------------------- #
def create_conversation(
    project_id: int,
    title: str,
    mode: str = "snippet",
    file_path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    selected_code: str | None = None,
) -> dict[str, Any]:
    conv_id = uuid.uuid4().hex
    now = utcnow()
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO conversations
               (id, project_id, title, mode, file_path, start_line, end_line, selected_code, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                conv_id,
                project_id,
                title,
                mode,
                file_path,
                start_line,
                end_line,
                selected_code,
                now,
                now,
            ),
        )
    return get_conversation(conv_id)  # type: ignore[return-value]


def conversation_public(conversation: dict[str, Any]) -> dict[str, Any]:
    """Drop the (possibly large) selection snapshot from list responses."""
    return {k: v for k, v in conversation.items() if k != "selected_code"}


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone())


# One row per conversation, plus the two fields the sidebar needs: which project
# it belongs to (so "other conversations" rows can be labelled) and whether a
# note already exists (so code cards can switch to the "笔记" state). Both are
# computed in the same statement on purpose — the sidebar must not issue one
# query per row.
_CONVERSATION_SELECT = """
    SELECT c.*,
           p.name AS project_name,
           EXISTS(SELECT 1 FROM notes n WHERE n.conversation_id = c.id) AS has_note
      FROM conversations c
      LEFT JOIN projects p ON p.id = c.project_id
"""


def _conversation_rows(rows) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        item["has_note"] = bool(item.get("has_note"))
        out.append(item)
    return out


def list_conversations(project_id: int | None = None, include_others: bool = False) -> list[dict[str, Any]]:
    """List conversations, newest first.

    ``include_others`` keeps the current project's conversations on top and
    appends every other project's conversations below, each group ordered by
    ``updated_at`` descending. That single ordering lets the sidebar split the
    list into two sections without a second request.

    Conversations without messages are listed too: the sidebar needs to see the
    one the user is about to type into. Abandoned ones are cleaned up by
    :func:`purge_empty_conversations` and by the renderer when it switches away.
    """
    with get_conn() as conn:
        if project_id is None:
            rows = conn.execute(_CONVERSATION_SELECT + " ORDER BY c.updated_at DESC").fetchall()
        elif include_others:
            rows = conn.execute(
                _CONVERSATION_SELECT
                + """ ORDER BY CASE WHEN c.project_id = ? THEN 0 ELSE 1 END,
                             c.updated_at DESC""",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                _CONVERSATION_SELECT + " WHERE c.project_id = ? ORDER BY c.updated_at DESC",
                (project_id,),
            ).fetchall()
        return _conversation_rows(rows)


def purge_empty_conversations(older_than_minutes: int = 60) -> int:
    """Delete abandoned conversations that never received a message.

    A freshly created one is spared for an hour: the user may still be typing
    into it, and deleting it mid-edit would turn the next send into a 404.
    """
    with write_conn() as conn:
        cur = conn.execute(
            """DELETE FROM conversations
                WHERE NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = conversations.id)
                  AND datetime(updated_at) < datetime('now', ?)""",
            (f"-{int(older_than_minutes)} minutes",),
        )
        return int(cur.rowcount or 0)


def list_conversations_by_file(project_id: int, file_path: str) -> list[dict[str, Any]]:
    """Conversations anchored to one file, ordered by their starting line.

    Used by the code cards in the editor: for a given file we need every snippet
    conversation that covers a line range, oldest line first.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.*, p.name AS project_name,
                      EXISTS(SELECT 1 FROM notes n WHERE n.conversation_id = c.id) AS has_note,
                      (SELECT n.id FROM notes n WHERE n.conversation_id = c.id
                        ORDER BY n.id LIMIT 1) AS note_id,
                      (SELECT n.title FROM notes n WHERE n.conversation_id = c.id
                        ORDER BY n.id LIMIT 1) AS note_title
                 FROM conversations c
                 LEFT JOIN projects p ON p.id = c.project_id
                WHERE c.project_id = ? AND c.file_path = ?
                ORDER BY COALESCE(c.start_line, 0) ASC, c.updated_at DESC""",
            (project_id, file_path),
        ).fetchall()
        return _conversation_rows(rows)


def rename_conversation(conversation_id: str, title: str) -> None:
    with write_conn() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, utcnow(), conversation_id),
        )


def set_conversation_title(conversation_id: str, title: str) -> None:
    """Rewrite a title without touching ``updated_at``.

    Used by the one-off title backfill: those conversations are old, and letting
    the rewrite bump ``updated_at`` would shuffle the whole sidebar to the top.
    """
    with write_conn() as conn:
        conn.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id))


def touch_conversation(conversation_id: str) -> None:
    with write_conn() as conn:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (utcnow(), conversation_id))


def delete_conversation(conversation_id: str) -> None:
    with write_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    cleanup_thread(conversation_id)


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg_id = uuid.uuid4().hex
    now = utcnow()
    with write_conn() as conn:
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, citations, meta, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                msg_id,
                conversation_id,
                role,
                content,
                json.dumps(citations, ensure_ascii=False) if citations else None,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                now,
            ),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
    return {
        "id": msg_id,
        "role": role,
        "content": content,
        "created_at": now,
        "citations": citations,
        "meta": meta,
    }


def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        )
        out = []
        for r in rows:
            item = dict(r)
            item["citations"] = _json(item.get("citations"))
            item["meta"] = _json(item.get("meta"))
            out.append(item)
        return out


def get_message(conversation_id: str, message_id: str) -> dict[str, Any] | None:
    """One message, scoped to its conversation so a stray id cannot leak across."""
    with get_conn() as conn:
        row = _row(
            conn.execute(
                "SELECT * FROM messages WHERE id = ? AND conversation_id = ?",
                (message_id, conversation_id),
            ).fetchone()
        )
    if row is not None:
        row["citations"] = _json(row.get("citations"))
        row["meta"] = _json(row.get("meta"))
    return row


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
def create_job(kind: str, project_id: int | None = None) -> int:
    now = utcnow()
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (project_id, kind, status, progress, message, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (project_id, kind, "pending", 0, "queued", now, now),
        )
        return int(cur.lastrowid or 0)


def update_job(
    job_id: int,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    sets: list[str] = []
    params: list[Any] = []
    for col, val in (
        ("status", status),
        ("progress", progress),
        ("message", message),
        ("error", error),
    ):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if stats is not None:
        sets.append("stats = ?")
        params.append(json.dumps(stats, ensure_ascii=False))
    sets.append("updated_at = ?")
    params.append(utcnow())
    params.append(job_id)
    with write_conn() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params)


def get_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = _row(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())
    if row:
        row["stats"] = _json(row.get("stats"))
    return row


# --------------------------------------------------------------------------- #
# settings (kv)
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: Any = None) -> Any:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return _json(row["value"], default)


def set_setting(key: str, value: Any) -> None:
    with write_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), utcnow()),
        )


# --------------------------------------------------------------------------- #
# notes
# --------------------------------------------------------------------------- #
def list_notes(project_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notes WHERE project_id = ? ORDER BY updated_at DESC", (project_id,))
        return [dict(r) for r in rows]


# The knowledge base is deliberately cross-project: a reading note about a
# library is just as useful while working in another repo. ``project_name`` lets
# the panel show where each note came from without a second request.
_NOTE_SELECT = """
    SELECT n.*, p.name AS project_name
      FROM notes n
      LEFT JOIN projects p ON p.id = n.project_id
"""


def list_all_notes(folder_id: int | None = None, unfiled: bool = False) -> list[dict[str, Any]]:
    """Every note in the knowledge base, newest first.

    ``folder_id`` narrows to one folder; ``unfiled`` returns only notes that have
    no folder yet (the "未分类" bucket).
    """
    with get_conn() as conn:
        if unfiled:
            rows = conn.execute(_NOTE_SELECT + " WHERE n.folder_id IS NULL ORDER BY n.updated_at DESC").fetchall()
        elif folder_id is not None:
            rows = conn.execute(
                _NOTE_SELECT + " WHERE n.folder_id = ? ORDER BY n.updated_at DESC",
                (folder_id,),
            ).fetchall()
        else:
            rows = conn.execute(_NOTE_SELECT + " ORDER BY n.updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_note(note_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute(_NOTE_SELECT + " WHERE n.id = ?", (note_id,)).fetchone())


def find_note_for_conversation(conversation_id: str) -> dict[str, Any] | None:
    """Return the note generated from a conversation, if any (newest first)."""
    with get_conn() as conn:
        return _row(
            conn.execute(
                "SELECT * FROM notes WHERE conversation_id = ? ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        )


def create_note(
    project_id: int,
    title: str,
    content_md: str,
    conversation_id: str | None = None,
    folder_id: int | None = None,
    file_name: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (project_id, conversation_id, folder_id, title, file_name, content_md, created_at, "
            "updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (project_id, conversation_id, folder_id, title, file_name or title, content_md, now, now),
        )
        note_id = int(cur.lastrowid or 0)
        # The notes graph is a projection of this table; drop it in the same
        # transaction so a concurrent reader cannot serve the pre-insert shape.
        drop_notes_graph_cache(conn)
    return get_note(note_id)  # type: ignore[return-value]


def update_note(
    note_id: int,
    title: str | None,
    content_md: str | None,
    folder_id: int | None = None,
    move_folder: bool = False,
    file_name: str | None = None,
) -> dict[str, Any] | None:
    """Update a note; ``move_folder`` is how a caller clears ``folder_id``.

    ``None`` already means "leave unchanged" for title/content, so moving a note
    back to 未分类 needs an explicit flag rather than a sentinel value.
    """
    note = get_note(note_id)
    if note is None:
        return None
    with write_conn() as conn:
        conn.execute(
            "UPDATE notes SET title = ?, file_name = ?, content_md = ?, folder_id = ?, updated_at = ? WHERE id = ?",
            (
                title if title is not None else note["title"],
                file_name if file_name is not None else (note.get("file_name") or note["title"]),
                content_md if content_md is not None else note["content_md"],
                folder_id if move_folder else note.get("folder_id"),
                utcnow(),
                note_id,
            ),
        )
        drop_notes_graph_cache(conn)
    return get_note(note_id)


# --------------------------------------------------------------------------- #
# note folders (user-made knowledge-base tree)
# --------------------------------------------------------------------------- #
def list_note_folders() -> list[dict[str, Any]]:
    """All folders plus how many notes each holds directly."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT f.*, (SELECT COUNT(*) FROM notes n WHERE n.folder_id = f.id) AS note_count
                 FROM note_folders f
                ORDER BY f.name COLLATE NOCASE ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def create_note_folder(name: str, parent_id: int | None = None) -> dict[str, Any]:
    now = utcnow()
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO note_folders (parent_id, name, created_at) VALUES (?,?,?)",
            (parent_id, name, now),
        )
        folder_id = int(cur.lastrowid or 0)
        drop_notes_graph_cache(conn)
        row = conn.execute("SELECT * FROM note_folders WHERE id = ?", (folder_id,)).fetchone()
    return dict(row)  # type: ignore[arg-type]


def rename_note_folder(
    folder_id: int,
    name: str | None,
    parent_id: int | None = None,
    move: bool = False,
) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM note_folders WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            return None
        folder = dict(row)
    with write_conn() as conn:
        conn.execute(
            "UPDATE note_folders SET name = ?, parent_id = ? WHERE id = ?",
            (
                name if name is not None else folder["name"],
                parent_id if move else folder["parent_id"],
                folder_id,
            ),
        )
        # A folder label appears on every community of the notes graph.
        drop_notes_graph_cache(conn)
        row = conn.execute("SELECT * FROM note_folders WHERE id = ?", (folder_id,)).fetchone()
    return dict(row)  # type: ignore[arg-type]


def delete_note_folder(folder_id: int) -> None:
    """Delete a folder; its notes fall back to 未分类, child folders go too."""
    with write_conn() as conn:
        conn.execute("UPDATE notes SET folder_id = NULL WHERE folder_id = ?", (folder_id,))
        conn.execute("DELETE FROM note_folders WHERE id = ?", (folder_id,))
        drop_notes_graph_cache(conn)


def delete_note(note_id: int) -> None:
    with write_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        drop_notes_graph_cache(conn)


def list_annotations(project_id: int, file_path: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM code_annotations WHERE project_id = ? AND file_path = ? ORDER BY start_line, id",
            (project_id, file_path),
        )
        return [dict(row) for row in rows]


def create_annotation(
    project_id: int,
    file_path: str,
    start_line: int,
    end_line: int,
    selected_code: str,
    body: str,
) -> dict[str, Any]:
    now = utcnow()
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO code_annotations "
            "(project_id,file_path,start_line,end_line,selected_code,body,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, file_path, start_line, end_line, selected_code, body, now, now),
        )
        row = conn.execute("SELECT * FROM code_annotations WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_annotation(annotation_id: int) -> None:
    with write_conn() as conn:
        conn.execute("DELETE FROM code_annotations WHERE id = ?", (annotation_id,))


# --------------------------------------------------------------------------- #
# command rules
# --------------------------------------------------------------------------- #
def list_rules(project_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM command_rules WHERE project_id = ? ORDER BY id DESC", (project_id,))
        out = []
        for r in rows:
            item = dict(r)
            item["needs_confirm"] = bool(item["needs_confirm"])
            out.append(item)
        return out


def create_rule(project_id: int, pattern: str, scope: str = "once", needs_confirm: bool = True) -> dict[str, Any]:
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO command_rules (project_id, pattern, scope, needs_confirm, created_at) VALUES (?,?,?,?,?)",
            (project_id, pattern, scope, 1 if needs_confirm else 0, utcnow()),
        )
        rule_id = int(cur.lastrowid or 0)
    return get_rule(rule_id)  # type: ignore[return-value]


def get_rule(rule_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = _row(conn.execute("SELECT * FROM command_rules WHERE id = ?", (rule_id,)).fetchone())
    if row:
        row["needs_confirm"] = bool(row["needs_confirm"])
    return row


def update_rule(rule_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"pattern", "scope", "needs_confirm"}
    sets, params = [], []
    for key, val in fields.items():
        if key in allowed and val is not None:
            sets.append(f"{key} = ?")
            params.append(int(val) if key == "needs_confirm" else val)
    if sets:
        params.append(rule_id)
        with write_conn() as conn:
            conn.execute(f"UPDATE command_rules SET {', '.join(sets)} WHERE id = ?", params)
    return get_rule(rule_id)


def delete_rule(rule_id: int) -> None:
    with write_conn() as conn:
        conn.execute("DELETE FROM command_rules WHERE id = ?", (rule_id,))


def add_approval(conversation_id: str, interrupt_id: str, kind: str, decision: str, detail: str | None = None) -> None:
    with write_conn() as conn:
        conn.execute(
            "INSERT INTO approvals (conversation_id, interrupt_id, kind, decision, detail, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (conversation_id, interrupt_id, kind, decision, detail, utcnow()),
        )


# --------------------------------------------------------------------------- #
# code anchors
# --------------------------------------------------------------------------- #
def list_anchors(project_id: int, conversation_id: str | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if conversation_id:
            rows = conn.execute(
                "SELECT * FROM code_anchors WHERE project_id = ? AND conversation_id = ? ORDER BY id",
                (project_id, conversation_id),
            )
        else:
            rows = conn.execute("SELECT * FROM code_anchors WHERE project_id = ? ORDER BY id", (project_id,))
        return [dict(r) for r in rows]


def create_anchor(
    project_id: int,
    conversation_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
    symbol: str | None = None,
    snippet: str | None = None,
    code_hash: str | None = None,
    commit_sha: str | None = None,
) -> dict[str, Any]:
    """Insert an anchor, or refresh the existing one for the same code range.

    A recurring citation (same conversation, same file, same range) must not pile
    up duplicate rows: the anchor panel is a reading list, not an event log.
    """
    now = utcnow()
    with write_conn() as conn:
        existing = conn.execute(
            """SELECT id FROM code_anchors
               WHERE project_id = ? AND conversation_id = ? AND file_path = ?
                 AND start_line = ? AND end_line = ?""",
            (project_id, conversation_id, file_path, start_line, end_line),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE code_anchors SET status = 'unchanged', checked_at = ?, snippet = ? WHERE id = ?",
                (now, snippet, existing["id"]),
            )
            anchor_id = int(existing["id"])
        else:
            cur = conn.execute(
                """INSERT INTO code_anchors
                   (project_id, conversation_id, file_path, commit_sha, symbol, start_line, end_line,
                    snippet, code_hash, status, checked_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    project_id,
                    conversation_id,
                    file_path,
                    commit_sha,
                    symbol,
                    start_line,
                    end_line,
                    snippet,
                    code_hash,
                    "unchanged",
                    now,
                    now,
                ),
            )
            anchor_id = int(cur.lastrowid or 0)
    return get_anchor(anchor_id)  # type: ignore[return-value]


def get_anchor(anchor_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return _row(conn.execute("SELECT * FROM code_anchors WHERE id = ?", (anchor_id,)).fetchone())


def update_anchor(anchor_id: int, **fields: Any) -> dict[str, Any] | None:
    allowed = {"status", "start_line", "end_line", "snippet", "code_hash", "checked_at", "symbol"}
    sets, params = [], []
    for key, val in fields.items():
        if key in allowed and val is not None:
            sets.append(f"{key} = ?")
            params.append(val)
    if sets:
        params.append(anchor_id)
        with write_conn() as conn:
            conn.execute(f"UPDATE code_anchors SET {', '.join(sets)} WHERE id = ?", params)
    return get_anchor(anchor_id)
