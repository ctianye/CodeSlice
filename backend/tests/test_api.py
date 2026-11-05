"""HTTP surface: auth, projects, indexing, conversations, notes, rules, settings."""

from __future__ import annotations

import time

import pytest


def test_health_is_public(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "CodeSlice"


def test_missing_token_is_rejected(client):
    resp = client.get("/api/projects", headers={"X-Session-Token": ""})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid session token"


def test_wrong_token_is_rejected(client):
    resp = client.get("/api/projects", headers={"X-Session-Token": "nope"})
    assert resp.status_code == 401


def test_project_crud(client, project):
    listing = client.get("/api/projects").json()
    assert any(p["id"] == project["id"] for p in listing)

    one = client.get(f"/api/projects/{project['id']}")
    assert one.status_code == 200 and one.json()["name"] == "proj"

    assert client.get("/api/projects/99999").status_code == 404

    # Re-adding the same folder is idempotent: it returns the existing record.
    dup = client.post("/api/projects", json={"path": project["path"], "name": "again"})
    assert dup.status_code == 200
    assert dup.json()["id"] == project["id"]


def test_create_project_rejects_missing_path(client):
    resp = client.post("/api/projects", json={"path": "D:/definitely-not-here-xyz"})
    assert resp.status_code == 400


def test_tree_and_file(client, project):
    tree = client.get(f"/api/projects/{project['id']}/tree", params={"depth": 2}).json()
    names = {node["name"] for node in tree}
    assert "pkg" in names
    assert "README.md" in names
    assert "node_modules" not in names  # ignored directory

    file_resp = client.get(f"/api/projects/{project['id']}/file", params={"path": "pkg/sample.py"})
    assert file_resp.status_code == 200
    body = file_resp.json()
    assert body["language"] == "python"
    assert "def bar" in body["content"]
    assert body["truncated"] is False

    escape = client.get(f"/api/projects/{project['id']}/file", params={"path": "../../x.txt"})
    assert escape.status_code == 400


def test_git_status_on_non_repo(client, project):
    body = client.get(f"/api/projects/{project['id']}/git/status").json()
    assert body["is_repo"] is False
    assert body["branch"] is None
    assert body["staged"] == []


def test_index_job_lifecycle(client, project):
    started = client.post(f"/api/projects/{project['id']}/index")
    assert started.status_code == 200
    job_id = started.json()["job_id"]

    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["progress"] == 100
    assert job["stats"]["chunks"] > 0

    status = client.get(f"/api/projects/{project['id']}/index").json()
    assert status["files"] > 0
    assert status["last_job"]["id"] == job_id


def test_search_requires_index(client, project):
    resp = client.get(f"/api/projects/{project['id']}/search", params={"q": "bar"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_conversation_flow(client, project):
    created = client.post(
        "/api/conversations",
        json={
            "project_id": project["id"],
            "mode": "snippet",
            "file_path": "pkg/sample.py",
            "start_line": 9,
            "end_line": 10,
            "selected_code": "def bar(self, x):",
        },
    )
    assert created.status_code == 200
    conversation = created.json()
    assert conversation["title"] == "sample.py:9-10"
    assert "selected_code" not in conversation

    detail = client.get(f"/api/conversations/{conversation['id']}").json()
    assert detail["messages"] == []
    assert detail["pending_interrupt"] is None

    listed = client.get("/api/conversations", params={"project_id": project["id"]}).json()
    assert [c["id"] for c in listed] == [conversation["id"]]

    assert client.delete(f"/api/conversations/{conversation['id']}").json() == {"ok": True}
    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404


def test_message_requires_model_config(client, project):
    conversation = client.post("/api/conversations", json={"project_id": project["id"], "mode": "project"}).json()
    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "hi"})
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_resume_rejects_bad_decision(client, project):
    conversation = client.post("/api/conversations", json={"project_id": project["id"], "mode": "project"}).json()
    resp = client.post(
        f"/api/conversations/{conversation['id']}/resume",
        json={"interrupt_id": "x", "decision": "maybe"},
    )
    assert resp.status_code == 400


def test_notes_crud(client, project):
    note = client.post(
        "/api/notes",
        json={"project_id": project["id"], "title": "idea", "content_md": "# hi"},
    ).json()
    assert note["title"] == "idea"

    updated = client.put(f"/api/notes/{note['id']}", json={"title": "renamed"}).json()
    assert updated["title"] == "renamed"

    assert any(n["id"] == note["id"] for n in client.get("/api/notes", params={"project_id": project["id"]}).json())
    assert client.delete(f"/api/notes/{note['id']}").json() == {"ok": True}
    assert client.put("/api/notes/99999", json={"title": "x"}).status_code == 404


def test_command_rules_crud(client, project):
    rule = client.post(
        f"/api/projects/{project['id']}/command-rules",
        json={"pattern": "pytest *", "scope": "project", "needs_confirm": False},
    ).json()
    assert rule["needs_confirm"] is False

    rules = client.get(f"/api/projects/{project['id']}/command-rules").json()
    assert rules[0]["pattern"] == "pytest *"

    client.put(f"/api/projects/{project['id']}/command-rules/{rule['id']}", json={"needs_confirm": True})
    assert client.get(f"/api/projects/{project['id']}/command-rules").json()[0]["needs_confirm"] is True

    assert client.delete(f"/api/projects/{project['id']}/command-rules/{rule['id']}").json() == {"ok": True}


def test_anchors_endpoints(client, project, conversation):
    from app.services import anchors as anchors_service

    anchors_service.create_for_citation(project["id"], conversation["id"], "pkg/sample.py", 9, 10)
    listed = client.get(f"/api/projects/{project['id']}/anchors").json()
    assert len(listed) == 1 and listed[0]["status"] == "unchanged"

    refreshed = client.post(f"/api/projects/{project['id']}/anchors/refresh").json()
    assert refreshed["deleted"] == 0


def test_settings_roundtrip_never_returns_key(client):
    resp = client.put(
        "/api/settings/model",
        json={
            "chat": {
                "protocol": "openai-compatible",
                "model": "gpt-test",
                "base_url": "https://x/v1",
                "api_key": "sk-secret-value",
            },
            "embedding": {"model": "text-embedding-3-small", "dimensions": 1536},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["chat"]["model"] == "gpt-test"
    assert body["chat"]["api_key_configured"] is True
    assert "api_key" not in body["chat"]
    assert "sk-secret-value" not in resp.text
    assert body["embedding"]["dimensions"] == 1536

    cleared = client.delete("/api/settings/model/key", params={"slot": "chat"})
    assert cleared.json() == {"ok": True}
    assert client.get("/api/settings/model").json()["chat"]["api_key_configured"] is False


def test_tests_never_touch_the_production_credential_store():
    """Guard rail: a test that forgets the sandbox would wipe a user's real key.

    ``conftest`` points ``CODESLICE_KEY_SERVICE`` at ``codeslice-test``; if that
    ever regresses, ``test_settings_roundtrip_never_returns_key`` would delete the
    API key of the installed app on every run.
    """
    from app.models import config as model_config

    assert model_config.SERVICE == "codeslice-test"


def test_unknown_route_is_404(client):
    assert client.get("/api/nope").status_code == 404


@pytest.mark.parametrize("mode", ["snippet", "project"])
def test_conversation_modes(client, project, mode):
    resp = client.post("/api/conversations", json={"project_id": project["id"], "mode": mode})
    assert resp.status_code == 200


def test_conversation_rejects_bad_mode(client, project):
    resp = client.post("/api/conversations", json={"project_id": project["id"], "mode": "weird"})
    assert resp.status_code == 400
