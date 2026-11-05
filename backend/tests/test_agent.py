"""Agent graph behaviour with a scripted fake model (no network, no API key).

Covers the parts that are easy to break: prompt assembly, citation events,
the approval interrupt, resume decisions and command-rule memory.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool

from app.agent import graph as agent_graph
from app.storage import repo


class ScriptedModel(BaseChatModel):
    """Yields a fixed sequence of messages, streaming the text of each one."""

    script: list[AIMessage]
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: list[BaseTool] | Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if not self.script:
            return AIMessage(content="（脚本已用尽）")
        return self.script.pop(0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        message = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next(messages)
        text = message.content if isinstance(message.content, str) else ""
        tool_chunks = [
            {
                "name": call["name"],
                "args": __import__("json").dumps(call.get("args") or {}, ensure_ascii=False),
                "id": call.get("id") or f"call_{i}",
                "index": i,
            }
            for i, call in enumerate(message.tool_calls)
        ]
        if text:
            for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
                if piece:
                    yield ChatGenerationChunk(message=AIMessageChunk(content=piece))
        if tool_chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=tool_chunks))


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted model and return a setter for the next replies."""

    def install(messages: list[AIMessage]) -> ScriptedModel:
        model = ScriptedModel(script=list(messages))
        monkeypatch.setattr(agent_graph.model_config, "build_chat_model", lambda: model)
        return model

    return install


def _drain(conversation_id: str, text: str) -> list[dict[str, Any]]:
    return list(agent_graph.start_turn(conversation_id, text))


def _resume(conversation_id: str, interrupt_id: str, decision: str, **kw) -> list[dict[str, Any]]:
    return list(agent_graph.resume_turn(conversation_id, interrupt_id, decision, **kw))


def test_plain_answer_streams_tokens_and_persists(project, conversation, scripted):
    scripted([AIMessage(content="这是一个解释。")])
    events = _drain(conversation["id"], "解释一下 bar")

    types = [e["type"] for e in events]
    assert "status" in types
    assert "token" in types
    assert types[-1] == "done"
    assert any("这是" in e.get("delta", "") for e in events if e["type"] == "token")

    saved = repo.list_messages(conversation["id"])
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert "解释" in saved[1]["content"]


def test_prompt_carries_selection_and_context(project, conversation, scripted):
    model = scripted([AIMessage(content="ok")])
    _drain(conversation["id"], "这段代码在做什么")
    system = str(model.calls[0][0].content)
    assert "pkg/sample.py" in system
    assert "def bar(self, x):" in system


def test_read_file_tool_emits_citation(project, conversation, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "pkg/sample.py", "start_line": 1, "end_line": 5},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content="文件开头是 docstring。"),
        ]
    )
    events = _drain(conversation["id"], "读一下文件开头")
    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    citations = [e for e in events if e["type"] == "citation"]
    assert any(c["path"] == "pkg/sample.py" for c in citations)


def test_dangerous_tool_interrupts_then_executes_on_approve(project, conversation, scripted, monkeypatch):
    executed: list[str] = []
    real_run = agent_graph.agent_tools.run_tool

    def spy(name, args, root, project_id):
        executed.append(name)
        return real_run(name, args, root, project_id)

    monkeypatch.setattr(agent_graph.agent_tools, "run_tool", spy)

    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/gen.py", "content": "x = 1\n", "reason": "生成示例"},
                        "id": "call_w",
                    }
                ],
            ),
            AIMessage(content="已写入。"),
        ]
    )
    events = _drain(conversation["id"], "帮我写个文件")
    assert executed == []  # nothing ran before approval
    approval = next(e for e in events if e["type"] == "approval_required")
    assert approval["kind"] == "write_file"
    assert approval["payload"]["path"] == "pkg/gen.py"

    pending = agent_graph.pending_interrupt(conversation["id"])
    assert pending and pending["id"] == approval["id"]

    resumed = _resume(conversation["id"], approval["id"], "approve")
    assert executed == ["write_file"]
    assert any(e["type"] == "done" for e in resumed)
    assert (__import__("pathlib").Path(project["path"]) / "pkg" / "gen.py").exists()
    assert agent_graph.pending_interrupt(conversation["id"]) is None


def test_deny_skips_side_effect(project, conversation, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/gen.py", "content": "x = 1\n", "reason": "r"},
                        "id": "call_d",
                    }
                ],
            ),
            AIMessage(content="好的，不写了。"),
        ]
    )
    events = _drain(conversation["id"], "写文件")
    approval = next(e for e in events if e["type"] == "approval_required")
    _resume(conversation["id"], approval["id"], "deny")
    assert not (__import__("pathlib").Path(project["path"]) / "pkg" / "gen.py").exists()


