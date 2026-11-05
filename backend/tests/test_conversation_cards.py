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
        # max_tokens is accepted because note generation retries an empty reply
        # with a larger budget; the stub ignores it on purpose.
        monkeypatch.setattr(
            notegen.model_config,
            "build_chat_model",
            lambda temperature=None, max_tokens=None: model,
        )
        return model

    return install


def test_generate_note_from_assistant_message(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "sample.py:2-3", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(conversation["id"], "assistant", "这个函数返回 1，是个占位实现。")

    model = echo(
        reply="## 标题\n占位函数的返回值约定\n\n## 知识点\n这个函数做加法。\n\n"
        "## 要点\n- 只返回常量\n\n## 何时使用\n- 接口未定稿时\n"
    )
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note")

    assert note.status_code == 200
    body = note.json()
    assert body["conversation_id"] == conversation["id"]
    assert body["project_id"] == project["id"]
    # The title names the knowledge point, never the selection it came from.
    assert body["title"] == "占位函数的返回值约定"
    assert body["title"] != conversation["title"]
    assert "sample.py" not in body["title"] and ":" not in body["title"]
    assert body["summary_failed"] is False
    # The fixed template: title first, then the four sections in order.
    assert body["content_md"].startswith("# 占位函数的返回值约定")
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


def test_note_chunk_does_not_repeat_the_title_heading(client, tmp_path, conn_reset, echo):
    """Generated notes start with ``# <title>``; the chunk must not prepend it again."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "sample.py:2-3", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(conversation["id"], "assistant", "这个函数返回 1。")

    echo(reply="## 标题\n占位函数返回常量\n\n## 知识点\n这个函数返回 1。\n")
    body = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    with repo.get_conn() as conn:
        row = conn.execute("SELECT content FROM chunks WHERE path = ?", (f"note://{body['id']}",)).fetchone()
    assert row is not None
    assert row["content"].count("# 占位函数返回常量") == 1, row["content"][:200]


def test_cited_markdown_keeps_its_own_fences_inside_the_block(client, tmp_path, conn_reset, echo):
    """A cited Markdown range usually contains fences of its own.

    Wrapping it in three backticks would end the block at its first inner fence
    and spill the rest of the snippet into the note as loose paragraphs, which
    then render as stray headings. The outer fence has to be longer.
    """
    project = _make_project(tmp_path, "proj")
    (Path(project["path"]) / "NOTES.md").write_text(
        "# Notes\n\n```python\nprint('inner')\n```\n\nAfter the block.\n",
        encoding="utf-8",
    )
    conversation = repo.create_conversation(
        project["id"], "NOTES.md:1-7", mode="snippet", file_path="NOTES.md", start_line=1, end_line=7
    )
    answer = repo.add_message(conversation["id"], "assistant", "这段说明演示了代码块。")

    echo()
    body = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()
    text = body["content_md"]
    section = text[text.index("## 关联代码") : text.index("## 要点")]

    # The outer fence must outnumber the inner one, otherwise the inner fence
    # would be read as the closing marker.
    outer = [line for line in section.splitlines() if line.startswith("````")]
    assert outer, section
    assert "```python" in section
    # The snippet's tail stays part of the note rather than becoming a paragraph.
    assert "After the block." in section


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


# --------------------------------------------------------------------------- #
# knowledge-point titles, whole-conversation notes, retitle
# --------------------------------------------------------------------------- #
def test_note_title_replaces_a_location_with_the_knowledge_point(client, tmp_path, conn_reset, echo):
    """The title must describe what was learned, not where the code lives."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "untitled", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(conversation["id"], "assistant", "这个函数返回 1。")

    # A model that ignores the "no location in the title" rule.
    echo(reply="## 标题\n关于 sample.py 第 2-3 行\n\n## 知识点\n占位函数把返回值写死成 1。\n")
    body = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    assert body["title"] == "占位函数把返回值写死成 1"
    assert "sample.py" not in body["title"]
    assert "行" not in body["title"]
    # The cited range still lands in 关联代码 - it just does not become the title.
    assert "### pkg/sample.py:2-3" in body["content_md"]


def test_clean_title_strips_markup_and_locations():
    assert notegen._clean_title("**关于 README.md 第 357-359 行**") == ""
    assert notegen._clean_title("`graph.py:186-204`") == ""
    assert notegen._clean_title("## 中断恢复靠 checkpoint") == "中断恢复靠 checkpoint"
    assert notegen._clean_title("关于缓存失效的策略") == ""
    assert notegen._clean_title("   ") == ""
    long_title = "一" * 60
    assert len(notegen._clean_title(long_title)) <= notegen.TITLE_MAX_CHARS


def test_long_titles_break_at_a_boundary_instead_of_mid_token():
    # A title that ends inside `/api/note` reads like a rendering bug.
    clipped = notegen._clean_title("笔记按当前打开的项目隔离，通过 GET /api/notes?project_id 过滤")
    assert clipped.endswith("隔离") or clipped.endswith("过滤")
    assert not clipped.endswith("/api/note")
    assert "/api/no" not in clipped
    # A single long ASCII token is never sliced in half either.
    token = notegen._clean_title("GET /api/conversations/by-file?path=frontend/src/App.tsx")
    assert "?" not in token or token.split("?")[0].endswith("by-file")
    assert len(token) <= notegen.TITLE_MAX_CHARS


def test_conversation_note_folds_every_turn_into_one_note(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "untitled", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    repo.add_message(conversation["id"], "user", "第一问：入口在哪？")
    repo.add_message(
        conversation["id"],
        "assistant",
        "第一答：入口在 pkg/sample.py。",
        citations=[{"path": "pkg/sample.py", "start_line": 1, "end_line": 2, "symbol": "top"}],
    )
    repo.add_message(conversation["id"], "user", "第二问：返回什么？")
    repo.add_message(
        conversation["id"],
        "assistant",
        "第二答：返回 1。",
        citations=[{"path": "pkg/sample.py", "start_line": 2, "end_line": 3, "symbol": "top"}],
    )

    model = echo(reply="## 标题\n占位函数的入口与返回值\n\n## 知识点\n整个会话讲了入口和返回值。\n")
    body = client.post(f"/api/conversations/{conversation['id']}/note").json()

    assert body["summary_failed"] is False
    assert body["title"] == "占位函数的入口与返回值"
    # One note per conversation, and every cited range is collected.
    assert body["refs"] == 2
    assert "### pkg/sample.py:1-2" in body["content_md"]
    assert "### pkg/sample.py:2-3" in body["content_md"]
    assert len(repo.list_notes(project["id"])) == 1

    prompt = "\n".join(str(m.content) for m in model.calls[0])
    assert "第一问：入口在哪？" in prompt
    assert "第二答：返回 1。" in prompt
    assert "整段会话" in prompt


def test_conversation_note_reuses_the_note_of_a_per_message_generation(client, tmp_path, conn_reset, echo):
    """Both entry points must converge on one note per conversation."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "untitled", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "讲了一段装饰器。")

    echo(reply="## 标题\n装饰器的包装时机\n\n## 知识点\n装饰器在定义时包装。\n")
    single = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()
    whole = client.post(f"/api/conversations/{conversation['id']}/note").json()

    assert whole["id"] == single["id"]
    assert len(repo.list_notes(project["id"])) == 1


def test_conversation_note_on_an_empty_conversation_is_400(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    echo()
    assert client.post(f"/api/conversations/{conversation['id']}/note").status_code == 400


def test_get_note_returns_the_note_with_its_index_state(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "c", mode="project")
    answer = repo.add_message(conversation["id"], "assistant", "讲解")
    echo(reply="## 标题\n闭包的捕获方式\n\n## 知识点\n闭包捕获变量。\n")
    created = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    body = client.get(f"/api/notes/{created['id']}")
    assert body.status_code == 200
    assert body.json()["title"] == "闭包的捕获方式"
    assert body.json()["indexed"] is True
    assert client.get("/api/notes/999999").status_code == 404


def test_retitle_rewrites_a_legacy_location_title(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    legacy = repo.create_note(project["id"], "关于 README.md 第 357-359 行", "# 关于 README.md 第 357-359 行\n\n正文")

    echo(reply="README 里的启动命令分组\n")
    body = client.post(f"/api/notes/{legacy['id']}/retitle").json()

    assert body["retitle_failed"] is False
    assert body["title"] == "README 里的启动命令分组"
    assert repo.get_note(legacy["id"])["title"] == "README 里的启动命令分组"
    # The rewritten title is what the knowledge base now matches on.
    hits = client.get(f"/api/projects/{project['id']}/search", params={"q": "启动命令"}).json()
    assert any(hit["path"] == f"note://{legacy['id']}" for hit in hits)


def test_retitle_keeps_the_note_when_the_model_fails(client, tmp_path, conn_reset, echo):
    project = _make_project(tmp_path, "proj")
    legacy = repo.create_note(project["id"], "关于 README.md 第 357-359 行", "# 关于 README.md 第 357-359 行\n\n正文")

    echo(fail=True)
    body = client.post(f"/api/notes/{legacy['id']}/retitle").json()

    assert body["retitle_failed"] is True
    assert body["title"] == "关于 README.md 第 357-359 行"
    assert repo.get_note(legacy["id"])["title"] == "关于 README.md 第 357-359 行"


def test_retitle_missing_note_is_404(client, tmp_path, conn_reset, echo):
    _make_project(tmp_path, "proj")
    echo()
    assert client.post("/api/notes/999999/retitle").status_code == 404


def test_by_file_cards_carry_the_note_title(client, tmp_path, conn_reset, echo):
    """The floating label over a code range reads the note title, not the range."""
    project = _make_project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "关于 sample.py 第 2-3 行", mode="snippet", file_path="pkg/sample.py", start_line=2, end_line=3
    )
    answer = repo.add_message(conversation["id"], "assistant", "这个函数返回 1。")
    echo(reply="## 标题\n占位函数为什么吞掉参数\n\n## 知识点\n它忽略入参。\n")
    note = client.post(f"/api/conversations/{conversation['id']}/messages/{answer['id']}/note").json()

    cards = client.get(
        f"/api/projects/{project['id']}/conversations/by-file",
        params={"path": "pkg/sample.py"},
    ).json()
    assert cards[0]["has_note"] is True
    assert cards[0]["note_id"] == note["id"]
    assert cards[0]["note_title"] == "占位函数为什么吞掉参数"
