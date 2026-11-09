"""The persisted graph cache: fast reads, precise invalidation, no stale facts.

The behaviour under test is the contract in ``app/graph/store.py``:

* a second read is a cache hit and does not call the builder;
* the payload survives a re-read (i.e. it really is off the filesystem/DB, not a
  process-local memo);
* clicking a node never rebuilds;
* the notes graph is invalidated by a note or folder mutation;
* a legacy ``graph.json`` is imported once, not rebuilt;
* bumping the schema version invalidates every stored graph.

Each test pins the *observable* thing (builder call count, endpoint behaviour)
rather than the implementation, because the point is to keep the cache honest
when the internals change again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import db
from app.core.db import GRAPH_SCOPE_CODE, GRAPH_SCOPE_NOTES, NOTES_GRAPH_PROJECT_ID
from app.graph import SCHEMA_VERSION, builder, notes, store
from app.storage import repo

from .test_graph_api import graph_project  # noqa: F401 - pytest fixture, re-exported by name


@pytest.fixture()
def stored_project(graph_project):  # noqa: F811 - fixture dependency, not a redefinition
    """``test_graph_api``'s project fixture (a row plus a written payload)."""
    return graph_project


def _payload(nodes: int = 2, *, marker: str = "stale") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "built_hash": marker,
        "nodes": [
            {
                "id": f"a.py::n{i}",
                "label": f"n{i}",
                "kind": "function",
                "path": "a.py",
                "start_line": i + 1,
                "end_line": i + 2,
                "degree": 1,
                "community": 0,
                "community_label": "a.py",
            }
            for i in range(nodes)
        ],
        "edges": [
            {"source": f"a.py::n{i}", "target": f"a.py::n{i + 1}", "relation": "calls", "confidence": "EXTRACTED"}
            for i in range(max(0, nodes - 1))
        ],
        "communities": [{"id": 0, "label": "a.py", "size": nodes, "hub": "a.py::n0", "kind": "file", "pooled": False}],
        "stats": {"files": 1, "symbols": nodes, "edges": max(0, nodes - 1), "extracted": 1, "inferred": 0},
    }


def _builder_calls(monkeypatch) -> list[Path]:
    """Replace the AST builder with a recorder that returns a trivial graph."""
    calls: list[Path] = []

    def fake_build_graph(sources=None, *, root=None):
        calls.append(root)
        return {
            "nodes": [
                {
                    "id": "b.py::built",
                    "label": "built",
                    "kind": "function",
                    "path": "b.py",
                    "start_line": 1,
                    "end_line": 2,
                    "degree": 0,
                    "community": 0,
                    "community_label": "b.py",
                }
            ],
            "edges": [],
            "communities": [],
            "stats": {"files": 1, "symbols": 1, "edges": 0, "extracted": 0, "inferred": 0},
        }

    monkeypatch.setattr(builder, "build_graph", fake_build_graph)
    return calls


# --------------------------------------------------------------------------- #
# the read path is a cache
# --------------------------------------------------------------------------- #
def test_second_read_is_a_cache_hit_and_the_builder_is_not_called(client, project, monkeypatch):
    calls = _builder_calls(monkeypatch)
    first = client.get(f"/api/projects/{project['id']}/graph").json()
    assert first["stats"]["rebuilt"] is True
    assert len(calls) == 1

    second = client.get(f"/api/projects/{project['id']}/graph").json()
    assert second["stats"]["rebuilt"] is False
    assert len(calls) == 1, "a warm read must not rebuild the graph"

    # Same content, and the shape the frontend depends on survives the cache.
    assert [n["label"] for n in second["nodes"]] == ["built"]
    assert set(second) == {"nodes", "edges", "communities", "stats"}
    assert second["stats"]["schema_version"] == SCHEMA_VERSION


def test_the_payload_really_lives_in_sqlite(client, project, monkeypatch):
    _builder_calls(monkeypatch)
    client.get(f"/api/projects/{project['id']}/graph")

    # A second connection (what a restarted process or another worker would see)
    # can read the graph without any in-memory state from the first request.
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT payload, schema_version, built_hash, node_count FROM graphs "
            "WHERE project_id = ? AND scope = ?",
            (project["id"], GRAPH_SCOPE_CODE),
        ).fetchone()
    assert row is not None
    assert row["node_count"] == 1
    assert row["schema_version"] == SCHEMA_VERSION
    assert json.loads(row["payload"])["nodes"][0]["label"] == "built"

    # And the public API agrees when asked again through that same database.
    assert store.load_cached(project["id"])["nodes"][0]["label"] == "built"


