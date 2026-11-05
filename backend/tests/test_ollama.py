"""Ollama preset/probe/pull behaviour.

The tests spin up a real (tiny) HTTP server on loopback so ``httpx`` talks to it
exactly as it would talk to Ollama. That keeps the streaming-parser, the timeout
handling and the error paths honest — a mocked client would not.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.services import ollama

TEST_TOKEN = "test-token"  # noqa: F841  (imported for readability of the client fixture)


class _Handler(BaseHTTPRequestHandler):
    # filled in by the fixture
    version_payload: dict[str, Any] | None = None
    tags_payload: dict[str, Any] | None = None
    pull_lines: list[dict[str, Any]] = []
    pull_status = 200
    last_pull_body: dict[str, Any] | None = None

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/api/version":
            if self.version_payload is None:
                self._json({"error": "no"}, status=500)
            else:
                self._json(self.version_payload)
        elif self.path == "/api/tags":
            self._json(self.tags_payload or {"models": []})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != "/api/pull":
            self._json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        type(self).last_pull_body = json.loads(raw or b"{}")
        if type(self).pull_status >= 400:
            self._json({"error": "boom"}, status=type(self).pull_status)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for line in type(self).pull_lines:
            self.wfile.write((json.dumps(line) + "\n").encode())
            self.wfile.flush()


@pytest.fixture
def fake_ollama(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Handler]]:
    _Handler.version_payload = {"version": "0.5.1"}
    _Handler.tags_payload = {"models": [{"name": "llama3:latest", "model": "llama3:latest"}]}
    _Handler.pull_lines = [
        {"status": "pulling manifest"},
        {"status": "downloading", "total": 1000, "completed": 0},
        {"status": "downloading", "total": 1000, "completed": 500},
        {"status": "downloading", "total": 1000, "completed": 1000},
        {"status": "verifying sha256 digest"},
        {"status": "success"},
    ]
    _Handler.pull_status = 200
    _Handler.last_pull_body = None

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    monkeypatch.setenv("CODESLICE_OLLAMA_URL", f"http://127.0.0.1:{port}")
    try:
        yield _Handler
    finally:
        server.shutdown()
        server.server_close()


def test_presets_are_the_two_documented_models() -> None:
    models = sorted(ollama.PRESET_MODELS)
    assert models == ["bge-m3", "qwen3-embedding:0.6b"]
    for preset in ollama.PRESETS:
        assert preset["context"] and preset["size"] and preset["description"]


def test_openai_base_url_points_at_the_v1_prefix() -> None:
    assert ollama.openai_base_url().endswith("/v1")
    assert ollama.base_url().startswith("http://")


def test_status_reports_a_running_daemon(fake_ollama: type[_Handler]) -> None:
    result = ollama.status()
    assert result["installed"] is True
    assert result["version"] == "0.5.1"
    assert "llama3:latest" in result["models"]
    assert result["error"] is None
    assert len(result["presets"]) == 2


def test_status_reports_absence_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing listens on this port: the probe must degrade, not explode.
    monkeypatch.setenv("CODESLICE_OLLAMA_URL", "http://127.0.0.1:9")
    result = ollama.status()
    assert result["installed"] is False
    assert result["version"] is None
    assert result["models"] == []
    assert result["error"]


def test_status_includes_base_url_for_the_ui(fake_ollama: type[_Handler]) -> None:
    result = ollama.status()
    assert result["base_url"] == ollama.base_url()
    assert result["openai_base_url"] == f"{ollama.base_url()}/v1"


def test_is_installed_accepts_the_latest_tag() -> None:
    assert ollama.is_installed("bge-m3", ["bge-m3:latest"]) is True
    assert ollama.is_installed("bge-m3", ["bge-m3"]) is True
    assert ollama.is_installed("bge-m3", ["qwen3-embedding:0.6b"]) is False


def test_pull_streams_progress_and_terminates_with_done(fake_ollama: type[_Handler]) -> None:
    events = list(ollama.pull("bge-m3"))
    kinds = [event["type"] for event in events]
    assert kinds[0] == "progress"
    assert kinds[-1] == "done"
    assert "error" not in kinds
    assert fake_ollama.last_pull_body == {"model": "bge-m3", "stream": True}

    progress = [event for event in events if event["type"] == "progress"]
    # Ollama keeps sending non-byte statuses ("verifying sha256 digest") after the
    # last chunk, so assert the peak rather than the tail.
    assert max(event["percent"] for event in progress) == 100.0
    assert any(event["status"] == "success" for event in progress)
    assert any(event["percent"] == 50.0 for event in progress)


def test_pull_rejects_an_unlisted_model_without_calling_the_daemon(
    fake_ollama: type[_Handler],
) -> None:
    events = list(ollama.pull("totally-unknown:70b"))
    assert events[0]["type"] == "error"
    assert events[-1]["type"] == "done"
    assert fake_ollama.last_pull_body is None  # never reached the daemon


def test_pull_surfaces_daemon_errors(fake_ollama: type[_Handler]) -> None:
    fake_ollama.pull_status = 500
    events = list(ollama.pull("bge-m3"))
    assert events[0]["type"] == "error"
    assert "500" in events[0]["detail"]
    assert events[-1]["type"] == "done"


def test_pull_reports_an_error_line_from_the_stream(fake_ollama: type[_Handler]) -> None:
    fake_ollama.pull_lines = [{"status": "downloading", "total": 10, "completed": 1}, {"error": "disk full"}]
    events = list(ollama.pull("bge-m3"))
    errors = [event for event in events if event["type"] == "error"]
    assert errors and "disk full" in errors[0]["detail"]
    assert events[-1]["type"] == "done"


def test_pull_reports_an_unreachable_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODESLICE_OLLAMA_URL", "http://127.0.0.1:9")
    events = list(ollama.pull("bge-m3"))
    assert events[0]["type"] == "error"
    assert "Ollama" in events[0]["detail"]
    assert events[-1]["type"] == "done"
