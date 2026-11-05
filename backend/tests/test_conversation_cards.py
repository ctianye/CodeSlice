"""Conversation sections (this project / others), per-file conversation cards and
the "generate a note from one answer" endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.core.errors import ConfigMissing
from app.indexing import indexer
from app.services import notegen
from app.storage import repo


class EchoModel(BaseChatModel):
    """Returns a fixed Markdown body and records the prompts it received."""

    reply: str = "## 要点\n\n这段代码做加法。\n"
    calls: list[list[BaseMessage]] = []
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        if self.fail:
            raise ConfigMissing("api key not configured")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


def _make_project(tmp_path, name: str) -> dict[str, Any]:
    root = tmp_path / name
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "sample.py").write_text("def top():\n    return 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    return repo.create_project(name, str(root))


# --------------------------------------------------------------------------- #
# list: this project first, then everybody else
# --------------------------------------------------------------------------- #
def test_include_others_puts_the_current_project_first(tmp_path, conn_reset):
    mine = _make_project(tmp_path, "mine")
    other = _make_project(tmp_path, "other")

    older = repo.create_conversation(mine["id"], "mine-old", mode="project")
    newer = repo.create_conversation(mine["id"], "mine-new", mode="project")
    foreign = repo.create_conversation(other["id"], "foreign", mode="project")

    # Force a deterministic ordering: the foreign conversation is the most recent.
    with repo.write_conn() as conn:
        conn.execute("UPDATE conversations SET updated_at = '2026-01-01T00:00:00Z' WHERE id = ?", (older["id"],))
        conn.execute("UPDATE conversations SET updated_at = '2026-01-02T00:00:00Z' WHERE id = ?", (newer["id"],))
        conn.execute("UPDATE conversations SET updated_at = '2026-01-03T00:00:00Z' WHERE id = ?", (foreign["id"],))

    rows = repo.list_conversations(mine["id"], include_others=True)
    assert [r["title"] for r in rows] == ["mine-new", "mine-old", "foreign"]
    assert rows[0]["project_name"] == "mine"
    assert rows[-1]["project_name"] == "other"
    assert all(r["has_note"] is False for r in rows)

    # Without the flag the foreign project stays invisible.
    assert [r["title"] for r in repo.list_conversations(mine["id"])] == ["mine-new", "mine-old"]


def test_list_endpoint_accepts_include_others(client, tmp_path, conn_reset):
    mine = _make_project(tmp_path, "mine")
    other = _make_project(tmp_path, "other")
    repo.create_conversation(mine["id"], "mine", mode="project")
    repo.create_conversation(other["id"], "foreign", mode="project")

    plain = client.get("/api/conversations", params={"project_id": mine["id"]}).json()
    assert [c["title"] for c in plain] == ["mine"]

    both = client.get(
        "/api/conversations",
        params={"project_id": mine["id"], "include_others": "true"},
    ).json()
    assert [c["title"] for c in both] == ["mine", "foreign"]
    assert both[1]["project_name"] == "other"


# --------------------------------------------------------------------------- #
# by-file: the data behind the code cards
# --------------------------------------------------------------------------- #
def test_by_file_orders_by_line_and_reports_notes(client, tmp_path, conn_reset):
    project = _make_project(tmp_path, "proj")
    late = repo.create_conversation(
        project["id"], "late", mode="snippet", file_path="pkg/sample.py", start_line=8, end_line=9
    )
    early = repo.create_conversation(
        project["id"], "early", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    # A project-wide conversation has no file and must not show up on any card.
    repo.create_conversation(project["id"], "whole project", mode="project")
    # ...and neither does a conversation about a different file.
    repo.create_conversation(project["id"], "readme", mode="snippet", file_path="README.md", start_line=1, end_line=1)

    repo.create_note(project["id"], "note for early", "body", early["id"])

    rows = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": "pkg/sample.py"},
    ).json()

    assert [r["title"] for r in rows] == ["early", "late"]
    assert rows[0]["has_note"] is True and rows[0]["note_id"] is not None
    assert rows[1]["has_note"] is False and rows[1]["note_id"] is None
    assert late["id"] not in {r["id"] for r in rows[:1]}


@pytest.mark.parametrize("bad", ["../outside.py", "pkg/../../outside.py", "C:/Windows/win.ini"])
def test_by_file_rejects_paths_outside_the_project(client, tmp_path, conn_reset, bad):
    project = _make_project(tmp_path, "proj")
    resp = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": bad},
    )
    assert resp.status_code == 400
    assert "escapes project root" in resp.json()["detail"]


def test_by_file_unknown_file_is_404(client, tmp_path, conn_reset):
    project = _make_project(tmp_path, "proj")
    resp = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": "pkg/missing.py"},
    )
    assert resp.status_code == 404


def test_by_file_requires_token(client, tmp_path, conn_reset):
    project = _make_project(tmp_path, "proj")
    resp = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": "README.md"},
        headers={"X-Session-Token": "wrong"},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# generate a note from one assistant message
# --------------------------------------------------------------------------- #
@pytest.fixture
def echo(monkeypatch):
    def install(reply: str = "## 要点\n\n这段代码做加法。\n", fail: bool = False) -> EchoModel:
        model = EchoModel(reply=reply, fail=fail)
        monkeypatch.setattr(notegen.model_config, "build_chat_model", lambda temperature=None: model)
        return model

    return install


def test_generate_note_from_assistant_message(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "sample.py:2-3", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(conversation["id"], "assistant", "这个函数返回 1，是个占位实现。")

    model = echo()
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note")

    assert note.status_code == 200
    body = note.json()
    assert body["conversation_id"] == conversation["id"]
    assert body["project_id"] == project["id"]
    assert body["title"] == "sample.py:2-3"
    assert body["summary_failed"] is False
    # The fixed template: title first, then the four sections in order.
    assert body["content_md"].startswith("# sample.py:2-3")
    positions = [body["content_md"].index(f"## {name}") for name in ("知识点", "关联代码", "要点", "何时使用")]
    assert positions == sorted(positions)
    # 关联代码 is filled from the citations / conversation range, not the model.
    assert "### pkg/sample.py:2-3" in body["content_md"]
    assert "return 1" in body["content_md"]
    assert "```python" in body["content_md"]
    assert body["refs"] == 1

    # The prompt must carry the answer body and the resolved code location.
    prompt = "\n".join(str(m.content) for m in model.calls[0])
    assert "这个函数返回 1" in prompt
    assert "pkg/sample.py:2-3" in prompt

    # A generated note also lands in the retrieval pipeline.
    assert body["indexed"] is True
    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "做加法"}).json()
    assert any(hit["path"] == f"note://{body['id']}" and hit["kind"] == "note" for hit in hits)
    listed = client.get("/api/notes", params={"project_id": project["id"]}).json()
    assert listed[0]["indexed"] is True

    # A second generation refreshes the same note and its chunk (no duplicates).
    again = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()
    assert again["id"] == body["id"]
    assert len(repo.list_notes(project["id"])) == 1
    with repo.get_conn() as conn:
        rows = conn.execute("SELECT id FROM chunks WHERE path = ?", (f"note://{body['id']}",)).fetchall()
    assert len(rows) == 1

    # ...and the file card now reports it.
    cards = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": "pkg/sample.py"},
    ).json()
    assert cards[0]["has_note"] is True
    assert cards[0]["note_id"] == body["id"]


def test_note_citations_do_not_become_dead_code_blocks(client, tmp_path, conn_reset, echo):
    """A reply that cites an earlier note must not copy note:// paths into 关联代码.

    Notes have no lines on disk, so such a reference could only render
    "引用位置的内容已读取不到" - useless in a note that is meant to be read later.
    """
    project = _make_project(tmp_path, "proj")
    other = repo.create_note(project["id"], "earlier note", "# earlier note\n\nbody")
    conversation = repo.create_conversation(
        project["id"], "sample.py:2-3", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(
        conversation["id"],
        "assistant",
        "先看之前的笔记，再看这段代码。",
        citations=[
            {"path": f"note://{other['id']}", "start_line": 1, "end_line": 1, "symbol": "earlier note"},
            {"path": "pkg/sample.py", "start_line": 2, "end_line": 3, "symbol": "top"},
        ],
    )

    echo()
    body = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    assert body["refs"] == 1
    assert "### pkg/sample.py:2-3" in body["content_md"]
    assert f"note://{other['id']}" not in body["content_md"]
    assert "内容已读取不到" not in body["content_md"]


def test_notes_are_reindexed_after_a_full_rebuild(client, tmp_path, conn_reset, echo):
    """A re-index wipes every chunk of the project, including note chunks."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "讲解")
    echo()
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    indexer.index_project(project["id"], force=True)

    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "要点"}).json()
    assert any(hit["path"] == f"note://{note['id']}" for hit in hits)
    assert client.get("/api/notes", params={"project_id": project["id"]}).json()[0]["indexed"] is True


