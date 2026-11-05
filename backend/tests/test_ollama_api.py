"""HTTP-level smoke tests for the Ollama settings endpoints.

Covers the contract the frontend relies on: auth, the status shape, the preset
guard on pull, and the SSE framing (``data: {...}\\n\\n``) that ``api.ts`` parses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.services import ollama


def _parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in text.replace("\r\n", "\n").split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


@pytest.fixture(autouse=True)
def _fresh_model_settings():
    """Isolate model settings between tests.

    ``settings`` is a plain key/value row and is *not* wiped by ``conn_reset``,
    so a test that configures an embedding model would otherwise leak into
    every later test in the session (this bit us: an index-status assertion saw
    the model configured by test_api.py's roundtrip test).
    """
    from app.models import config as model_config

    model_config.set_setting(model_config.SETTINGS_KEY, {})
    model_config.delete_api_key("embedding")
    yield
    model_config.set_setting(model_config.SETTINGS_KEY, {})
    model_config.delete_api_key("embedding")


def test_ollama_status_requires_a_token(client) -> None:
    response = client.get("/api/settings/ollama/status", headers={"X-Session-Token": "nope"})
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid session token"


def test_ollama_status_is_200_even_when_absent(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODESLICE_OLLAMA_URL", "http://127.0.0.1:9")
    response = client.get("/api/settings/ollama/status")
    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is False
    assert body["models"] == []
    assert [p["model"] for p in body["presets"]] == ["bge-m3", "qwen3-embedding:0.6b"]


def test_ollama_pull_rejects_unknown_models_before_streaming(client) -> None:
    response = client.post("/api/settings/ollama/pull", json={"model": "llama3:70b"})
    assert response.status_code == 400
    assert "unsupported model" in response.json()["detail"]


def test_ollama_pull_rejects_an_empty_model(client) -> None:
    response = client.post("/api/settings/ollama/pull", json={"model": ""})
    assert response.status_code == 422


def test_ollama_pull_streams_sse_frames(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_pull(model: str):
        yield {"type": "progress", "model": model, "status": "downloading", "percent": 40.0}
        yield {"type": "progress", "model": model, "status": "downloading", "percent": 100.0}
        yield {"type": "done", "model": model}

    monkeypatch.setattr(ollama, "pull", fake_pull)
    response = client.post("/api/settings/ollama/pull", json={"model": "bge-m3"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(response.text)
    assert [e["type"] for e in events] == ["progress", "progress", "done"]
    assert events[1]["percent"] == 100.0


def test_ollama_pull_terminates_the_stream_on_an_unexpected_exception(client, monkeypatch: pytest.MonkeyPatch) -> None:
    def exploding_pull(model: str):
        yield {"type": "progress", "model": model, "percent": 10.0}
        raise RuntimeError("kaboom")

    monkeypatch.setattr(ollama, "pull", exploding_pull)
    response = client.post("/api/settings/ollama/pull", json={"model": "bge-m3"})
    events = _parse_sse(response.text)
    assert events[0]["type"] == "progress"
    assert events[1]["type"] == "error"
    assert "kaboom" in events[1]["detail"]
    assert events[-1]["type"] == "done"


def test_project_index_reports_the_active_embed_model(client, project) -> None:
    response = client.get(f"/api/projects/{project['id']}/index")
    assert response.status_code == 200
    body = response.json()
    assert "embed_models" in body
    assert body["embed_models"] == []
    assert body["active_embed_model"] is None
    assert body["embedding_configured"] is False


def test_using_a_preset_updates_the_embedding_config(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """The flow the UI drives: pick a card, PUT the config, status flips to ready."""
    monkeypatch.setenv("CODESLICE_OLLAMA_URL", "http://127.0.0.1:11434")
    payload = {
        "embedding": {
            "model": "bge-m3",
            "base_url": ollama.openai_base_url(),
            "api_key": "ollama",
        }
    }
    response = client.put("/api/settings/model", json=payload)
    assert response.status_code == 200
    embedding = response.json()["embedding"]
    assert embedding["model"] == "bge-m3"
    assert embedding["base_url"] == "http://127.0.0.1:11434/v1"
    assert embedding["api_key_configured"] is True
    # The placeholder must never be echoed back to the client.
    assert "api_key" not in embedding or not embedding.get("api_key")

    from app.models import embeddings

    assert embeddings.embedding_model_name() == "bge-m3"
    assert embeddings.is_configured() is True

    status_response = client.get("/api/settings/model")
    assert status_response.json()["embedding"]["model"] == "bge-m3"
