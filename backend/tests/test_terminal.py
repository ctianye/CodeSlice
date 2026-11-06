"""Tests for the integrated terminal: sandbox policy, streaming and history."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api import terminal as terminal_api
from app.core.config import settings
from app.core.errors import AppError, InvalidInput
from app.services import sandbox, terminal


@pytest.fixture(autouse=True)
def _clean_history() -> Iterator[None]:
    sandbox.clear_history()
    yield
    sandbox.clear_history()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A minimal app with only the terminal router.

    ``app.main`` does not mount the router online yet, so the HTTP contract is
    exercised against a throwaway app that shares the real dependencies and the
    real ``AppError`` handler.
    """
    app = FastAPI()

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    app.include_router(terminal_api.router, prefix="/api")
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Session-Token": settings.token})
        yield test_client


def sh(code: str) -> str:
    """A shell command that runs ``code`` with the test interpreter."""
    return f'"{sys.executable}" -c "{code}"'


def drain(iterator: Iterator[dict]) -> list[dict]:
    return list(iterator)


def frames(text: str) -> list[dict]:
    """Parse an SSE body into event dicts."""
    out: list[dict] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


# --------------------------------------------------------------------------- #
# sandbox: command policy
# --------------------------------------------------------------------------- #
def test_check_command_allows_normal_commands():
    assert sandbox.check_command("pytest -q") is None
    assert sandbox.check_command("git status --short") is None


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "format C:",
        "shutdown /s",
        "REBOOT",
        "diskpart",
        "del /f /s /q c:\\windows",
        "rd /s /q c:\\",
        "mkfs.ext4 /dev/sda",
        ":(){ :|:& };:",
    ],
)
def test_check_command_rejects_dangerous(command: str):
    assert sandbox.is_blocked(command) is True
    with pytest.raises(InvalidInput) as excinfo:
        sandbox.check_command(command)
    assert "黑名单" in str(excinfo.value)


def test_check_command_rejects_empty():
    with pytest.raises(InvalidInput) as excinfo:
        sandbox.check_command("   ")
    assert "不能为空" in str(excinfo.value)


def test_blocked_fragments_shared_with_agent_tools():
    from app.agent import tools

    assert tools._BLOCKED_COMMAND_FRAGMENTS is sandbox.BLOCKED_FRAGMENTS
    assert tools._is_blocked("rm -rf /") is True


# --------------------------------------------------------------------------- #
# sandbox: cwd resolution
# --------------------------------------------------------------------------- #
def test_resolve_cwd_defaults_to_root(project: dict):
    root = Path(project["path"])
    assert sandbox.resolve_cwd(root, None) == root
    assert sandbox.resolve_cwd(root, "  ") == root


def test_resolve_cwd_accepts_subdirectory(project: dict):
    root = Path(project["path"])
    (root / "backend").mkdir()
    assert sandbox.resolve_cwd(root, "backend") == root / "backend"


def test_resolve_cwd_rejects_escape(project: dict):
    root = Path(project["path"])
    with pytest.raises(InvalidInput):
        sandbox.resolve_cwd(root, "../outside")


def test_resolve_cwd_rejects_missing_directory(project: dict):
    root = Path(project["path"])
    with pytest.raises(InvalidInput) as excinfo:
        sandbox.resolve_cwd(root, "nope")
    assert "工作目录不存在" in str(excinfo.value)


def test_cwd_label(project: dict):
    root = Path(project["path"])
    assert sandbox.cwd_label(root, root) == "."
    (root / "backend").mkdir()
    assert sandbox.cwd_label(root, root / "backend") == "backend"


# --------------------------------------------------------------------------- #
# sandbox: tail formatting and history
# --------------------------------------------------------------------------- #
def test_format_tail_keeps_last_lines():
    text = "\n".join(f"line{i}" for i in range(20))
    tail = sandbox.format_tail(text, lines=3)
    assert tail == "line17\nline18\nline19"


def test_format_tail_clips_long_text():
    tail = sandbox.format_tail("x" * 9000, lines=6)
    assert len(tail) <= sandbox.OUTPUT_TAIL_CHARS


