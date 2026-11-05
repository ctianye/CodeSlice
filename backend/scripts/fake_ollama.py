"""A stand-in Ollama daemon for UI work when the real one is not installed.

Serves just enough of the API for the settings panel:

* ``GET  /api/version``   -> a version string (the card flow lights up)
* ``GET  /api/tags``      -> whatever has been "pulled" so far
* ``POST /api/pull``      -> NDJSON progress at 10% steps, then ``success``
* ``POST /v1/embeddings`` -> deterministic bag-of-words vectors

Usage:

    .venv\\Scripts\\python.exe scripts\\fake_ollama.py --port 11500

Then start the backend with ``CODESLICE_OLLAMA_URL=http://127.0.0.1:11500``.
This is a development aid only; the packaged app talks to the real daemon at
``http://127.0.0.1:11434``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.5.1-fake"
EMBED_DIMS = 64


class FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    installed_models: list[str] = []

    def log_message(self, fmt: str, *args) -> None:
        print(f"[fake-ollama] {fmt % args}", flush=True)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/version":
            self._json({"version": VERSION})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": n, "model": n} for n in type(self).installed_models]})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}

        if self.path == "/v1/embeddings":
            self._embeddings(payload)
        elif self.path == "/api/pull":
            self._pull(payload)
        else:
            self._json({"error": "not found"}, status=404)

    def _embeddings(self, payload: dict) -> None:
        raw_inputs = payload.get("input")
        texts = raw_inputs if isinstance(raw_inputs, list) else [str(raw_inputs or "")]
        data = []
        for index, text in enumerate(texts):
            vec = [0.0] * EMBED_DIMS
            for token in str(text).lower().replace("\n", " ").split():
                vec[abs(hash(token)) % EMBED_DIMS] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            data.append({"object": "embedding", "index": index, "embedding": [v / norm for v in vec]})
        self._json({"object": "list", "model": str(payload.get("model") or "bge-m3"), "data": data})

    def _pull(self, payload: dict) -> None:
        model = str(payload.get("model") or "")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        total = 1_200_000_000

        def emit(frame: dict) -> None:
            chunk = (json.dumps(frame) + "\n").encode()
            self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()

        emit({"status": "pulling manifest"})
        for percent in range(0, 101, 10):
            emit({"status": "downloading", "total": total, "completed": int(total * percent / 100)})
            time.sleep(0.35)
        emit({"status": "verifying sha256 digest"})
        emit({"status": "success"})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        if model and model not in type(self).installed_models:
            type(self).installed_models.append(model)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Fake Ollama daemon for UI checks")
    parser.add_argument("--port", type=int, default=11500)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FakeOllamaHandler)
    server.daemon_threads = True
    print(f"[fake-ollama] listening on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