def test_editing_a_note_refreshes_its_chunk(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "讲解")
    echo()
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    resp = client.put(f"/api/notes/{note['id']}", json={"content_md": "改写后的内容：闭包捕获"})
    assert resp.status_code == 200
    assert resp.json()["indexed"] is True

    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "闭包捕获"}).json()
    assert any(hit["path"] == f"note://{note['id']}" for hit in hits)
    with repo.get_conn() as conn:
        rows = conn.execute("SELECT id, content FROM chunks WHERE path = ?", (f"note://{note['id']}",)).fetchall()
    assert len(rows) == 1
    assert "闭包捕获" in rows[0]["content"]


def test_deleting_a_note_removes_its_chunk(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "讲解")
    echo()
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    assert client.delete(f"/api/notes/{note['id']}").status_code == 200

    with repo.get_conn() as conn:
        assert conn.execute("SELECT id FROM chunks WHERE path = ?", (f"note://{note['id']}",)).fetchall() == []
        assert (
            conn.execute("SELECT rowid FROM chunks_fts WHERE path LIKE ?", (f"%note://{note['id']}%",)).fetchall() == []
        )


def test_export_writes_a_markdown_file(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "sample.py:1-2", mode="snippet", file_path="pkg/sample.py", start_line=1, end_line=2
    )
    answer = repo.add_message(conversation["id"], "assistant", "讲解")
    echo()
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    resp = client.post(f"/api/notes/{note['id']}/export")
    assert resp.status_code == 200
    body = resp.json()
    exported = Path(body["path"])
    assert exported.is_file()
    assert exported.suffix == ".md"
    assert body["filename"].endswith(".md")
    text = exported.read_text(encoding="utf-8")
    assert text.startswith(f"# {note['title']}")
    assert text.count(f"# {note['title']}") == 1  # no duplicated H1
    assert "## 关联代码" in text
    assert "pkg/sample.py" in text

    # Exporting again must not clobber the first file.
    second = client.post(f"/api/notes/{note['id']}/export").json()
    assert second["path"] != body["path"]
    assert Path(second["path"]).is_file()


