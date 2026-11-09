"""Tests for the graph HTTP endpoints and the auto-derived workflow topology."""

from __future__ import annotations

import pytest

from app.agent import node_config
from app.api import graph_api
from app.graph import SCHEMA_VERSION, store


@pytest.fixture()
def graph_project(monkeypatch, tmp_path, conn_reset):
    """A project row whose graph.json exists on disk.

    The endpoints only need ``id`` and ``path``; the graph itself is written as a
    file so the test does not depend on the chunker.
    """
    from app.storage import repo

    project_path = tmp_path / "proj"
    project_path.mkdir()
    project = repo.create_project("demo", str(project_path))

    payload = {
        "schema_version": SCHEMA_VERSION,
        # Any hash will do: the endpoint compares it against the chunk table, so a
        # mismatch simply triggers a rebuild, which is also fine here.
        "built_hash": "stale",
        "nodes": [
            {
                "id": "a.py::run::function::1",
                "label": "run",
                "kind": "function",
                "path": "a.py",
                "language": "python",
                "start_line": 1,
                "end_line": 3,
                "parent": "a.py::__file__",
                "degree": 2,
                "community": 0,
                "community_label": "a.py",
            },
            {
                "id": "a.py::__file__",
                "label": "a.py",
                "kind": "file",
                "path": "a.py",
                "language": "python",
                "start_line": 1,
                "end_line": 3,
                "parent": None,
                "degree": 1,
                "community": 0,
                "community_label": "a.py",
            },
        ],
        "edges": [
            {
                "source": "a.py::__file__",
                "target": "a.py::run::function::1",
                "relation": "contains",
                "confidence": "EXTRACTED",
            }
        ],
        "communities": [
            {"id": 0, "label": "a.py", "size": 2, "hub": "a.py::__file__", "kind": "file", "pooled": False}
        ],
        "stats": {"files": 1, "symbols": 1, "edges": 1, "extracted": 1, "inferred": 0},
    }
    store.write_graph(project_path, payload)
    # Pin the freshness hash so ensure_fresh serves the file instead of rebuilding.
    monkeypatch.setattr(store, "index_fingerprint", lambda _pid: "stale")
    return project


def test_get_graph_returns_the_stored_payload(client, graph_project):
    resp = client.get(f"/api/projects/{graph_project['id']}/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert {node["label"] for node in body["nodes"]} == {"run", "a.py"}
    assert body["edges"][0]["relation"] == "contains"
    assert body["communities"][0]["label"] == "a.py"
    assert body["stats"]["files"] == 1


def test_get_graph_reports_whether_it_rebuilt(client, graph_project):
    body = client.get(f"/api/projects/{graph_project['id']}/graph").json()
    # The pinned fingerprint matches, so this read was served from disk.
    assert body["stats"]["rebuilt"] is False


def test_node_detail_lists_outgoing_connections(client, graph_project):
    resp = client.get(
        f"/api/projects/{graph_project['id']}/graph/node",
        params={"node_id": "a.py::run::function::1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["node"]["label"] == "run"
    assert [edge["label"] for edge in body["edges"]] == ["a.py"]
    assert body["edges"][0]["direction"] == "in"


def test_node_detail_accepts_a_bare_label(client, graph_project):
    body = client.get(
        f"/api/projects/{graph_project['id']}/graph/node",
        params={"node_id": "run"},
    ).json()
    assert body["node"]["label"] == "run"


def test_node_detail_returns_null_for_an_unknown_node(client, graph_project):
    resp = client.get(
        f"/api/projects/{graph_project['id']}/graph/node",
        params={"node_id": "nope"},
    )
    assert resp.status_code == 200
    assert resp.json()["node"] is None
    assert resp.json()["edges"] == []


def test_graph_endpoints_require_a_token(graph_project):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as anonymous:
        assert anonymous.get(f"/api/projects/{graph_project['id']}/graph").status_code == 401


def test_get_graph_for_a_missing_project_is_404(client):
    assert client.get("/api/projects/99999/graph").status_code == 404


# --------------------------------------------------------------------------- #
# workflow topology is derived, not hand-maintained
# --------------------------------------------------------------------------- #
def test_workflow_edges_come_from_the_compiled_graph(client):
    resp = client.get("/api/agent/graph")
    assert resp.status_code == 200
    edges = {(edge["from"], edge["to"]) for edge in resp.json()["edges"]}
    # Every node the panel draws must exist in the real graph.
    assert ("__start__", "ensure_graph") in edges
    assert ("ensure_graph", "prepare") in edges
    assert ("prepare", "agent") in edges
    assert ("agent", "run_readonly_batch") in edges
    assert ("agent", "run_one") in edges
    assert ("finalize", "__end__") in edges


def test_workflow_edges_mark_conditionals(client):
    edges = client.get("/api/agent/graph").json()["edges"]
    by_key = {(edge["from"], edge["to"]): edge for edge in edges}
    assert by_key[("agent", "run_one")]["conditional"] is True
    assert by_key[("ensure_graph", "prepare")]["conditional"] is False


def test_every_workflow_node_has_panel_config(client):
    """A node in the graph without a ``NODES`` entry renders no panel fields."""
    from app.agent import graph as agent_graph

    drawn = agent_graph.build_graph().get_graph()
    ids = set(drawn.nodes)
    for node_id in ids:
        if node_id in ("__start__", "__end__"):
            continue
        assert node_id in node_config.NODES, f"{node_id} is in the graph but not in NODES"


def test_edges_helper_labels_are_known():
    """A label key must correspond to a real edge, or the panel shows nothing."""
    real = {(edge["from"], edge["to"]) for edge in graph_api._edges()}
    for key in graph_api._EDGE_LABELS:
        source, _, target = key.partition("->")
        assert (source, target) in real, f"label for a non-existent edge: {key}"