def test_format_tail_handles_blank_input():
    assert sandbox.format_tail("") == ""
    assert sandbox.format_tail("\n\n") == ""


def test_history_is_newest_first_and_scoped(project: dict):
    sandbox.record(1, "first", ".", "user", 0, 10, "a")
    sandbox.record(2, "other", ".", "user", 0, 10, "b")
    sandbox.record(1, "second", "backend", "agent", 1, 20, "c")

    items = sandbox.history(1)
    assert [item["command"] for item in items] == ["second", "first"]
    assert all("project_id" not in item for item in items)
    assert items[0]["source"] == "agent"
    assert items[0]["cwd"] == "backend"
    assert items[0]["exit_code"] == 1
    assert sandbox.history(2)[0]["command"] == "other"


def test_history_respects_limit(project: dict):
    for index in range(5):
        sandbox.record(1, f"cmd{index}", ".", "user", 0, 5, "")
    assert len(sandbox.history(1, limit=2)) == 2
    assert sandbox.history(1, limit=2)[0]["command"] == "cmd4"


def test_history_entry_shape(project: dict):
    entry = sandbox.record(project["id"], "git status", None, "user", 0, 1200, "clean")
    assert set(entry) == {
        "id",
        "command",
        "cwd",
        "source",
        "exit_code",
        "started_at",
        "duration_ms",
        "output_tail",
        "project_id",
    }
    assert entry["started_at"].endswith("Z")
    assert entry["cwd"] == "."


# --------------------------------------------------------------------------- #
# streaming runner
# --------------------------------------------------------------------------- #
def test_run_stream_success(project: dict):
    root = Path(project["path"])
    out = drain(terminal.run_stream(root, sh("print('hello')"), project_id=project["id"]))
    assert out[0]["type"] == "start"
    assert out[0]["command"] == sh("print('hello')")
    assert out[0]["cwd"] == "."
    texts = [f["text"] for f in out if f["type"] == "output"]
    assert texts == ["hello"]
    exit_frame = out[-1]
    assert exit_frame["type"] == "exit"
    assert exit_frame["code"] == 0
    assert exit_frame["truncated"] is False
    assert exit_frame["duration_ms"] >= 0


def test_run_stream_non_zero_exit(project: dict):
    root = Path(project["path"])
    out = drain(terminal.run_stream(root, sh("raise SystemExit(3)"), project_id=project["id"]))
    assert out[-1]["type"] == "exit"
    assert out[-1]["code"] == 3


def test_run_stream_separates_stderr(project: dict):
    root = Path(project["path"])
    code = "import sys; print('boom', file=sys.stderr)"
    out = drain(terminal.run_stream(root, sh(code), project_id=project["id"]))
    stderr = [f for f in out if f["type"] == "output" and f["stream"] == "stderr"]
    assert any("boom" in frame["text"] for frame in stderr)


def test_run_stream_rejects_blocked_command(project: dict):
    root = Path(project["path"])
    out = drain(terminal.run_stream(root, "rm -rf /", project_id=project["id"]))
    assert out[0]["type"] == "start"
    assert out[1]["type"] == "error"
    assert "黑名单" in out[1]["detail"]
    assert all(frame["type"] != "exit" for frame in out)


def test_run_stream_rejects_cwd_escape(project: dict):
    root = Path(project["path"])
    out = drain(terminal.run_stream(root, sh("print(1)"), cwd="../outside", project_id=project["id"]))
    assert out[0]["type"] == "start"
    assert out[1]["type"] == "error"
    assert all(frame["type"] != "exit" for frame in out)


def test_run_stream_emits_while_running(project: dict):
    root = Path(project["path"])
    code = "import time; print('early', flush=True); time.sleep(1.5); print('late', flush=True)"
    started = time.monotonic()
    first_output_at: float | None = None
    for frame in terminal.run_stream(root, sh(code), project_id=project["id"]):
        if frame["type"] == "output" and first_output_at is None:
            first_output_at = time.monotonic() - started
    assert first_output_at is not None
    assert first_output_at < 1.0