def test_read_path_does_not_walk_the_source_tree(client, project, monkeypatch):
    """The whole point: a warm read must not touch the filesystem at all."""
    _builder_calls(monkeypatch)
    client.get(f"/api/projects/{project['id']}/graph")

    def no_walk(*args, **kwargs):
        raise AssertionError("collect_sources must not run on a warm read")

    monkeypatch.setattr(builder, "collect_sources", no_walk)

    def no_resolve(*args, **kwargs):
        raise AssertionError("index_fingerprint must not run on a warm read")

    monkeypatch.setattr(store, "index_fingerprint", no_resolve)

    body = client.get(f"/api/projects/{project['id']}/graph").json()
    assert body["stats"]["rebuilt"] is False


# --------------------------------------------------------------------------- #
# every non-opening read is served, never rebuilt
# --------------------------------------------------------------------------- #
def test_node_endpoint_serves_the_cache_and_never_rebuilds(client, stored_project, monkeypatch):
    """Clicking a node in the canvas must not trigger a graph build."""
    calls = _builder_calls(monkeypatch)

    body = client.get(
        f"/api/projects/{stored_project['id']}/graph/node",
        params={"node_id": "a.py::run::function::1"},
    ).json()
    assert body["node"]["label"] == "run"
    assert calls == []


def test_node_endpoint_builds_once_when_nothing_is_stored(client, project, monkeypatch):
    calls = _builder_calls(monkeypatch)
    body = client.get(f"/api/projects/{project['id']}/graph/node", params={"node_id": "built"}).json()
    assert body["node"]["label"] == "built"
    assert len(calls) == 1
    # ... and the second click is free.
    client.get(f"/api/projects/{project['id']}/graph/node", params={"node_id": "built"})
    assert len(calls) == 1


def test_summary_and_conversation_targets_read_the_cache(client, project, monkeypatch):
    calls = _builder_calls(monkeypatch)
    client.get(f"/api/projects/{project['id']}/graph")
    assert len(calls) == 1

    conversation = repo.create_conversation(project["id"], "chat", mode="project")
    repo.add_message(conversation["id"], "user", "问", meta={"graph_anchor": "b.py::built"})

    summary = client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "b.py::built"})
    # The model is not configured in tests, so the call fails *after* the graph
    # was loaded: what matters is that it did not rebuild to get there.
    assert summary.status_code in (400, 502)
    targets = client.get(f"/api/conversations/{conversation['id']}/graph-targets").json()
    assert [node["id"] for node in targets["targets"]] == ["b.py::built"]
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# the notes graph
# --------------------------------------------------------------------------- #
def test_notes_graph_is_cached_and_invalidated_by_a_note_update(client, project):
    a = repo.create_note(project["id"], "笔记 A", "引用 pkg/sample.py")
    first = client.get("/api/graphs/notes").json()
    assert [n["label"] for n in first["nodes"]] == ["笔记 A"]

    with db.get_conn() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM graphs WHERE project_id = ? AND scope = ?",
                (NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES),
            ).fetchone()[0]
            == 1
        )

    # A read without a mutation is served from the persisted copy.
    assert client.get("/api/graphs/notes").json()["nodes"][0]["content_md"] == "引用 pkg/sample.py"

    repo.update_note(a["id"], "笔记 A（改名）", "引用 pkg/sample.py")
    assert client.get("/api/graphs/notes").json()["nodes"][0]["label"] == "笔记 A（改名）"

    repo.delete_note(a["id"])
    assert client.get("/api/graphs/notes").json()["nodes"] == []


