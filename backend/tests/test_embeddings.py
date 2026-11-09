"""The embedding client must speak the OpenAI-compatible string protocol.

Ollama rejects token-id arrays with ``400 invalid input type``. That failure was
silent at query time (``embed_query`` returns ``None``), so the whole vector
retriever quietly disappeared. This test talks to a real loopback HTTP server
that rejects token ids, which makes the client's wire format observable.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.models import config as model_config


class _EmbeddingHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]] = []

    def log_message(self, *args: Any) -> None:  # keep pytest output clean
        pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        if self.path != "/v1/embeddings":
            self._json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(body)
        inputs = body.get("input")
        values = [inputs] if isinstance(inputs, str) else inputs
        if not isinstance(values, list) or any(isinstance(value, int) for value in values):
            self._json({"error": {"message": "invalid input type"}}, status=400)
            return
        data = [
            {"object": "embedding", "index": index, "embedding": [float(index + 1), 0.5]}
            for index in range(len(values))
        ]
        self._json({"object": "list", "data": data, "model": body.get("model", "")})


@pytest.fixture
def fake_embeddings(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_EmbeddingHandler]]:
    _EmbeddingHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    settings = model_config.ModelSettings(
        chat=model_config.ModelConfig(model="test-chat"),
        embedding=model_config.EmbeddingConfig(
            model="test-embed",
            base_url=f"http://127.0.0.1:{port}/v1",
        ),
    )
    monkeypatch.setattr(model_config, "load_settings", lambda: settings)
    monkeypatch.setattr(model_config, "get_api_key", lambda slot: "test-key")
    try:
        yield _EmbeddingHandler
    finally:
        server.shutdown()
        server.server_close()


def test_embedding_requests_send_text_not_token_ids(fake_embeddings: type[_EmbeddingHandler]) -> None:
    client = model_config.build_embeddings()
    assert client is not None

    query_vector = client.embed_query("测试文本")
    document_vectors = client.embed_documents(["alpha", "beta"])

    assert len(query_vector) == 2
    assert len(document_vectors) == 2
    assert fake_embeddings.requests, "the embedding endpoint was never called"
    for request in fake_embeddings.requests:
        payload = request["input"]
        values = [payload] if isinstance(payload, str) else payload
        assert all(isinstance(value, str) for value in values), request