def test_run_stream_truncates_large_output(project: dict, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(terminal, "MAX_OUTPUT_BYTES", 4096)
    root = Path(project["path"])
    flood = "[print('x' * 200) for _ in range(100)]"
    out = drain(terminal.run_stream(root, sh(flood), project_id=project["id"]))
    exit_frame = next(frame for frame in out if frame["type"] == "exit")
    assert exit_frame["truncated"] is True
    emitted = sum(len(frame["text"]) + 1 for frame in out if frame["type"] == "output")
    assert emitted <= 4096


def test_run_stream_records_history(project: dict):
    root = Path(project["path"])
    drain(terminal.run_stream(root, sh("print('logged')"), project_id=project["id"]))
    items = sandbox.history(project["id"])
    assert len(items) == 1
    assert items[0]["source"] == "user"
    assert items[0]["exit_code"] == 0
    assert "logged" in items[0]["output_tail"]


def test_run_stream_timeout_kills(project: dict):
    root = Path(project["path"])
    out = drain(terminal.run_stream(root, sh("import time; time.sleep(20)"), timeout=1, project_id=project["id"]))
    assert out[-1]["type"] == "exit"
    assert any(frame["type"] == "error" and "超时" in frame["detail"] for frame in out)


def test_cancel_with_nothing_running(project: dict):
    assert terminal.cancel(project["id"]) == 0
    assert terminal.has_running(project["id"]) is False


# --------------------------------------------------------------------------- #
# HTTP contract
# --------------------------------------------------------------------------- #
def test_http_run_streams_frames(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/terminal/run",
        json={"command": sh("print('hi')")},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = frames(response.text)
    assert events[0]["type"] == "start"
    assert events[0]["cwd"] == "."
    assert any(event["type"] == "output" and event["text"] == "hi" for event in events)
    assert events[-2]["type"] == "exit"
    assert events[-2]["code"] == 0
    assert events[-1] == {"type": "done"}
    assert sum(1 for event in events if event["type"] == "done") == 1


def test_http_run_blocked_command_still_done(client: TestClient, project: dict):
    response = client.post(f"/api/projects/{project['id']}/terminal/run", json={"command": "shutdown /s"})
    assert response.status_code == 200
    events = frames(response.text)
    assert [event["type"] for event in events] == ["start", "error", "done"]
    assert "黑名单" in events[1]["detail"]


def test_http_run_bad_cwd(client: TestClient, project: dict):
    response = client.post(f"/api/projects/{project['id']}/terminal/run", json={"command": "pwd", "cwd": "../.."})
    events = frames(response.text)
    assert events[0]["type"] == "start"
    assert events[1]["type"] == "error"
    assert events[-1]["type"] == "done"


def test_http_run_rejects_empty_command(client: TestClient, project: dict):
    response = client.post(f"/api/projects/{project['id']}/terminal/run", json={"command": ""})
    assert response.status_code == 422


def test_http_run_unknown_project(client: TestClient):
    response = client.post("/api/projects/999999/terminal/run", json={"command": "pwd"})
    assert response.status_code == 404


def test_http_requires_token(client: TestClient, project: dict):
    response = client.post(
        f"/api/projects/{project['id']}/terminal/run",
        json={"command": "pwd"},
        headers={"X-Session-Token": "wrong"},
    )
    assert response.status_code == 401


def test_http_history(client: TestClient, project: dict):
    for index in range(3):
        client.post(f"/api/projects/{project['id']}/terminal/run", json={"command": sh(f"print({index})")})

    response = client.get(f"/api/projects/{project['id']}/terminal/history")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3
    assert items[0]["exit_code"] == 0
    assert set(items[0]) == {
        "id",
        "command",
        "cwd",
        "source",
        "exit_code",
        "started_at",
        "duration_ms",
        "output_tail",
    }

    limited = client.get(f"/api/projects/{project['id']}/terminal/history?limit=1").json()["items"]
    assert len(limited) == 1


def test_http_history_validates_limit(client: TestClient, project: dict):
    assert client.get(f"/api/projects/{project['id']}/terminal/history?limit=0").status_code == 422


def test_http_cancel_nothing_running(client: TestClient, project: dict):
    response = client.post(f"/api/projects/{project['id']}/terminal/cancel")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "cancelled": 0}
