"""SQLite storage: single local database for business data, FTS5 index,
vector storage and LangGraph checkpoints.

All tables are created here so every module codes against one schema.
Connections are opened per use (WAL mode) and guarded by a process-wide write
lock, which is enough for a single-desktop-user service.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings

_write_lock = threading.RLock()

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    path          TEXT    NOT NULL UNIQUE,
    excluded      TEXT,                       -- JSON list of extra ignore globs
    created_at    TEXT    NOT NULL,
    last_opened_at TEXT,
    indexed_at    TEXT
);

CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT    NOT NULL,
    mode        TEXT    NOT NULL DEFAULT 'snippet',   -- snippet | project
    file_path   TEXT,
    start_line  INTEGER,
    end_line    INTEGER,
    selected_code TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_project ON conversations(project_id);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- user | assistant | tool
    content         TEXT NOT NULL,
    citations       TEXT,                     -- JSON list
    meta            TEXT,                     -- JSON object
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS indexed_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    language    TEXT,
    size        INTEGER NOT NULL DEFAULT 0,
    mtime       REAL    NOT NULL DEFAULT 0,
    hash        TEXT,
    status      TEXT    NOT NULL DEFAULT 'indexed',   -- indexed | skipped | error
    indexed_at  TEXT,
    UNIQUE(project_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id       INTEGER REFERENCES indexed_files(id) ON DELETE CASCADE,
    path          TEXT    NOT NULL,
    language      TEXT,
    kind          TEXT    NOT NULL DEFAULT 'block',   -- function | class | module | block | doc
    symbol        TEXT,
    parent_symbol TEXT,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    content       TEXT    NOT NULL,
    hash          TEXT    NOT NULL,
    tokens        INTEGER NOT NULL DEFAULT 0,
    embed_model   TEXT,
    vector        BLOB,
    created_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_project ON chunks(project_id);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(project_id, path);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content,
    path,
    symbol,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS code_anchors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    file_path       TEXT    NOT NULL,
    commit_sha      TEXT,
    symbol          TEXT,
    start_line      INTEGER NOT NULL,
    end_line        INTEGER NOT NULL,
    snippet         TEXT,
    code_hash       TEXT,
    status          TEXT NOT NULL DEFAULT 'unchanged',
    checked_at      TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anchors_project ON code_anchors(project_id);

CREATE TABLE IF NOT EXISTS note_folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER REFERENCES note_folders(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    conversation_id TEXT,
    folder_id       INTEGER REFERENCES note_folders(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    file_name       TEXT,
    content_md      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS code_annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    selected_code TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_project_file ON code_annotations(project_id, file_path);

CREATE TABLE IF NOT EXISTS command_rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pattern       TEXT    NOT NULL,
    scope         TEXT    NOT NULL DEFAULT 'once',   -- once | session | always
    needs_confirm INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    interrupt_id    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    decision        TEXT NOT NULL,
    detail          TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    progress    INTEGER NOT NULL DEFAULT 0,
    message     TEXT NOT NULL DEFAULT '',
    stats       TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Derived knowledge graphs, persisted so a click never re-walks the source tree.
-- project_id 0 is the global notes graph (see GRAPH_SCOPE_NOTES); every other
-- value is a projects.id. No foreign key on purpose: 0 is not a project.
CREATE TABLE IF NOT EXISTS graphs (
    project_id     INTEGER NOT NULL DEFAULT 0,
    scope          TEXT    NOT NULL,
    payload        TEXT    NOT NULL,
    built_hash     TEXT,
    schema_version INTEGER NOT NULL,
    node_count     INTEGER NOT NULL DEFAULT 0,
    edge_count     INTEGER NOT NULL DEFAULT 0,
    built_at       TEXT    NOT NULL,
    PRIMARY KEY (project_id, scope)
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

# Scope keys for the ``graphs`` table, shared by the store, the notes builder and
# the storage layer so the invalidation rules cannot drift apart.
GRAPH_SCOPE_CODE = "code"
"""The AST knowledge graph of one project (``graphs.project_id`` = projects.id)."""

GRAPH_SCOPE_NOTES = "notes"
"""The global knowledge-base graph, stored once under ``NOTES_GRAPH_PROJECT_ID``."""

NOTES_GRAPH_PROJECT_ID = 0
"""``graphs.project_id`` used for the cross-project notes graph."""


def drop_graph_cache(conn: sqlite3.Connection, project_id: int, scope: str) -> None:
    """Delete one persisted graph row on a caller-supplied connection.

    Lives here rather than in ``app.graph.store`` so that ``app.storage.repo`` can
    invalidate a graph inside the transaction that mutates its source data,
    without importing ``app.graph`` (which would be a cycle). SQL only — no
    behaviour to duplicate.
    """
    conn.execute("DELETE FROM graphs WHERE project_id = ? AND scope = ?", (project_id, scope))
    bump_graph_epoch(conn, project_id)


def _epoch_key(project_id: int) -> str:
    return f"graph_epoch:{project_id}"


def bump_graph_epoch(conn: sqlite3.Connection, project_id: int) -> None:
    """Record that "something invalidated this project's graph" happened.

    A rebuild reads the source tree for seconds. If an index run finishes in the
    middle, the freshly computed graph may describe files that no longer exist,
    and persisting it would label stale data as fresh — the one failure this
    design is supposed to make impossible. ``store.build_and_store`` therefore
    snapshots this counter before reading and only writes if it did not move.
    Cheap: one upsert on the settings table, inside the caller's transaction.
    """
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, '1', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT), "
        "updated_at = excluded.updated_at",
        (_epoch_key(project_id), utcnow()),
    )


def graph_epoch(project_id: int) -> int:
    """Current epoch for a project's graph; see :func:`bump_graph_epoch`."""
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (_epoch_key(project_id),)).fetchone()
    try:
        return int(row["value"]) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def drop_notes_graph_cache(conn: sqlite3.Connection) -> None:
    """Invalidate the global knowledge-base graph (see ``app.graph.notes``)."""
    drop_graph_cache(conn, NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a connection. Writes must be wrapped in :func:`write_conn`."""
    path = Path(db_path or settings.db_path)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        conn.close()


@contextmanager
def write_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path or settings.db_path)
    with _write_lock:
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db(db_path: Path | None = None) -> None:
    path = Path(db_path or settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with write_conn(path) as conn:
        conn.executescript(SCHEMA_SQL)
        # Lightweight migrations for forward compatibility.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(chunks)")}
        for name, ddl in (
            ("embed_model", "ALTER TABLE chunks ADD COLUMN embed_model TEXT"),
            ("vector", "ALTER TABLE chunks ADD COLUMN vector BLOB"),
        ):
            if name not in cols:
                conn.execute(ddl)
        conv_cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)")}
        if "selected_code" not in conv_cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN selected_code TEXT")
        # Notes started life bound to one project; the knowledge base now spans
        # every project and is organised in a user-made folder tree.
        note_cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}
        if "folder_id" not in note_cols:
            conn.execute(
                "ALTER TABLE notes ADD COLUMN folder_id INTEGER REFERENCES note_folders(id) ON DELETE SET NULL"
            )
        if "file_name" not in note_cols:
            conn.execute("ALTER TABLE notes ADD COLUMN file_name TEXT")
        conn.execute("UPDATE notes SET file_name = title WHERE file_name IS NULL OR file_name = ''")
        # The index used to live next to the CREATE TABLE, which fails on an
        # existing database: the column only appears in the ALTER above. Creating
        # it here covers both fresh databases and migrated ones.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder_id)")
        # Annotations gained a title separate from their body. Existing rows keep
        # their content: the first line becomes the visible collapsed label.
        annotation_cols = {r["name"] for r in conn.execute("PRAGMA table_info(code_annotations)")}
        if "title" not in annotation_cols:
            conn.execute("ALTER TABLE code_annotations ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE code_annotations "
            "SET title = trim(substr(body, 1, instr(body || char(10), char(10)) - 1)) "
            "WHERE trim(COALESCE(title, '')) = ''"
        )
    _migrate_fts(path)


def _migrate_fts(path: Path) -> None:
    """Rebuild the FTS index when the tokenization scheme changes.

    ``chunks_fts`` stores a bag of search tokens (see ``app.retrieval.tokenize``)
    rather than raw text, so a stale index silently stops matching new-style
    queries. Rebuilding is cheap enough to do lazily on startup.
    """
    from app.retrieval import tokenize  # local import: keeps core/db import-light

    current = str(tokenize.INDEX_VERSION)
    with get_conn(path) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'fts_version'").fetchone()
    if row is not None and row["value"] == current:
        return

    log = logging.getLogger(__name__)
    with write_conn(path) as conn:
        rows = conn.execute("SELECT id, content, path, symbol FROM chunks").fetchall()
        conn.execute("DELETE FROM chunks_fts")
        for row in rows:
            conn.execute(
                "INSERT INTO chunks_fts (rowid, content, path, symbol) VALUES (?,?,?,?)",
                (
                    row["id"],
                    tokenize.index_text(row["content"]),
                    tokenize.index_text(row["path"]),
                    tokenize.index_text(row["symbol"] or ""),
                ),
            )
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('fts_version', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (current, utcnow()),
        )
    log.info("rebuilt full-text index for %d chunks (version %s)", len(rows), current)