def test_notes_graph_is_invalidated_by_a_folder_mutation(client, project):
    repo.create_note_folder("旧名字")
    note = repo.create_note(project["id"], "笔记", "正文")
    folder = client.get("/api/note-folders").json()[0]
    client.put(f"/api/notes/{note['id']}", json={"folder_id": folder["id"], "move_folder": True})

    before = client.get("/api/graphs/notes").json()
    assert any(node["community_label"] == "旧名字" for node in before["nodes"])

    client.put(f"/api/note-folders/{folder['id']}", json={"name": "新名字"})
    after = client.get("/api/graphs/notes").json()
    assert any(node["community_label"] == "新名字" for node in after["nodes"])


def test_cached_notes_graph_is_a_copy(client, project):
    repo.create_note(project["id"], "笔记", "正文")
    graph = notes.build()
    graph["nodes"].clear()
    assert notes.build()["nodes"], "a cached payload must not be mutated by its caller"


# --------------------------------------------------------------------------- #
# legacy graph.json import, exactly once
# --------------------------------------------------------------------------- #
def _write_legacy(root: Path, payload: dict) -> None:
    path = store.graph_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_legacy_graph_json_is_imported_once_instead_of_rebuilt(project, monkeypatch):
    root = Path(project["path"])
    legacy = _payload()
    legacy["built_hash"] = store.index_fingerprint(project["id"])
    _write_legacy(root, legacy)

    calls = _builder_calls(monkeypatch)
    graph, rebuilt = store.ensure_fresh(project["id"], root)

    assert rebuilt is False, "an unchanged legacy graph must be imported, not rebuilt"
    assert calls == []
    assert [n["label"] for n in graph["nodes"]] == ["n0", "n1"]

    # One row now exists, with the cheap marker rather than the content hash.
    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT built_hash FROM graphs WHERE project_id = ? AND scope = ?",
            (project["id"], GRAPH_SCOPE_CODE),
        ).fetchone()
    assert stored["built_hash"] == store.freshness_token(project["id"])

    # A second read is a plain cache hit.
    again, rebuilt_again = store.ensure_fresh(project["id"], root)
    assert rebuilt_again is False and calls == []
    assert again["nodes"] == graph["nodes"]

    # A *stale* legacy file is not imported.
    with db.write_conn() as conn:
        conn.execute("DELETE FROM graphs")
    legacy["built_hash"] = "definitely-not-the-current-tree"
    _write_legacy(root, legacy)
    graph3, rebuilt3 = store.ensure_fresh(project["id"], root)
    assert rebuilt3 is True
    assert graph3["nodes"][0]["label"] == "built"


def test_legacy_file_is_retired_so_later_starts_never_re_walk(project, monkeypatch):
    """After the first import the legacy file must not be consulted again."""
    root = Path(project["path"])
    legacy = _payload()
    legacy["built_hash"] = store.index_fingerprint(project["id"])
    _write_legacy(root, legacy)

    store.ensure_fresh(project["id"], root)
    assert not store.graph_path(root).exists(), "the retired file would cost a tree walk on every start"
    assert store.graph_path(root).with_name("graph.json.stale").is_file()

    calls = _builder_calls(monkeypatch)

    def no_collect(*args, **kwargs):
        raise AssertionError("a retired legacy file must not trigger collect_sources")

    monkeypatch.setattr(builder, "collect_sources", no_collect)
    _, rebuilt = store.ensure_fresh(project["id"], root)
    assert rebuilt is False and calls == []


def test_write_graph_without_a_project_row_still_writes_the_legacy_file(tmp_path):
    """The preview script registers a project, but fixtures may not."""
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    payload = _payload()
    store.write_graph(orphan, payload)

    assert store.graph_path(orphan).is_file()
    assert store.read_graph(orphan)["nodes"][0]["label"] == "n0"
    assert store.load_cached(999999) is None


def test_a_stale_legacy_file_is_walked_once_and_the_sources_are_reused(project, monkeypatch):
    """The upgrade path must not read the tree twice (verify, then rebuild)."""
    root = Path(project["path"])
    legacy = _payload()
    legacy["built_hash"] = "hash-from-an-older-release"
    _write_legacy(root, legacy)

    walked: list[int] = []
    real_collect = builder.collect_sources

    def counting_collect(path: Path):
        walked.append(1)
        return real_collect(path)

    monkeypatch.setattr(builder, "collect_sources", counting_collect)

    graph, rebuilt = store.ensure_fresh(project["id"], root)
    assert rebuilt is True
    assert graph["nodes"], "the rebuild must produce a real graph"
    assert len(walked) == 1, f"the source tree was read {len(walked)} times"