def test_export_unknown_note_is_404(client, conn_reset):
    assert client.post("/api/notes/9999/export").status_code == 404


def test_generate_note_rejects_user_messages(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    question = repo.add_message(conversation["id"], "user", "这段代码在干嘛？")
    echo()
    resp = client.post(f"/api/conversations/{conversation['id']}/messages/{question['id']}/note")
    assert resp.status_code == 400


def test_generate_note_unknown_ids_are_404(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    echo()
    assert client.post("/api/conversations/nope/messages/x/note").status_code == 404
    assert client.post(f"/api/conversations/{conversation['id']}/messages/nope/note").status_code == 404


def test_generate_note_keeps_the_answer_when_the_model_fails(client, tmp_path, conn_reset, echo):
    """A broken model must degrade to the raw answer, not lose the note."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "解释：这是装饰器。")
    echo(fail=True)

    resp = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note")

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary_failed"] is True
    assert "api key" in body["summary_error"]
    assert "解释：这是装饰器。" in body["content_md"]
    assert "## 知识点" in body["content_md"]


def test_generate_note_on_empty_message_is_400(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    empty = repo.add_message(conversation["id"], "assistant", "   ")
    echo()
    assert client.post(f"/api/conversations/{conversation['id']}/messages/{empty['id']}/note").status_code == 400
