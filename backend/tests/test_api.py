"""HTTP surface: auth, projects, indexing, conversations, notes, rules, settings."""

from __future__ import annotations

import time
from pathlib import Path

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
    assert body["binary"] is False

    escape = client.get(f"/api/projects/{project['id']}/file", params={"path": "../../x.txt"})
    assert escape.status_code == 400


def test_binary_files_are_reported_without_decoding(client, project):
    root = Path(project["path"])
    (root / "checkpoints.db").write_bytes(b"SQLite format 3\x00" + bytes(range(64)))
    db = client.get(f"/api/projects/{project['id']}/file", params={"path": "checkpoints.db"}).json()
    assert db["binary"] is True
    assert db["language"] == "binary"
    assert db["content"] == ""

    # No known suffix: a NUL byte or invalid UTF-8 is still data, not text.
    (root / "blob.dat").write_bytes(b"\x00\x01\x02binary")
    blob = client.get(f"/api/projects/{project['id']}/file", params={"path": "blob.dat"}).json()
    assert blob["binary"] is True
    assert blob["content"] == ""

    write = client.put(
        f"/api/projects/{project['id']}/file",
        json={"path": "checkpoints.db", "content": "nope"},
    )
    assert write.status_code == 400


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


def test_opening_project_starts_backend_indexing(client, project):
    opened = client.post(f"/api/projects/{project['id']}/open")
    assert opened.status_code == 200
    assert opened.json()["id"] == project["id"]

    for _ in range(100):
        status = client.get(f"/api/projects/{project['id']}/index").json()
        job = status["last_job"]
        if job and job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert status["files"] > 0


def test_opened_project_reindexes_files_changed_on_disk(client, project):
    from pathlib import Path

    from app.storage import repo

    client.post(f"/api/projects/{project['id']}/open")
    for _ in range(100):
        first = client.get(f"/api/projects/{project['id']}/index").json()["last_job"]
        if first and first["status"] == "done":
            break
        time.sleep(0.1)
    assert first and first["status"] == "done"

    (Path(project["path"]) / "watched.py").write_text("def watched():\n    return 42\n", encoding="utf-8")
    for _ in range(150):
        status = client.get(f"/api/projects/{project['id']}/index").json()
        latest = status["last_job"]
        if latest and latest["id"] != first["id"] and latest["status"] == "done":
            break
        time.sleep(0.1)

    assert latest and latest["id"] != first["id"]
    assert latest["status"] == "done", latest
    with repo.get_conn() as conn:
        assert conn.execute(
            "SELECT id FROM indexed_files WHERE project_id = ? AND path = ?",
            (project["id"], "watched.py"),
        ).fetchone()


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


def test_annotations_roundtrip(client, project):
    created = client.post(
        f"/api/projects/{project['id']}/annotations",
        json={
            "file_path": "pkg/sample.py",
            "start_line": 2,
            "end_line": 4,
            "selected_code": "def sample():",
            "title": "为什么这样写",
            "body": "注释内容",
        },
    )
    assert created.status_code == 200
    annotation = created.json()
    assert annotation["title"] == "为什么这样写"
    assert annotation["body"] == "注释内容"
    listed = client.get(f"/api/projects/{project['id']}/annotations", params={"file_path": "pkg/sample.py"})
    assert [item["id"] for item in listed.json()] == [annotation["id"]]

    # Older clients only send a body; the first line becomes the collapsed title.
    fallback = client.post(
        f"/api/projects/{project['id']}/annotations",
        json={"file_path": "pkg/sample.py", "start_line": 5, "end_line": 5, "body": "首行标题\n正文第二行"},
    ).json()
    assert fallback["title"] == "首行标题"

    assert client.delete(f"/api/annotations/{annotation['id']}").json() == {"ok": True}
    assert client.delete(f"/api/annotations/{fallback['id']}").json() == {"ok": True}
    assert client.get(f"/api/projects/{project['id']}/annotations", params={"file_path": "pkg/sample.py"}).json() == []


def test_chat_settings_support_32k_and_custom_prompts(client):
    initial = client.get("/api/settings/model").json()
    assert initial["chat"]["max_tokens"] == 32768
    assert initial["default_system_prompt"]
    assert initial["default_qa_prompt"]
    assert "{location}" in initial["default_note_prompt"]
    response = client.put(
        "/api/settings/model",
        json={
            "chat": {
                "max_tokens": 32768,
                "system_prompt": "系统规则自定义",
                "qa_prompt": "问答方式自定义",
                "note_prompt": "笔记格式自定义 {location}",
            }
        },
    )
    assert response.status_code == 200
    assert response.json()["chat"]["system_prompt"] == "系统规则自定义"
    assert response.json()["chat"]["qa_prompt"] == "问答方式自定义"
    assert response.json()["chat"]["note_prompt"] == "笔记格式自定义 {location}"
    client.put("/api/settings/model", json={"chat": {"qa_prompt": "", "note_prompt": ""}})


def test_note_generation_uses_saved_note_prompt(conversation, monkeypatch):
    from types import SimpleNamespace

    from app.services import notegen

    captured = []

    class FakeModel:
        def invoke(self, messages):
            captured.append(messages)
            return SimpleNamespace(content="## 标题\n测试标题\n\n## 知识点\n测试内容")

    monkeypatch.setattr(notegen.model_config, "build_chat_model", lambda **_: FakeModel())
    monkeypatch.setattr(
        notegen.model_config,
        "load_settings",
        lambda: SimpleNamespace(chat=SimpleNamespace(note_prompt="用户的笔记格式 {location}")),
    )

    sections, error = notegen._model_sections("原始回答", conversation)

    assert error is None
    assert sections and sections["知识点"] == "测试内容"
    assert captured[0][0][1] == f"用户的笔记格式 {notegen._location(conversation)}"


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
