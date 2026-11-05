"""HTTP-level SSE integration: the exact wire format the UI consumes.

``test_agent.py`` drives the graph directly; this module goes through FastAPI and
parses the ``data: {...}`` frames like ``frontend/src/api.ts`` does, so a
regression in the streaming envelope, the approval round-trip or the
citation→anchor persistence is caught at the boundary the renderer actually uses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.tools import BaseTool

from app.agent import graph as agent_graph
from app.storage import repo


class ScriptedModel(BaseChatModel):
    script: list[AIMessage]
    calls: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "scripted-http"

    def bind_tools(self, tools: list[BaseTool] | Any, **kwargs: Any) -> ScriptedModel:
        return self

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.calls.append(list(messages))
        if not self.script:
            return AIMessage(content="（脚本已用尽）")
        return self.script.pop(0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._next(messages)
        text = message.content if isinstance(message.content, str) else ""
        if text:
            yield ChatGenerationChunk(message=AIMessageChunk(content=text))
        if message.tool_calls:
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": call["name"],
                            "args": json.dumps(call.get("args") or {}, ensure_ascii=False),
                            "id": call.get("id") or f"call_{i}",
                            "index": i,
                        }
                        for i, call in enumerate(message.tool_calls)
                    ],
                )
            )


@pytest.fixture
def scripted(monkeypatch):
    def install(messages: list[AIMessage]) -> ScriptedModel:
        model = ScriptedModel(script=list(messages))
        monkeypatch.setattr(agent_graph.model_config, "build_chat_model", lambda: model)
        return model

    return install


def parse_sse(raw: str) -> list[dict[str, Any]]:
    """Mirror of the parser in frontend/src/api.ts (``data:`` frames only)."""
    events: list[dict[str, Any]] = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            events.append(json.loads(payload))
    return events


@pytest.fixture
def http_conversation(client, project):
    created = client.post(
        "/api/conversations",
        json={
            "project_id": project["id"],
            "title": "sse",
            "mode": "snippet",
            "file_path": "pkg/sample.py",
            "start_line": 9,
            "end_line": 10,
            "selected_code": "    def bar(self, x):\n        return x + 1\n",
        },
    )
    assert created.status_code == 200, created.text
    return created.json()


def test_messages_stream_is_parseable_sse(client, http_conversation, scripted):
    scripted([AIMessage(content="这是解释，见 pkg/sample.py:9-10。")])
    response = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "这段代码干什么"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[0] == "status"
    assert "token" in types
    assert types[-1] == "done"

    tokens = "".join(e["delta"] for e in events if e["type"] == "token")
    assert tokens.strip()

    saved = repo.list_messages(http_conversation["id"])
    assert [m["role"] for m in saved] == ["user", "assistant"]
    assert "pkg/sample.py" in saved[-1]["content"]

    # An assistant message event must carry the same text the tokens spelled out.
    final = next(e for e in events if e["type"] == "message" and e["message"]["role"] == "assistant")
    assert final["message"]["content"].strip() == tokens.strip()


def test_citation_events_become_anchors(client, http_conversation, project, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "pkg/sample.py", "start_line": 1, "end_line": 5},
                        "id": "call_r",
                    }
                ],
            ),
            AIMessage(content="开头是模块 docstring。"),
        ]
    )
    response = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "读开头"},
    )
    events = parse_sse(response.text)
    citations = [e for e in events if e["type"] == "citation"]
    assert citations, [e["type"] for e in events]
    assert all({"path", "start_line", "end_line"} <= set(c) for c in citations)

    anchors = repo.list_anchors(project["id"])
    assert any(a["file_path"] == "pkg/sample.py" for a in anchors), anchors


def test_approval_round_trip_over_http(client, http_conversation, project, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/http_gen.py", "content": "x = 1\n", "reason": "示例"},
                        "id": "call_w",
                    }
                ],
            ),
            AIMessage(content="已写入 pkg/http_gen.py。"),
        ]
    )
    first = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "帮我建个文件"},
    )
    events = parse_sse(first.text)
    approval = next(e for e in events if e["type"] == "approval_required")
    assert approval["payload"]["path"] == "pkg/http_gen.py"
    assert approval["id"]
    assert not (project["path"] and (__import__("pathlib").Path(project["path"]) / "pkg" / "http_gen.py").exists())

    # The UI learns about a pending approval from the conversation GET too.
    detail = client.get(f"/api/conversations/{http_conversation['id']}").json()
    assert detail["pending_interrupt"]["id"] == approval["id"]

    # A second message while the approval is pending must be refused up front.
    blocked = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "再问一句"},
    )
    assert blocked.status_code == 400
    assert "resume" in blocked.json()["detail"]

    resumed = client.post(
        f"/api/conversations/{http_conversation['id']}/resume",
        json={"interrupt_id": approval["id"], "decision": "approve"},
    )
    assert resumed.status_code == 200, resumed.text
    resumed_events = parse_sse(resumed.text)
    assert any(e["type"] == "tool_result" and e["ok"] for e in resumed_events)
    assert resumed_events[-1]["type"] == "done"
    assert (__import__("pathlib").Path(project["path"]) / "pkg" / "http_gen.py").read_text() == "x = 1\n"

    after = client.get(f"/api/conversations/{http_conversation['id']}").json()
    assert after["pending_interrupt"] is None


def test_deny_reports_tool_failure_without_side_effect(client, http_conversation, scripted):
    scripted(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"path": "pkg/denied.py", "content": "y = 2\n", "reason": "r"},
                        "id": "call_d",
                    }
                ],
            ),
            AIMessage(content="好，不写了。"),
        ]
    )
    first = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "写文件"},
    )
    approval = next(e for e in parse_sse(first.text) if e["type"] == "approval_required")

    resumed = client.post(
        f"/api/conversations/{http_conversation['id']}/resume",
        json={"interrupt_id": approval["id"], "decision": "deny"},
    )
    events = parse_sse(resumed.text)
    denied = [e for e in events if e["type"] == "tool_result"]
    assert denied and denied[0]["ok"] is False
    assert client.get(f"/api/conversations/{http_conversation['id']}").json()["pending_interrupt"] is None


def test_model_error_surfaces_as_error_event(client, http_conversation, monkeypatch):
    class Boom(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "boom"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream exploded")

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            raise RuntimeError("upstream exploded")
            yield  # pragma: no cover

    monkeypatch.setattr(agent_graph.model_config, "build_chat_model", lambda: Boom())
    response = client.post(
        f"/api/conversations/{http_conversation['id']}/messages",
        json={"content": "问一下"},
    )
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert any(e["type"] == "error" for e in events)
    assert events[-1]["type"] == "done"