def test_approve_always_writes_command_rule(project, conversation, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "run_command",
                        "args": {"command": "echo hi", "cwd": "."},
                        "id": "call_c",
                    }
                ],
            ),
            AIMessage(content="执行完成。"),
        ]
    )
    events = _drain(conversation["id"], "跑个命令")
    approval = next(e for e in events if e["type"] == "approval_required")
    assert approval["kind"] == "run_command"
    _resume(conversation["id"], approval["id"], "approve_always", remember_pattern="echo *")

    rules = repo.list_rules(project["id"])
    assert any(rule["pattern"] == "echo *" and rule["needs_confirm"] is False for rule in rules)


def test_matching_rule_skips_approval(project, conversation, scripted):
    repo.create_rule(project["id"], pattern="echo *", scope="always", needs_confirm=False)
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "run_command", "args": {"command": "echo bye"}, "id": "call_r"}],
            ),
            AIMessage(content="完成。"),
        ]
    )
    events = _drain(conversation["id"], "再跑一次")
    assert not any(e["type"] == "approval_required" for e in events)
    assert any(e["type"] == "tool_call" for e in events)


def test_approve_session_covers_later_calls(project, conversation, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/a.py", "content": "a = 1\n", "reason": "r1"},
                        "id": "c1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/b.py", "content": "b = 2\n", "reason": "r2"},
                        "id": "c2",
                    }
                ],
            ),
            AIMessage(content="两个都写好了。"),
        ]
    )
    events = _drain(conversation["id"], "写两个文件")
    first = next(e for e in events if e["type"] == "approval_required")
    resumed = list(agent_graph.resume_turn(conversation["id"], first["id"], "approve_session"))
    # The second write is auto-approved within the same turn.
    assert not any(e["type"] == "approval_required" for e in resumed)
    root = __import__("pathlib").Path(project["path"])
    assert (root / "pkg" / "a.py").exists() and (root / "pkg" / "b.py").exists()


def test_approve_always_for_write_file_is_path_scoped(project, conversation, scripted):
    """「总是允许」 for a file must not unlock a different file in the same turn."""
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/a.py", "content": "a = 1\n", "reason": "r1"},
                        "id": "a1",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/b.py", "content": "b = 2\n", "reason": "r2"},
                        "id": "b1",
                    }
                ],
            ),
            AIMessage(content="第二个文件需要你再确认。"),
        ]
    )
    events = _drain(conversation["id"], "写两个文件")
    first = next(e for e in events if e["type"] == "approval_required")
    resumed = _resume(conversation["id"], first["id"], "approve_always", remember_pattern="pkg/a.py")
    # pkg/b.py is a different path, so the graph stops for approval again.
    second = next(e for e in resumed if e["type"] == "approval_required")
    assert second["payload"]["path"] == "pkg/b.py"

    root = __import__("pathlib").Path(project["path"])
    assert (root / "pkg" / "a.py").exists()
    assert not (root / "pkg" / "b.py").exists()

    rules = repo.list_rules(project["id"])
    assert any(r["pattern"] == "pkg/a.py" and r["needs_confirm"] is False for r in rules), rules

    # Approving the second one now goes through and the rule set is unchanged.
    final = _resume(conversation["id"], second["id"], "approve")
    assert (root / "pkg" / "b.py").exists()
    assert any(e["type"] == "done" for e in final)


def test_saved_path_rule_skips_approval_next_turn(project, conversation, scripted):
    repo.create_rule(project["id"], pattern="pkg/gen.py", scope="always", needs_confirm=False)
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/gen.py", "content": "z = 3\n", "reason": "r"},
                        "id": "g1",
                    }
                ],
            ),
            AIMessage(content="已写入。"),
        ]
    )
    events = _drain(conversation["id"], "再写一次同样的文件")
    assert not any(e["type"] == "approval_required" for e in events)
    assert (__import__("pathlib").Path(project["path"]) / "pkg" / "gen.py").exists()


def test_model_error_is_reported(project, conversation, monkeypatch):
    class Boom(ScriptedModel):
        def _stream(self, *args, **kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(agent_graph.model_config, "build_chat_model", lambda: Boom(script=[], calls=[]))
    events = _drain(conversation["id"], "hi")
    # The graph keeps the turn alive and finalizes with a fallback message.
    assert any(e["type"] == "done" for e in events)


def test_pending_interrupt_none_for_fresh_thread():
    assert agent_graph.pending_interrupt("no-such-thread") is None
