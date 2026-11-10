"""Project indexing pipeline.

walk → filter → language detect → chunk → persist → (optional) embed

Runs in a background thread, updating the ``jobs`` row so the UI can poll
``GET /api/jobs/{id}``. Re-indexing is incremental: files whose size+mtime are
unchanged keep their chunks, changed files are re-chunked and re-embedded.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Any

from watchfiles import DefaultFilter, watch

from app.core.db import GRAPH_SCOPE_CODE, drop_graph_cache, get_conn, utcnow, write_conn
from app.indexing.chunker import chunk_file
from app.indexing.languages import detect_language
from app.models import embeddings as emb
from app.retrieval import tokenize
from app.services import notes_index, paths
from app.storage import repo

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 1_500_000
CHUNK_FORMAT_VERSION = 2
_JOB_LOCK = threading.Lock()
_ACTIVE_JOBS: dict[int, int] = {}
_RERUN_REQUESTED: set[int] = set()
_WATCH_LOCK = threading.Lock()
_PROJECT_WATCHERS: dict[int, tuple[threading.Event, threading.Thread]] = {}


class _ProjectWatchFilter(DefaultFilter):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def __call__(self, change: Any, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        target = Path(path)
        try:
            relative = target.relative_to(self.root)
        except ValueError:
            return False
        if any(part in paths.DEFAULT_IGNORES for part in relative.parts):
            return False
        return not paths.skip_file(target.name) and target.suffix.lower() not in paths.BINARY_SUFFIXES


def _iter_files(root: Path):
    for path in root.rglob("*"):
        rel_parts = path.relative_to(root).parts
        # Check ignored parts before touching the entry. Build bundles can contain
        # symlinks that Windows cannot stat (WinError 1920), and an unreadable
        # ignored artifact must not abort the scan of real source.
        if any(part in paths.DEFAULT_IGNORES for part in rel_parts):
            continue
        try:
            if path.is_dir():
                continue
            stat = path.stat()
        except OSError:
            continue
        if path.suffix.lower() in paths.BINARY_SUFFIXES:
            continue
        if paths.skip_file(path.name):
            continue
        if stat.st_size > MAX_FILE_BYTES or stat.st_size == 0:
            continue
        yield path, stat


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    except OSError:
        return None


def clear_index(project_id: int) -> None:
    with write_conn() as conn:
        conn.execute(
            "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE project_id = ?)",
            (project_id,),
        )
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM indexed_files WHERE project_id = ?", (project_id,))
        # The persisted graph describes the corpus that was just deleted.
        drop_graph_cache(conn, project_id, GRAPH_SCOPE_CODE)


def _existing_files(project_id: int) -> dict[str, tuple[int, float, int, str | None]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, path, size, mtime, hash FROM indexed_files WHERE project_id = ?",
            (project_id,),
        )
        return {r["path"]: (r["id"], r["mtime"], r["size"], r["hash"]) for r in rows}


def index_project(project_id: int, job_id: int | None = None, force: bool = False) -> dict[str, Any]:
    project = repo.get_project(project_id)
    if project is None:
        raise ValueError(f"project {project_id} not found")
    root = paths.resolve_root(project["path"])

    def progress(pct: int, msg: str, stats: dict | None = None, status: str = "running") -> None:
        if job_id:
            repo.update_job(job_id, status=status, progress=max(0, min(100, pct)), message=msg, stats=stats)

    progress(1, "扫描文件…")
    files = list(_iter_files(root))
    # Always read the previous index: even a forced rebuild must be able to drop
    # rows for files that vanished or became ignored since the last run.
    existing = _existing_files(project_id)
    seen_paths: set[str] = set()

    stats = {"files": 0, "chunks": 0, "vectors": 0, "skipped": 0}
    use_embeddings = emb.is_configured() and emb.embedding_model_name() is not None
    model_name = emb.embedding_model_name() if use_embeddings else None
    total = max(1, len(files))

    # chunk id -> text still needing a vector, keyed by id so a chunk that was
    # just rewritten is not embedded twice in the same run.
    pending_vectors: dict[int, str] = {}

    for idx, (path, stat) in enumerate(files, start=1):
        rel = path.relative_to(root).as_posix()
        seen_paths.add(rel)
        old = existing.get(rel)
        if (
            old
            and str(old[3] or "").startswith(f"chunk-v{CHUNK_FORMAT_VERSION}:")
            and not force
            and int(old[2]) == stat.st_size
            and abs(float(old[1]) - stat.st_mtime) < 1e-3
        ):
            stats["skipped"] += 1
            if idx % 50 == 0:
                progress(int(idx / total * 90), f"跳过未变化文件 {idx}/{total}", stats)
            continue

        text = _read_text(path)
        if text is None:
            continue
        language = detect_language(rel, text)
        digest = f"chunk-v{CHUNK_FORMAT_VERSION}:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

        with write_conn() as conn:
            conn.execute(
                """INSERT INTO indexed_files (project_id, path, language, size, mtime, hash, status, indexed_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id, path) DO UPDATE SET
                     language=excluded.language, size=excluded.size, mtime=excluded.mtime,
                     hash=excluded.hash, status=excluded.status, indexed_at=excluded.indexed_at""",
                (
                    project_id,
                    rel,
                    language,
                    stat.st_size,
                    stat.st_mtime,
                    digest,
                    "indexed",
                    utcnow(),
                ),
            )
            file_row = conn.execute(
                "SELECT id FROM indexed_files WHERE project_id = ? AND path = ?", (project_id, rel)
            ).fetchone()
            file_id = file_row["id"]
            old_chunk_ids = [
                r["id"]
                for r in conn.execute("SELECT id FROM chunks WHERE project_id = ? AND path = ?", (project_id, rel))
            ]
            for cid in old_chunk_ids:
                conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
            conn.execute("DELETE FROM chunks WHERE project_id = ? AND path = ?", (project_id, rel))

            chunks = chunk_file(rel, language, text)
            for chunk in chunks:
                cur = conn.execute(
                    """INSERT INTO chunks (project_id, file_id, path, language, kind, symbol, parent_symbol,
                                           start_line, end_line, content, hash, tokens, embed_model, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        project_id,
                        file_id,
                        chunk["path"],
                        chunk["language"],
                        chunk["kind"],
                        chunk["symbol"],
                        chunk["parent_symbol"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["content"],
                        chunk["hash"],
                        chunk["tokens"],
                        None,
                        utcnow(),
                    ),
                )
                chunk_id = int(cur.lastrowid or 0)
                conn.execute(
                    "INSERT INTO chunks_fts (rowid, content, path, symbol) VALUES (?,?,?,?)",
                    (
                        chunk_id,
                        tokenize.index_text(chunk["content"]),
                        tokenize.index_text(chunk["path"]),
                        tokenize.index_text(chunk["symbol"] or ""),
                    ),
                )
                if use_embeddings:
                    pending_vectors[chunk_id] = chunk["content"]
                stats["chunks"] += 1
        stats["files"] += 1
        if idx % 10 == 0 or idx == total:
            progress(int(idx / total * 90), f"已解析 {idx}/{total} 个文件", stats)

    # Drop files that no longer exist.
    stale = [p for p in existing if p not in seen_paths]
    if stale:
        with write_conn() as conn:
            for rel in stale:
                ids = [
                    r["id"]
                    for r in conn.execute("SELECT id FROM chunks WHERE project_id = ? AND path = ?", (project_id, rel))
                ]
                for cid in ids:
                    conn.execute("DELETE FROM chunks_fts WHERE rowid = ?", (cid,))
                conn.execute("DELETE FROM chunks WHERE project_id = ? AND path = ?", (project_id, rel))
                conn.execute("DELETE FROM indexed_files WHERE project_id = ? AND path = ?", (project_id, rel))

    # Chunks carried over from an earlier run keep their old vectors. When the
    # configured embedding model changed (e.g. the user switched to a local
    # Ollama model), those vectors are not comparable with new query vectors, so
    # re-embed them here instead of forcing the user to re-parse every file.
    if use_embeddings and model_name:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, content FROM chunks WHERE project_id = ? AND (embed_model IS NULL OR embed_model <> ?)",
                (project_id, model_name),
            ).fetchall()
        for row in rows:
            pending_vectors.setdefault(int(row["id"]), str(row["content"]))

    if pending_vectors and model_name:
        progress(92, f"生成向量 {len(pending_vectors)} 段…", stats)
        items = list(pending_vectors.items())
        texts = [content for _, content in items]
        for attempt in range(3):
            try:
                vectors = emb.embed_texts(texts)
                if vectors:
                    with write_conn() as conn:
                        for (chunk_id, _), vector in zip(items, vectors, strict=False):
                            conn.execute(
                                "UPDATE chunks SET vector = ?, embed_model = ? WHERE id = ?",
                                (emb.pack(vector), model_name, chunk_id),
                            )
                    stats["vectors"] = len(vectors)
                break
            except Exception as exc:  # keyword search remains available while vectors retry
                if attempt == 2:
                    log.warning("embedding failed after retries: %s", exc)
                    stats["embedding_error"] = f"{type(exc).__name__}: {exc}"[:300]
                else:
                    time.sleep(1 + attempt * 2)

    # Notes are chunks too (path = note://<id>), so a rebuild must put them back:
    # the file scan above only knows about files on disk, and clear_index wipes
    # every chunk of the project. Re-sync also drops rows for deleted notes.
    try:
        note_stats = notes_index.sync_project_notes(project_id)
        stats["notes"] = int(note_stats.get("notes", 0))
    except Exception as exc:  # note indexing must not fail the file index
        log.warning("note index sync failed: %s", exc)

    # The persisted code graph is only invalidated when this run actually changed
    # the corpus. A background scan that skipped every file must not throw away a
    # perfectly good graph — the whole point is that a click never rebuilds it.
    # Note that notes are chunks but never graph sources: a note edit does not
    # touch the code graph (it invalidates the notes graph instead, in repo).
    if stats["files"] > 0 or stale:
        with write_conn() as conn:
            drop_graph_cache(conn, project_id, GRAPH_SCOPE_CODE)
        log.debug(
            "invalidated code graph for project %s after index run (%d file(s) re-chunked, %d pruned)",
            project_id,
            stats["files"],
            len(stale),
        )

    repo.mark_project_indexed(project_id)
    progress(100, "索引完成", stats, status="done")
    return stats