def test_a_row_written_by_an_older_release_is_verified_once_then_restamped(project, monkeypatch):
    """Upgrade must not force a rebuild when the stored content hash still matches."""
    root = Path(project["path"])
    store.save_cached(
        project["id"],
        GRAPH_SCOPE_CODE,
        _payload(),
        built_hash=store.index_fingerprint(project["id"]),
    )
    calls = _builder_calls(monkeypatch)

    graph, rebuilt = store.ensure_fresh(project["id"], root)
    assert rebuilt is False, "an unchanged graph from the previous release stays valid"
    assert calls == []
    assert [n["label"] for n in graph["nodes"]] == ["n0", "n1"]

    with db.get_conn() as conn:
        marker = conn.execute(
            "SELECT built_hash FROM graphs WHERE project_id = ? AND scope = ?",
            (project["id"], GRAPH_SCOPE_CODE),
        ).fetchone()["built_hash"]
    assert marker == store.freshness_token(project["id"])

    # The walk happened at most once, and the next read is a plain hit.
    assert store.load_cached(project["id"]) is not None


def test_write_graph_with_a_project_row_persists_to_sqlite(project):
    root = Path(project["path"])
    store.write_graph(root, _payload(marker="whatever"))
    assert store.load_cached(project["id"])["nodes"][0]["label"] == "n0"
    assert not store.graph_path(root).exists(), "a registered project must not also write the legacy file"


