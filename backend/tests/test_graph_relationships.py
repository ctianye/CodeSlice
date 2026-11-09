"""Graph scopes, complete one-hop evidence and conversation navigation."""

import json
from types import SimpleNamespace

import pytest

from app.core.errors import ConfigMissing
from app.graph import notes, relationships, service, store
from app.storage import repo


@pytest.fixture
def topology(monkeypatch):
    nodes = [
        {
            "id": n,
            "label": n,
            "kind": "function",
            "path": "a.py",
            "start_line": i + 1,
            "end_line": i + 3,
            "degree": 1,
            "community": 0,
            "community_label": "A",
        }
        for i, n in enumerate(["center", "in", "out", "far"])
    ]
    edges = [
        {"source": a, "target": b, "relation": "calls", "confidence": c}
        for a, b, c in [
            ("in", "center", "EXTRACTED"),
            ("center", "out", "INFERRED"),
            ("out", "far", "EXTRACTED"),
        ]
    ]
    graph = {"nodes": nodes, "edges": edges, "communities": []}
    monkeypatch.setattr(store, "ensure_fresh", lambda *args: (graph, False))
    return graph


@pytest.fixture
def model(monkeypatch):
    calls = []

    class Model:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="## 核心作用\n中心调用 out，被 in 调用。", response_metadata={})

        def stream(self, messages):
            calls.append(messages)
            yield SimpleNamespace(content="## 核心作用\n", response_metadata={})
            yield SimpleNamespace(content="中心调用 out。", response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(relationships.model_config, "build_chat_model", lambda **kw: Model())
    return calls


def test_summary_exact_one_hop_and_directions(client, project, topology, model):
    response = client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "center"})
    assert response.status_code == 200
    result = response.json()
    assert result["neighbor_count"] == 2
    assert result["edge_count"] == 2
    assert result["scope"] == "code"
    evidence = json.loads(model[0][1].content)
    assert evidence["node"]["id"] == "center"
    assert {node["id"] for node in evidence["neighbors"]} == {"in", "out"}
    assert {(edge["source"], edge["target"]) for edge in evidence["edges"]} == {("in", "center"), ("center", "out")}
    assert evidence["edges"][1]["confidence"] == "INFERRED"
    assert "far" not in model[0][1].content


def test_summary_never_slices_high_degree_hub(topology, model):
    for i in range(250):
        topology["nodes"].append({"id": f"extra{i}", "label": f"extra{i}"})
        topology["edges"].append(
            {"source": "center", "target": f"extra{i}", "relation": "calls", "confidence": "EXTRACTED"}
        )
    result = relationships.summarize(topology, "center", "code")
    assert result["neighbor_count"] == 252
    assert len(json.loads(model[0][1].content)["neighbors"]) == 252


def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_summary_stream_emits_deltas_then_done(client, project, topology, model):
    response = client.post(
        f"/api/projects/{project['id']}/graph/summary/stream",
        json={"node_id": "center"},
    )
    assert response.status_code == 200
    frames = _frames(response.text)
    assert [frame["type"] for frame in frames] == ["delta", "delta", "done"]
    assert "".join(frame.get("text", "") for frame in frames) == "## 核心作用\n中心调用 out。"
    done = frames[-1]
    assert done["scope"] == "code"
    assert done["neighbor_count"] == 2
    assert done["edge_count"] == 2
    assert json.loads(model[0][1].content)["node"]["id"] == "center"


def test_summary_stream_reports_model_failure_as_error_frame(client, project, topology, monkeypatch):
    class Broken:
        def stream(self, messages):
            raise RuntimeError("provider failed")

        def invoke(self, messages):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(relationships.model_config, "build_chat_model", lambda **kw: Broken())
    response = client.post(
        f"/api/projects/{project['id']}/graph/summary/stream",
        json={"node_id": "center"},
    )
    assert response.status_code == 200
    frames = _frames(response.text)
    assert frames[-1]["type"] == "error"
    assert "AI 解读暂时失败" in frames[-1]["detail"]


def test_missing_or_wrong_scope_node_does_not_call_model(client, project, topology, model):
    assert client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "note://1"}).status_code == 404
    assert client.post("/api/graphs/notes/summary", json={"node_id": "center"}).status_code == 404
    assert not model


def test_summary_no_neighbors(topology, model):
    topology["edges"] = []
    result = relationships.summarize(topology, "center", "code")
    assert result["neighbor_count"] == 0
    assert json.loads(model[0][1].content)["edges"] == []


@pytest.mark.parametrize("failure", ["empty", "exception", "length", "anthropic_length"])
def test_summary_failures_are_retryable(client, project, topology, monkeypatch, failure):
    class Model:
        def invoke(self, messages):
            if failure == "exception":
                raise RuntimeError("provider failed")
            return SimpleNamespace(
                content="" if failure == "empty" else "incomplete",
                response_metadata={"finish_reason": "length"}
                if failure == "length"
                else {"stop_reason": "max_tokens"}
                if failure == "anthropic_length"
                else {},
            )

    monkeypatch.setattr(relationships.model_config, "build_chat_model", lambda **kw: Model())
    response = client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "center"})
    assert response.status_code == 502
    assert "summary" not in response.json()