def start_index_job(project_id: int, force: bool = False, queue_after_current: bool = False) -> int:
    with _JOB_LOCK:
        active_id = _ACTIVE_JOBS.get(project_id)
        active_job = repo.get_job(active_id) if active_id is not None else None
        if active_job and active_job.get("status") in {"pending", "running"}:
            if queue_after_current:
                _RERUN_REQUESTED.add(project_id)
            return active_id

        job_id = repo.create_job("index", project_id)
        _ACTIVE_JOBS[project_id] = job_id
        repo.update_job(job_id, status="pending", progress=0, message="排队中")

    def _run() -> None:
        try:
            index_project(project_id, job_id=job_id, force=force)
        except Exception as exc:  # pragma: no cover - job error path
            log.exception("index job failed")
            repo.update_job(job_id, status="error", error=f"{type(exc).__name__}: {exc}", message="索引失败")
        finally:
            rerun = False
            with _JOB_LOCK:
                if _ACTIVE_JOBS.get(project_id) == job_id:
                    _ACTIVE_JOBS.pop(project_id, None)
                    rerun = project_id in _RERUN_REQUESTED
                    _RERUN_REQUESTED.discard(project_id)
            if rerun:
                start_index_job(project_id)

    thread = threading.Thread(target=_run, name=f"index-{project_id}", daemon=True)
    thread.start()
    return job_id


