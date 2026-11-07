"""Indexing + hybrid retrieval against a real temp project (no network)."""

from __future__ import annotations

import threading
from pathlib import Path

from app.indexing import indexer
from app.retrieval import hybrid


def test_index_project_stats(project: dict):
    stats = indexer.index_project(project["id"])
    assert stats["files"] >= 2
    assert stats["chunks"] >= 3
    assert stats["vectors"] == 0  # embeddings are not configured in tests


def test_index_skips_ignored_and_binary(project: dict):
    from app.core.db import get_conn

    indexer.index_project(project["id"])
    with get_conn() as conn:
        paths = {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
    assert "pkg/sample.py" in paths
    assert "README.md" in paths
    assert not any(p.startswith("node_modules/") for p in paths)


def test_index_is_incremental(project: dict):
    first = indexer.index_project(project["id"])
    second = indexer.index_project(project["id"])
    assert second["skipped"] >= first["files"] - 1
    assert second["chunks"] == 0


def test_force_reindex_rebuilds(project: dict):
    indexer.index_project(project["id"])
    forced = indexer.index_project(project["id"], force=True)
    assert forced["chunks"] > 0


def test_keyword_search_finds_symbol(project: dict):
    indexer.index_project(project["id"])
    hits = hybrid.search(project["id"], "bar", top_k=5)
    assert hits
    assert any("sample.py" in h["path"] for h in hits)
    assert all({"path", "start_line", "end_line", "content", "score"} <= set(h) for h in hits)


def test_vector_search_ignores_vectors_from_a_different_embedding_model(project: dict, monkeypatch):
    from app.core.db import get_conn, write_conn
    from app.models import embeddings

    indexer.index_project(project["id"])
    with get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM chunks WHERE project_id = ? ORDER BY id", (project["id"],)).fetchall()
    active_id, stale_id = int(rows[0]["id"]), int(rows[1]["id"])
    with write_conn() as conn:
        conn.execute(
            "UPDATE chunks SET embed_model = ?, vector = ? WHERE id = ?",
            ("active-model", embeddings.pack([1.0, 0.0]), active_id),
        )
        conn.execute(
            "UPDATE chunks SET embed_model = ?, vector = ? WHERE id = ?",
            ("old-model", embeddings.pack([0.0, 1.0]), stale_id),
        )
    monkeypatch.setattr(embeddings, "embedding_model_name", lambda: "active-model")
    monkeypatch.setattr(embeddings, "embed_query", lambda _: [1.0, 0.0])

    hits = hybrid.vector_search(project["id"], "query", limit=10)

    assert [hit["chunk_id"] for hit in hits] == [active_id]


def test_index_jobs_are_deduplicated_per_project(project: dict, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def slow_index(project_id: int, job_id=None, force=False):
        entered.set()
        release.wait(timeout=5)
        return {}

    monkeypatch.setattr(indexer, "index_project", slow_index)
    first = indexer.start_index_job(project["id"])
    assert entered.wait(timeout=1)
    second = indexer.start_index_job(project["id"], queue_after_current=True)
    assert second == first
    release.set()


def test_project_watcher_schedules_a_background_incremental_job(project: dict, monkeypatch):
    watching = threading.Event()
    changed = threading.Event()
    queued: list[int] = []

    def fake_watch(*args, **kwargs):
        watching.set()
        changed.wait(timeout=2)
        yield set()

    monkeypatch.setattr(indexer, "watch", fake_watch)
    monkeypatch.setattr(indexer, "start_index_job", lambda project_id, **_: queued.append(project_id) or 1)
    indexer.watch_project(project["id"])
    assert watching.wait(timeout=1)
    changed.set()
    for _ in range(50):
        if queued:
            break
        threading.Event().wait(0.02)
    indexer.stop_project_watcher(project["id"])
    assert queued == [project["id"]]


def test_search_without_index_returns_empty(project: dict):
    assert hybrid.search(project["id"], "anything") == []


def test_search_handles_fts_syntax_characters(project: dict):
    indexer.index_project(project["id"])
    for query in ['"quoted"', "a AND b", "x*", "()", "C++/C#"]:
        assert isinstance(hybrid.search(project["id"], query), list)


def test_context_for_prompt_is_budgeted(project: dict):
    indexer.index_project(project["id"])
    hits = hybrid.search(project["id"], "bar", top_k=5)
    prompt = hybrid.context_for_prompt(hits, budget_chars=300)
    assert len(prompt) <= 400
    assert "pkg/sample.py" in prompt


def test_clear_index_empties_tables(project: dict):
    from app.core.db import get_conn

    indexer.index_project(project["id"])
    indexer.clear_index(project["id"])
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0] == 0


def test_missing_file_is_pruned(project: dict):
    from app.core.db import get_conn

    root = Path(project["path"])
    (root / "extra.py").write_text("value = 1\n", encoding="utf-8")
    indexer.index_project(project["id"])
    (root / "extra.py").unlink()
    indexer.index_project(project["id"])
    with get_conn() as conn:
        paths = {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
    assert "extra.py" not in paths


def test_force_reindex_prunes_files_that_became_ignored(project: dict):
    """A forced rebuild must still drop rows it no longer indexes.

    Regression: ``existing`` used to be emptied when ``force=True``, so stale
    chunks survived a rebuild and kept polluting search results.
    """
    from app.core.db import get_conn

    root = Path(project["path"])
    (root / "plugins").mkdir()
    (root / "plugins" / "bundle.js").write_text("console.log('vendor');\n", encoding="utf-8")
    indexer.index_project(project["id"])
    with get_conn() as conn:
        assert "plugins/bundle.js" in {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
    # Simulate the ignore list growing between releases, then rebuild.
    from app.services import paths as path_helpers

    path_helpers.DEFAULT_IGNORES.add("plugins")
    try:
        indexer.index_project(project["id"], force=True)
        with get_conn() as conn:
            paths = {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
            chunk_paths = {r["path"] for r in conn.execute("SELECT path FROM chunks")}
    finally:
        path_helpers.DEFAULT_IGNORES.discard("plugins")
    assert "plugins/bundle.js" not in paths
    assert "plugins/bundle.js" not in chunk_paths
    assert "pkg/sample.py" in paths


def test_lockfiles_and_vendor_dirs_are_skipped(project: dict):
    from app.core.db import get_conn

    root = Path(project["path"])
    (root / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "bundle.js.map").write_text("{}", encoding="utf-8")
    (root / "_internal").mkdir()
    (root / "_internal" / "vendor.py").write_text("vendored = True\n", encoding="utf-8")
    indexer.index_project(project["id"])
    with get_conn() as conn:
        paths = {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
    assert {"package-lock.json", "uv.lock", "bundle.js.map"} & paths == set()
    assert not any(p.startswith("_internal/") for p in paths)


def test_chinese_query_matches_chinese_content(project: dict):
    """CJK bigrams make a Chinese question findable (unicode61 alone cannot)."""
    root = Path(project["path"])
    (root / "NOTES.md").write_text(
        "# 说明\n\n本项目的入口在 pkg/sample.py，负责加载示例模块。\n",
        encoding="utf-8",
    )
    indexer.index_project(project["id"])
    hits = hybrid.search(project["id"], "项目的入口在哪里？", top_k=5)
    assert hits, "Chinese query returned no results"
    assert "NOTES.md" in {h["path"] for h in hits}


def test_keyword_ranking_prefers_query_coverage(project: dict):
    """A chunk covering more of the query must outrank a token-spammy one."""
    root = Path(project["path"])
    (root / "spam.md").write_text("# spam\n\n入口 入口 入口 入口 入口 入口\n", encoding="utf-8")
    (root / "full.md").write_text("# full\n\n项目的入口在 pkg/sample.py 里。\n", encoding="utf-8")
    indexer.index_project(project["id"])
    hits = hybrid.search(project["id"], "项目的入口", top_k=3)
    assert hits[0]["path"] == "full.md"
