"""Knowledge base across projects, folders, knowledge-point titles and cleanup.

These cover the parts a reader would notice immediately: notes from every project
showing up in one tree, folders the user made themselves, a conversation title
that names what was learned instead of where the code sits, and abandoned empty
conversations not piling up in the sidebar.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent import graph as agent_graph
from app.services import notegen, notes_export
from app.storage import repo


def _project(tmp_path, name: str) -> dict[str, Any]:
    root = tmp_path / name
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "sample.py").write_text("def top():\n    return 1\n", encoding="utf-8")
    return repo.create_project(name, str(root))


# --------------------------------------------------------------------------- #
# knowledge base: every project in one place
# --------------------------------------------------------------------------- #
def test_knowledge_base_lists_notes_from_every_project(client, tmp_path, conn_reset):
    first = _project(tmp_path, "first")
    second = _project(tmp_path, "second")
    a = repo.create_note(first["id"], "闭包捕获变量", "# 闭包捕获变量\n")
    b = repo.create_note(second["id"], "索引何时重建", "# 索引何时重建\n")

    everything = client.get("/api/notes").json()
    assert {n["id"] for n in everything} == {a["id"], b["id"]}
    # The panel shows where each note came from without a second request.
    names = {n["id"]: n["project_name"] for n in everything}
    assert names[a["id"]] == "first" and names[b["id"]] == "second"

    # Older callers still get the per-project view.
    only_first = client.get("/api/notes", params={"project_id": first["id"]}).json()
    assert [n["id"] for n in only_first] == [a["id"]]


def test_folders_organise_notes_and_keep_them_when_deleted(client, tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    folder = client.post("/api/note-folders", json={"name": "并发"}).json()
    child = client.post("/api/note-folders", json={"name": "GIL", "parent_id": folder["id"]}).json()
    note = repo.create_note(project["id"], "锁与原子操作", "# 锁与原子操作\n")

    moved = client.put(f"/api/notes/{note['id']}", json={"folder_id": folder["id"], "move_folder": True}).json()
    assert moved["folder_id"] == folder["id"]

    listing = client.get("/api/note-folders").json()
    by_id = {f["id"]: f for f in listing}
    assert by_id[folder["id"]]["note_count"] == 1
    assert by_id[child["id"]]["parent_id"] == folder["id"]

    in_folder = client.get("/api/notes", params={"folder_id": folder["id"]}).json()
    assert [n["id"] for n in in_folder] == [note["id"]]
    assert client.get("/api/notes", params={"unfiled": True}).json() == []

    # Deleting a folder must not take the notes with it.
    assert client.delete(f"/api/note-folders/{folder['id']}").json() == {"ok": True}
    assert client.get("/api/notes", params={"unfiled": True}).json()[0]["id"] == note["id"]
    assert child["id"] not in {f["id"] for f in client.get("/api/note-folders").json()}


def test_moving_a_note_out_of_a_folder_needs_the_explicit_flag(client, tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    folder = client.post("/api/note-folders", json={"name": "阅读"}).json()
    note = repo.create_note(project["id"], "读懂 checkpoint", "# 读懂 checkpoint\n", folder_id=folder["id"])

    # A plain title edit must leave the folder alone.
    kept = client.put(f"/api/notes/{note['id']}", json={"title": "读懂 checkpoint 恢复"}).json()
    assert kept["folder_id"] == folder["id"]

    cleared = client.put(f"/api/notes/{note['id']}", json={"move_folder": True}).json()
    assert cleared["folder_id"] is None


def test_unknown_folder_rename_is_404(client, tmp_path, conn_reset):
    _project(tmp_path, "proj")
    assert client.put("/api/note-folders/999999", json={"name": "x"}).status_code == 404


# --------------------------------------------------------------------------- #
# titles name the knowledge point
# --------------------------------------------------------------------------- #
def test_auto_titles_are_recognised_and_real_titles_are_not():
    assert notegen.is_auto_title("关于 README.md 第 357-359 行")
    assert notegen.is_auto_title("README.md:357-359")
    assert notegen.is_auto_title("全项目对话")
    assert notegen.is_auto_title("项目问答")
    assert notegen.is_auto_title("CodeSplit · 阅读笔记")
    assert not notegen.is_auto_title("闭包捕获的是变量而不是值")
    assert notegen.title_from_answer("## 中断恢复靠 checkpoint\n\n正文") == "中断恢复靠 checkpoint"
    assert notegen.title_from_answer("") == ""


class _Answer(BaseChatModel):
    reply: str = "## 要点\n\n正文\n"
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "answer"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN201 - stub
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


@pytest.fixture
def answer_model(monkeypatch):
    def install(reply: str) -> _Answer:
        model = _Answer(reply=reply)
        monkeypatch.setattr(agent_graph.model_config, "build_chat_model", lambda *a, **k: model)
        return model

    return install


def test_note_generation_prioritises_selected_code_and_separates_filename_title(
    tmp_path, conn_reset, answer_model, monkeypatch
):
    project = _project(tmp_path, "proj")
    selected = "def capture(value):\n    return lambda: value\n"
    conversation = repo.create_conversation(
        project["id"],
        "代码阅读",
        mode="snippet",
        file_path="pkg/sample.py",
        start_line=1,
        end_line=2,
        selected_code=selected,
    )
    message = repo.add_message(
        conversation["id"],
        "assistant",
        "选中代码返回一个闭包。联系上下文还展示了项目启动流程。",
    )
    model = answer_model(
        "## 标题\nPython 闭包延迟读取外层变量\n\n"
        "## 文件名\n闭包捕获变量\n\n"
        "## 知识点\n选中的函数返回闭包，闭包在调用时读取外层变量。\n\n"
        "## 要点\n- 返回的是函数对象\n\n## 何时使用\n- 需要延迟读取状态时"
    )
    monkeypatch.setattr(notegen, "_index_note", lambda note: note)

    note = notegen.generate(conversation["id"], message["id"])

    assert note["title"] == "Python 闭包延迟读取外层变量"
    assert note["file_name"] == "闭包捕获变量"
    assert note["content_md"].startswith("# Python 闭包延迟读取外层变量")
    prompt, source = model.calls[-1]
    source_text = str(source.content)
    assert "用户明确选中的代码（本笔记的首要依据）" in source_text
    assert selected in source_text
    assert source_text.index(selected) < source_text.index("联系上下文还展示了项目启动流程")
    assert "不能替代或扩大选中范围" in str(prompt.content)

    exported = notes_export.render(note, project)
    assert exported.startswith("# Python 闭包延迟读取外层变量")


def test_note_filename_can_be_customised_independently(client, tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    response = client.post(
        "/api/notes",
        json={
            "project_id": project["id"],
            "title": "闭包延迟读取外层变量",
            "file_name": "Python 闭包小记",
            "content_md": "# 闭包延迟读取外层变量\n\n正文。",
        },
    )
    assert response.status_code == 200
    note = response.json()
    assert note["title"] == "闭包延迟读取外层变量"
    assert note["file_name"] == "Python 闭包小记"

    updated = client.put(f"/api/notes/{note['id']}", json={"file_name": "闭包机制"}).json()
    assert updated["title"] == "闭包延迟读取外层变量"
    assert updated["file_name"] == "闭包机制"

    exported = notes_export.render(
        {**updated, "title": "侧栏标题", "file_name": "闭包机制", "content_md": "# 正文标题\n\n正文。"},
        project,
    )
    assert exported.startswith("# 正文标题")


def test_note_keyword_sync_does_not_wait_for_embedding(client, project, monkeypatch):
    import threading

    from app.core.db import get_conn
    from app.services import notes_index

    entered = threading.Event()
    release = threading.Event()

    def slow_embedding(text):
        entered.set()
        release.wait(timeout=5)
        return None, None

    monkeypatch.setattr(notes_index, "_embed", slow_embedding)
    note = repo.create_note(project["id"], "异步向量笔记", "# 异步向量笔记\n\n立即可搜索。")
    notes_index.sync_note_background(note)

    assert entered.wait(timeout=1)
    with get_conn() as conn:
        indexed = conn.execute("SELECT content FROM chunks WHERE path = ?", (f"note://{note['id']}",)).fetchone()
    assert indexed is not None and "立即可搜索" in indexed["content"]
    release.set()


def test_placeholder_conversation_title_becomes_the_knowledge_point(tmp_path, conn_reset, answer_model):
    project = _project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "关于 sample.py 第 1-2 行", mode="snippet", file_path="pkg/sample.py", start_line=1, end_line=2
    )
    answer_model("# 模块入口只有一个顶层函数\n\n它返回常量 1。")

    list(agent_graph.start_turn(conversation["id"], "这个文件里有什么"))

    assert repo.get_conversation(conversation["id"])["title"] == "模块入口只有一个顶层函数"


def test_conversation_title_prompt_uses_all_user_questions(tmp_path, conn_reset, answer_model):
    project = _project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "项目问答", mode="project")
    repo.add_message(conversation["id"], "user", "Agent 如何暂停等待授权？")
    repo.add_message(conversation["id"], "assistant", "LangGraph 将 interrupt 写入 checkpoint。")
    repo.add_message(conversation["id"], "user", "恢复时怎样避免重复执行写文件？")
    model = answer_model("LangGraph 授权中断与副作用恢复")

    title = notegen.title_for_conversation(conversation["id"])

    assert title == "LangGraph 授权中断与副作用恢复"
    prompt, source = model.calls[-1]
    assert "只描述对话主题，不是笔记标题" in str(prompt.content)
    assert "Agent 如何暂停等待授权？" in str(source.content)
    assert "恢复时怎样避免重复执行写文件？" in str(source.content)
    assert "只描述对话主题，不是笔记标题" in str(prompt.content)


def test_a_title_the_user_wrote_is_never_overwritten(tmp_path, conn_reset, answer_model):
    project = _project(tmp_path, "proj")
    conversation = repo.create_conversation(
        project["id"], "我自己的标题", mode="snippet", file_path="pkg/sample.py", start_line=1, end_line=2
    )
    answer_model("# 模型想改标题\n\n正文")

    list(agent_graph.start_turn(conversation["id"], "看看"))

    assert repo.get_conversation(conversation["id"])["title"] == "我自己的标题"


# --------------------------------------------------------------------------- #
# abandoned conversations
# --------------------------------------------------------------------------- #
def test_untouched_conversations_are_purged_but_fresh_ones_are_kept(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    stale = repo.create_conversation(project["id"], "全项目对话", mode="project")
    fresh = repo.create_conversation(project["id"], "全项目对话", mode="project")
    used = repo.create_conversation(project["id"], "真的聊过", mode="project")
    repo.add_message(used["id"], "user", "你好")

    with repo.write_conn() as conn:
        conn.execute("UPDATE conversations SET updated_at = '2020-01-01T00:00:00Z' WHERE id = ?", (stale["id"],))

    assert repo.purge_empty_conversations(older_than_minutes=60) == 1
    left = {c["id"] for c in repo.list_conversations(project["id"])}
    assert stale["id"] not in left
    assert fresh["id"] in left and used["id"] in left


def test_the_conversation_being_typed_into_is_still_listed(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    drafted = repo.create_conversation(project["id"], "全项目对话", mode="project")
    # The sidebar has to show it, otherwise "新建对话" looks broken until send.
    assert [c["id"] for c in repo.list_conversations(project["id"])] == [drafted["id"]]


# --------------------------------------------------------------------------- #
# title backfill for conversations that never see another turn
# --------------------------------------------------------------------------- #
def test_old_placeholders_are_retitled_from_their_last_answer(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    older = repo.create_conversation(
        project["id"],
        "关于 README.md 第 357-359 行",
        mode="snippet",
        file_path="README.md",
        start_line=357,
        end_line=359,
    )
    repo.add_message(older["id"], "user", "这段在讲什么")
    repo.add_message(older["id"], "assistant", "## 中断恢复靠 checkpoint\n\n旧的一轮")
    repo.add_message(older["id"], "user", "再说说")
    repo.add_message(older["id"], "assistant", "恢复时从 checkpoint 重放状态")

    assert notegen.backfill_conversation_titles() == 1
    assert repo.get_conversation(older["id"])["title"] == "恢复时从 checkpoint 重放状态"


def test_backfill_leaves_user_titles_and_untouched_conversations_alone(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    named = repo.create_conversation(project["id"], "我自己的标题", mode="project")
    repo.add_message(named["id"], "assistant", "随便聊聊")
    blank = repo.create_conversation(project["id"], "全项目对话", mode="project")

    assert notegen.backfill_conversation_titles() == 0
    assert repo.get_conversation(named["id"])["title"] == "我自己的标题"
    assert repo.get_conversation(blank["id"])["title"] == "全项目对话"


def test_backfill_falls_back_to_the_question_when_no_answer_was_written(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    unanswered = repo.create_conversation(project["id"], "全项目对话", mode="project")
    repo.add_message(unanswered["id"], "user", "解释一下 Agent 的审批流程是怎么实现的")

    # A turn that never produced an answer still says what the session is about.
    assert notegen.backfill_conversation_titles() == 1
    assert repo.get_conversation(unanswered["id"])["title"] == "解释一下 Agent 的审批流程是怎么实现的"


def test_backfill_ignores_the_apology_the_graph_saves_on_empty_output(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    empty = repo.create_conversation(project["id"], "全项目对话", mode="project")
    repo.add_message(empty["id"], "assistant", "抱歉，这一轮没有生成有效内容，请重试")

    assert notegen.backfill_conversation_titles() == 0
    assert repo.get_conversation(empty["id"])["title"] == "全项目对话"


def test_backfill_skips_first_person_narration_and_reads_on(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "全项目对话", mode="project")
    repo.add_message(
        conversation["id"],
        "assistant",
        "我已经把笔记的完整链路摸清楚了。现在可以给你一个准确的回答\n\n## 笔记落库靠 code_anchors 关联\n",
    )

    assert notegen.backfill_conversation_titles() == 1
    assert repo.get_conversation(conversation["id"])["title"] == "笔记落库靠 code_anchors 关联"


def test_backfill_keeps_conversation_title_independent_from_its_note(tmp_path, conn_reset):
    project = _project(tmp_path, "proj")
    conversation = repo.create_conversation(project["id"], "关于 README.md 第 357-359 行", mode="project")
    repo.add_message(conversation["id"], "assistant", "LangGraph 在恢复前检查 checkpoint，并从中断节点继续执行。")
    repo.create_note(project["id"], "中断后在 checkpoint 上恢复", "# 中断后在 checkpoint 上恢复\n", conversation["id"])

    assert notegen.backfill_conversation_titles() == 1
    title = repo.get_conversation(conversation["id"])["title"]
    assert "LangGraph" in title
    assert title != "中断后在 checkpoint 上恢复"