def test_model_configuration_failure(client, project, topology, monkeypatch):
    def missing(**kwargs):
        raise ConfigMissing("model not configured")

    monkeypatch.setattr(relationships.model_config, "build_chat_model", missing)
    response = client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "center"})
    assert response.status_code == 400


def test_oversize_evidence_rejected_without_dropping_neighbors(topology, model):
    topology["nodes"][0]["content_md"] = "x" * 180_001
    with pytest.raises(Exception, match="全部直接邻点"):
        relationships.summarize(topology, "center", "code")
    assert not model


def test_notes_topology_and_ai_share_same_edges(client, project, topology, model):
    a = repo.create_note(project["id"], "笔记 A", "引用 pkg/sample.py")
    b = repo.create_note(project["id"], "笔记 B", "也引用 pkg/sample.py")
    c = repo.create_note(project["id"], "笔记 C", "其他内容")
    payload = client.get("/api/graphs/notes").json()
    assert all(node["kind"] == "note" and node["id"].startswith("note://") for node in payload["nodes"])
    assert {edge["relation"] for edge in payload["edges"]} == {"same_project", "shared_reference"}
    result = client.post("/api/graphs/notes/summary", json={"node_id": f"note://{a['id']}"})
    assert result.status_code == 200
    assert result.json()["scope"] == "notes"
    data = json.loads(model[0][1].content)
    assert [node["note_id"] for node in data["neighbors"]] == [b["id"]]
    assert data["node"]["content_md"] == a["content_md"]
    assert all(edge in payload["edges"] for edge in data["edges"])
    assert f"note://{c['id']}" not in {node["id"] for node in data["neighbors"]}
    chat_context = json.loads(service.context_for_node(project["id"], None, f"note://{a['id']}"))
    assert chat_context == data


def test_reference_paths_are_project_scoped(project, tmp_path):
    other = repo.create_project("other", str(tmp_path / "other"))
    repo.create_note(project["id"], "A", "same.py")
    repo.create_note(other["id"], "B", "same.py")
    assert notes.build()["edges"] == []


def test_shared_reference_normalization_and_evidence(project):
    folder = repo.create_note_folder("folder")
    repo.create_note(project["id"], "A", r"./pkg/sample.py and pkg\other.py", folder_id=folder["id"])
    repo.create_note(project["id"], "B", "pkg/sample.py and pkg/other.py")
    edges = notes.build()["edges"]
    assert len(edges) == 1
    assert edges[0]["relation"] == "shared_reference"
    assert set(edges[0]["evidence"]) == {"pkg/sample.py", "pkg/other.py"}


def test_conversation_locator_uses_precise_symbol_and_anchor(client, project, topology):
    conversation = repo.create_conversation(
        project["id"], "code", "snippet", file_path="a.py", start_line=4, end_line=4
    )
    repo.add_message(conversation["id"], "user", "question", meta={"graph_anchor": "center"})
    repo.add_message(
        conversation["id"], "assistant", "answer", citations=[{"path": "a.py", "start_line": 4, "end_line": 4}]
    )
    response = client.get(f"/api/conversations/{conversation['id']}/graph-targets")
    assert response.status_code == 200
    result = response.json()
    assert result["project_id"] == project["id"]
    assert result["targets"][0]["id"] == "center"
    assert len({node["id"] for node in result["targets"]}) == len(result["targets"])


def test_conversation_locator_prefers_smallest_enclosing_symbol(client, project, topology):
    topology["nodes"] = [
        {"id": "file", "kind": "file", "path": "a.py", "start_line": 1, "end_line": 100},
        {"id": "class", "kind": "class", "path": "a.py", "start_line": 5, "end_line": 50},
        {"id": "method", "kind": "function", "path": "a.py", "start_line": 10, "end_line": 20},
    ]
    conversation = repo.create_conversation(project["id"], "code", "snippet", file_path="a.py", start_line=12)
    result = client.get(f"/api/conversations/{conversation['id']}/graph-targets").json()
    assert [node["id"] for node in result["targets"]] == ["method"]


def test_conversation_locator_no_guessed_target(client, project, topology):
    conversation = repo.create_conversation(project["id"], "center", "project")
    assert client.get(f"/api/conversations/{conversation['id']}/graph-targets").json()["targets"] == []
    assert client.get("/api/conversations/missing/graph-targets").status_code == 404


def test_new_graph_routes_require_authentication(client, project):
    client.headers.pop("X-Session-Token")
    assert client.get("/api/graphs/notes").status_code == 401
    assert client.post("/api/graphs/notes/summary", json={"node_id": "note://1"}).status_code == 401
    assert client.post("/api/graphs/notes/summary/stream", json={"node_id": "note://1"}).status_code == 401
    assert client.post(f"/api/projects/{project['id']}/graph/summary", json={"node_id": "x"}).status_code == 401
    assert client.post(f"/api/projects/{project['id']}/graph/summary/stream", json={"node_id": "x"}).status_code == 401
    assert client.get("/api/conversations/x/graph-targets").status_code == 401
