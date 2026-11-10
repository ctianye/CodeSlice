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


def test_renamed_build_output_and_binaries_never_reach_the_index(project: dict):
    """A ``release-linux-021`` tree is not ``release``; binaries are rejected anyway.

    Bundled blobs decode into thousands of U+FFFD, and one such chunk is enough
    to make an embedding batch exceed the model context (HTTP 413) — which used
    to fail every run while the sidebar kept re-embedding the same thousands of
    chunks.
    """
    from app.core.db import get_conn

    root = Path(project["path"])
    packed = root / "desktop" / "release-linux-021" / "linux-unpacked"
    (packed / "resources" / "frontend").mkdir(parents=True)
    (packed / "resources" / "frontend" / "chunk-abc.js").write_text("export const a = 1\n", encoding="utf-8")
    (packed / "v8_context_snapshot.bin").write_bytes(b"\x00\x01snapshot\x00\xff" * 200)
    (packed / "chrome-sandbox").write_bytes(b"\x7fELF\x00\x00binary")
    (root / "blob.dat").write_bytes(b"\x00\x00\x00binary payload")
    (root / "src").mkdir()
    (root / "src" / "real.py").write_text("def real():\n    return 1\n", encoding="utf-8")

    indexer.index_project(project["id"], force=True)

    with get_conn() as conn:
        paths = {r["path"] for r in conn.execute("SELECT path FROM indexed_files")}
        contents = [r["content"] for r in conn.execute("SELECT content FROM chunks")]
    assert not any(p.startswith("desktop/release-linux-021/") for p in paths)
    assert "blob.dat" not in paths
    assert "src/real.py" in paths
    assert not any("\ufffd" in content for content in contents), "binary garbage became a chunk"


def test_graph_builder_reads_the_same_corpus_as_the_indexer(project: dict):
    """When the two disagree, bundled files become graph nodes (2.6k-node canvases)."""
    from app.graph import builder

    root = Path(project["path"])
    packed = root / "desktop" / "release-clean" / "linux-unpacked"
    packed.mkdir(parents=True)
    (packed / "main.js").write_text("function main() {}\n", encoding="utf-8")
    (packed / "icudtl.dat").write_bytes(b"\x00icu data")

    rels = {rel for rel, _ in builder.collect_sources(root)}
    assert not any(rel.startswith("desktop/") for rel in rels)


def test_progress_write_failure_does_not_abort_the_index(project: dict, monkeypatch):
    """Reporting progress must never kill the run it reports on.

    A transient SQLite error ("attempt to write a readonly database" while another
    process checkpointed the file) used to surface as a failed index job even
    though the corpus was fine, and the next run succeeded.
    """
    import sqlite3

    from app.storage import repo

    job_id = repo.create_job("index", project["id"])
    real_update = repo.update_job
    calls = {"n": 0}

    def flaky_update(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("attempt to write a readonly database")
        return real_update(*args, **kwargs)

    monkeypatch.setattr(indexer.repo, "update_job", flaky_update)
    stats = indexer.index_project(project["id"], job_id=job_id, force=True)

    assert stats["files"] > 0
    job = repo.get_job(job_id)
    assert job is not None and job["status"] == "done"


def test_configured_embeddings_backfill_vectors_without_rechunking(project: dict, monkeypatch):
    """Configuring an embedding model must fill old chunks, not require edits.

    This is the upgrade path: a project was indexed while embeddings were off,
    then the user configures one. The next background scan skips every source
    file but still embeds the existing chunks and persists both the vector and
    the model name.
    """
    from app.core.db import get_conn
    from app.models import embeddings

    indexer.index_project(project["id"])
    with get_conn() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE project_id = ? AND vector IS NULL",
            (project["id"],),
        ).fetchone()["n"]
    assert pending > 0

    calls: list[list[str]] = []

    def fake_embed_texts(texts: list[str], batch_size: int = 32):
        calls.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]

    monkeypatch.setattr(embeddings, "is_configured", lambda: True)
    monkeypatch.setattr(embeddings, "embedding_model_name", lambda: "test-embed")
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)

    stats = indexer.index_project(project["id"])

    assert stats["files"] == 0, "the corpus did not change; only vectors were missing"
    assert stats["vectors"] == pending
    assert calls, "the backfill never called the embedding model"
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT vector, embed_model FROM chunks WHERE project_id = ?",
            (project["id"],),
        ).fetchall()
    assert rows
    assert all(row["vector"] is not None and row["embed_model"] == "test-embed" for row in rows)


def test_generated_artifacts_and_env_files_are_not_indexed(project: dict):
    """Debug outputs and secrets must not enter the retrieval/embedding corpus."""
    from app.core.db import get_conn

    root = Path(project["path"])
    generated = (".shots", ".tmp-preview-data", ".acl-fix", "acl-recovery", "graphify-out")
    for name in generated:
        (root / name).mkdir()
        (root / name / "noise.py").write_text("def noise():\n    return 1\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=one\n", encoding="utf-8")
    (root / ".env.local").write_text("SECRET=two\n", encoding="utf-8")

    indexer.index_project(project["id"])

    with get_conn() as conn:
        paths = {row["path"] for row in conn.execute("SELECT path FROM indexed_files")}
        chunk_paths = {row["path"] for row in conn.execute("SELECT path FROM chunks")}
    assert not any(path.startswith(generated) for path in paths | chunk_paths)
    assert ".env" not in paths | chunk_paths
    assert ".env.local" not in paths | chunk_paths
    assert "pkg/sample.py" in paths


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