def watch_project(project_id: int) -> None:
    """Watch an opened project and coalesce source changes into background scans."""
    project = repo.get_project(project_id)
    if project is None:
        return
    root = paths.resolve_root(project["path"])
    with _WATCH_LOCK:
        current = _PROJECT_WATCHERS.get(project_id)
        if current and current[1].is_alive():
            return
        stop = threading.Event()

        def _run() -> None:
            try:
                for _ in watch(
                    root,
                    watch_filter=_ProjectWatchFilter(root),
                    stop_event=stop,
                    debounce=1200,
                    step=300,
                ):
                    if stop.is_set():
                        break
                    start_index_job(project_id, queue_after_current=True)
            except Exception:
                if not stop.is_set():
                    log.warning("project file watcher stopped for %s", project_id, exc_info=True)
            finally:
                with _WATCH_LOCK:
                    if _PROJECT_WATCHERS.get(project_id, (None, None))[0] is stop:
                        _PROJECT_WATCHERS.pop(project_id, None)

        thread = threading.Thread(target=_run, name=f"watch-{project_id}", daemon=True)
        _PROJECT_WATCHERS[project_id] = (stop, thread)
        thread.start()


def stop_project_watcher(project_id: int) -> None:
    with _WATCH_LOCK:
        watcher = _PROJECT_WATCHERS.pop(project_id, None)
    if watcher:
        watcher[0].set()


def stop_project_watchers() -> None:
    with _WATCH_LOCK:
        watchers = list(_PROJECT_WATCHERS.values())
        _PROJECT_WATCHERS.clear()
    for stop, _ in watchers:
        stop.set()
