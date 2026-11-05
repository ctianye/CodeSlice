"""LangGraph checkpoint storage.

We keep graph checkpoints in their own SQLite file so the business database and
the graph state never contend for the same write lock.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.core.config import settings

_saver = None
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _checkpoint_path() -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir / "checkpoints.db"


def get_saver():
    """Return a process-wide SqliteSaver."""
    global _saver, _conn
    if _saver is not None:
        return _saver
    with _lock:
        if _saver is not None:
            return _saver
        from langgraph.checkpoint.sqlite import SqliteSaver

        path = _checkpoint_path()
        _conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        _conn.execute("PRAGMA journal_mode=WAL")
        _saver = SqliteSaver(_conn)
        _saver.setup()
        return _saver


def delete_thread(conversation_id: str) -> None:
    """Drop every checkpoint of one conversation."""
    try:
        conn = sqlite3.connect(_checkpoint_path(), timeout=30)
        try:
            for table in ("checkpoints", "writes"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (conversation_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # pragma: no cover - cleanup is best effort
        pass