# --------------------------------------------------------------------------- #
# invalidation rules
# --------------------------------------------------------------------------- #
def test_schema_version_bump_invalidates_the_cached_graph(client, project, monkeypatch):
    calls = _builder_calls(monkeypatch)
    assert client.get(f"/api/projects/{project['id']}/graph").json()["stats"]["rebuilt"] is True
    assert client.get(f"/api/projects/{project['id']}/graph").json()["stats"]["rebuilt"] is False

    # Simulate the next release changing the builder's rules.
    monkeypatch.setattr(store, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    assert store.load_cached(project["id"]) is None
    rebuilt = client.get(f"/api/projects/{project['id']}/graph").json()
    assert rebuilt["stats"]["rebuilt"] is True
    assert len(calls) == 2


def test_an_index_run_that_changed_files_invalidates_the_code_graph(client, project, monkeypatch):
    from app.indexing import indexer

    calls = _builder_calls(monkeypatch)
    client.get(f"/api/projects/{project['id']}/graph")

    root = Path(project["path"])
    (root / "new.py").write_text("def added():\n    return 1\n", encoding="utf-8")
    indexer.index_project(project["id"])

    assert store.load_cached(project["id"]) is None
    refreshed = client.get(f"/api/projects/{project['id']}/graph").json()
    assert refreshed["stats"]["rebuilt"] is True
    assert len(calls) == 2


def test_an_index_run_that_changed_nothing_keeps_the_graph(client, project, monkeypatch):
    """Opening a project re-indexes it; that alone must not discard the graph."""
    from app.indexing import indexer

    calls = _builder_calls(monkeypatch)
    indexer.index_project(project["id"])
    client.get(f"/api/projects/{project['id']}/graph")
    assert len(calls) == 1

    stats = indexer.index_project(project["id"])
    assert stats["files"] == 0, "the second run should skip every file"
    assert store.load_cached(project["id"]) is not None
    assert client.get(f"/api/projects/{project['id']}/graph").json()["stats"]["rebuilt"] is False
    assert len(calls) == 1


def test_clear_index_drops_the_cached_graph(project, monkeypatch):
    from app.indexing import indexer

    _builder_calls(monkeypatch)
    store.ensure_fresh(project["id"], Path(project["path"]))
    indexer.clear_index(project["id"])
    assert store.load_cached(project["id"]) is None


def test_force_rebuild_replaces_the_row(client, project, monkeypatch):
    calls = _builder_calls(monkeypatch)
    client.get(f"/api/projects/{project['id']}/graph")
    response = client.post(f"/api/projects/{project['id']}/graph/build")
    assert response.status_code == 200
    assert response.json()["rebuilt"] is True
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def test_graph_response_is_gzipped_when_the_client_accepts_it(client, project):
    """A ~2 MB payload must go out compressed; small replies stay untouched."""
    payload = _payload(nodes=400)
    payload["built_hash"] = store.freshness_token(project["id"])
    store.write_graph(Path(project["path"]), payload)

    response = client.get(
        f"/api/projects/{project['id']}/graph",
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    # httpx decompresses transparently; the body must still be the same JSON.
    assert len(response.json()["nodes"]) == 400

    small = client.get(
        f"/api/projects/{project['id']}/graph/node",
        params={"node_id": "a.py::n0"},
        headers={"Accept-Encoding": "gzip"},
    )
    assert small.status_code == 200
    assert "content-encoding" not in small.headers


@pytest.mark.parametrize("scope", [GRAPH_SCOPE_CODE, GRAPH_SCOPE_NOTES])
def test_invalidate_is_scope_specific(scope):
    other = GRAPH_SCOPE_NOTES if scope == GRAPH_SCOPE_CODE else GRAPH_SCOPE_CODE
    with db.write_conn() as conn:
        conn.execute("DELETE FROM graphs")
    store.save_cached(1, scope, _payload())
    store.save_cached(1, other, _payload())
    store.invalidate(1, scope)
    assert store.load_cached(1, scope) is None
    assert store.load_cached(1, other) is not None


def test_a_rebuild_racing_an_index_run_does_not_persist_stale_results(project, monkeypatch):
    """A build that overlaps an invalidation must not label old data as fresh.

    Reading the tree takes seconds; an index run finishing in that window means
    the payload describes a corpus that no longer exists. Persisting it with a
    fresh marker would serve stale relationships forever, because nothing would
    invalidate them again.
    """
    walked = []

    def build_then_race(sources=None, *, root=None):
        walked.append(root)
        # Simulate the index run landing mid-build.
        store.invalidate(project["id"])
        return {
            "nodes": [{"id": "stale", "label": "stale", "kind": "function", "path": "x.py"}],
            "edges": [],
            "communities": [],
            "stats": {},
        }

    monkeypatch.setattr(builder, "build_graph", build_then_race)
    graph, rebuilt = store.ensure_fresh(project["id"], Path(project["path"]))

    assert rebuilt is True
    assert [n["label"] for n in graph["nodes"]] == ["stale"], "the caller still gets an answer"
    assert store.load_cached(project["id"]) is None, "but nothing stale is persisted as fresh"

    # The next read rebuilds from the (new) corpus.
    monkeypatch.setattr(builder, "build_graph", lambda sources=None, *, root=None: {
        "nodes": [{"id": "fresh", "label": "fresh", "kind": "function", "path": "x.py"}],
        "edges": [],
        "communities": [],
        "stats": {},
    })
    healed, rebuilt_again = store.ensure_fresh(project["id"], Path(project["path"]))
    assert rebuilt_again is True
    assert [n["label"] for n in healed["nodes"]] == ["fresh"]
    assert len(walked) == 1


def test_write_graph_stamps_a_missing_marker_so_the_next_read_is_a_hit(project, monkeypatch):
    """A fixture payload with no ``built_hash`` must not force a rebuild."""
    payload = _payload(marker="")
    del payload["built_hash"]
    store.write_graph(Path(project["path"]), payload)
    calls = _builder_calls(monkeypatch)
    graph, rebuilt = store.ensure_fresh(project["id"], Path(project["path"]))
    assert rebuilt is False and calls == []
    assert [n["label"] for n in graph["nodes"]] == ["n0", "n1"]


def test_a_cached_graph_for_a_deleted_project_is_not_served_as_fresh(project, monkeypatch):
    """The joined marker must agree with ``freshness_token`` for a missing row."""
    store.save_cached(project["id"], GRAPH_SCOPE_CODE, _payload(), built_hash="stale")
    calls = _builder_calls(monkeypatch)
    with db.write_conn() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project["id"],))

    _, rebuilt = store.ensure_fresh(project["id"], Path(project["path"]))
    assert rebuilt is True
    assert len(calls) == 1
