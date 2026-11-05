"""End-to-end check of the Ollama vector-model flow over real HTTP.

The machine this was developed on has no Ollama installed, so acceptance step
"download bge-m3 from 0% to 100%" cannot be exercised against the real daemon.
This script stands up a **fake Ollama daemon** on a free loopback port, points
the backend at it with ``CODESLICE_OLLAMA_URL``, boots the real ASGI app on
another port, and drives the whole UI flow with httpx:

    status (installed, two presets) -> pull (SSE progress 0..100, done)
    -> PUT /api/settings/model (preset -> embedding config)
    -> models/embedding state -> project index embedding status
    -> force re-index with the vector model configured

Run it from ``backend/``:

    .venv\\Scripts\\python.exe scripts\\check_ollama.py

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------------------------------------------------------------------
# fake Ollama daemon
# ---------------------------------------------------------------------------

FAKE_VERSION = "0.5.1-fake"
FAKE_MODEL = "bge-m3"
STAIRS = [0, 12, 25, 37, 50, 62, 75, 87, 100]


class _FakeOllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    installed_models: list[str] = []
    pull_requests: list[dict] = []

    def log_message(self, *_args) -> None:  # keep the console readable
        pass

    def _embeddings(self, payload: dict) -> None:
        """Deterministic bag-of-words vectors so the vector path is exercised.

        A real Ollama returns semantic vectors; here we only need *stable*
        vectors that put texts sharing words closer together, which is enough
        to prove the index writes ``embed_model`` and that hybrid search can
        reach the vector retriever.
        """
        raw_inputs = payload.get("input")
        texts = raw_inputs if isinstance(raw_inputs, list) else [str(raw_inputs or "")]
        dims = 64
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * dims
            for token in str(text).lower().replace("\n", " ").split():
                vec[hash(token) % dims] += 1.0
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            vectors.append([v / norm for v in vec])
        self._json(
            {
                "object": "list",
                "model": str(payload.get("model") or FAKE_MODEL),
                "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
                "usage": {"prompt_tokens": 0, "total_tokens": 0},
            }
        )

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/api/version":
            self._json({"version": FAKE_VERSION})
        elif self.path == "/api/tags":
            self._json({"models": [{"name": name, "model": name} for name in self.installed_models]})
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        if self.path == "/v1/embeddings":
            self._embeddings(payload)
            return
        if self.path != "/api/pull":
            self._json({"error": "not found"}, status=404)
            return

        type(self).pull_requests.append(payload)
        model = str(payload.get("model") or "")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        total = 1_200_000_000
        frames: list[dict] = [{"status": "pulling manifest"}]
        for percent in STAIRS:
            frames.append(
                {
                    "status": "downloading",
                    "total": total,
                    "completed": int(total * percent / 100),
                }
            )
        frames.append({"status": "verifying sha256 digest"})  # no total: progress must not reset
        frames.append({"status": "success"})

        def send_chunk(payload_bytes: bytes) -> None:
            self.wfile.write(f"{len(payload_bytes):X}\r\n".encode())
            self.wfile.write(payload_bytes + b"\r\n")
            self.wfile.flush()

        for frame in frames:
            send_chunk((json.dumps(frame) + "\n").encode())
            time.sleep(0.01)
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        if model and model not in type(self).installed_models:
            type(self).installed_models.append(model)


def start_fake_ollama() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllamaHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def sse_events(response) -> list[dict]:
    events: list[dict] = []
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return events


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    data_dir = Path(tempfile.mkdtemp(prefix="codeslice-ollama-check-")) / "data"
    os.environ["CODESLICE_DATA_DIR"] = str(data_dir)
    os.environ["CODESLICE_TOKEN"] = "checktoken"
    os.environ["CODESLICE_KEY_SERVICE"] = "codeslice-check-ollama"
    os.environ["CODESLICE_PORT"] = "0"
    os.environ.setdefault("CODESLICE_FRONTEND_DIST", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))

    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    ollama_server, ollama_port = start_fake_ollama()
    os.environ["CODESLICE_OLLAMA_URL"] = f"http://127.0.0.1:{ollama_port}"

    import httpx
    import uvicorn

    from app.main import create_app

    port = free_port()
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)

    base = f"http://127.0.0.1:{port}/api"
    headers = {"X-Session-Token": "checktoken"}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    try:
        with httpx.Client(base_url=base, headers=headers, timeout=300) as client:
            health = client.get("/health")
            check("health", health.status_code == 200, str(health.status_code))

            status = client.get("/settings/ollama/status")
            body = status.json()
            check("status 200", status.status_code == 200, str(status.status_code))
            check("ollama reported installed", body.get("installed") is True, str(body.get("version")))
            check(
                "two presets in order",
                [p["model"] for p in body["presets"]] == ["bge-m3", "qwen3-embedding:0.6b"],
                str([p["model"] for p in body["presets"]]),
            )
            check("no models downloaded yet", body["models"] == [], str(body["models"]))
            check("openai base ends with /v1", str(body["openai_base_url"]).endswith("/v1"), body["openai_base_url"])

            before = client.get("/settings/model").json()
            check("embedding unconfigured at start", before.get("embedding") is None, str(before.get("embedding")))

            with client.stream("POST", "/settings/ollama/pull", json={"model": "bge-m3"}) as response:
                check("pull 200", response.status_code == 200, str(response.status_code))
                check(
                    "pull is event-stream",
                    response.headers.get("content-type", "").startswith("text/event-stream"),
                    response.headers.get("content-type", ""),
                )
                events = sse_events(response)
            kinds = [e.get("type") for e in events]
            check("pull streams progress frames", kinds.count("progress") >= 3, str(len(kinds)))
            check("pull ends with done", kinds[-1] == "done", str(kinds[-3:]))
            percents = [e.get("percent", 0) for e in events if e.get("type") == "progress"]
            check("progress reaches 100%", max(percents) == 100.0, f"max={max(percents)}")
            check("progress is monotonic", percents == sorted(percents), str(percents))
            check(
                "pull asked the daemon for bge-m3",
                _FakeOllamaHandler.pull_requests and _FakeOllamaHandler.pull_requests[-1].get("model") == "bge-m3",
                str(_FakeOllamaHandler.pull_requests[-1:]),
            )

            after_pull = client.get("/settings/ollama/status").json()
            check("status lists the downloaded model", "bge-m3" in after_pull["models"], str(after_pull["models"]))

            rejected = client.post("/settings/ollama/pull", json={"model": "llama3:70b"})
            check("non-preset model rejected", rejected.status_code == 400, str(rejected.status_code))

            # ---- the "use this model" step the UI performs ----
            put = client.put(
                "/settings/model",
                json={
                    "embedding": {
                        "model": "bge-m3",
                        "base_url": after_pull["openai_base_url"],
                        "api_key": "ollama",
                    }
                },
            )
            check("PUT settings 200", put.status_code == 200, str(put.status_code))
            embedding = put.json()["embedding"]
            check("embedding model saved", embedding["model"] == "bge-m3", embedding["model"])
            check(
                "base_url points at ollama",
                embedding["base_url"] == f"http://127.0.0.1:{ollama_port}/v1",
                embedding["base_url"],
            )
            check("placeholder key registered", embedding["api_key_configured"] is True, "api_key_configured")
            check("key never echoed", not embedding.get("api_key"), "leaked!")

            from app.models import embeddings

            check(
                "embedding_model_name",
                embeddings.embedding_model_name() == "bge-m3",
                str(embeddings.embedding_model_name()),
            )
            check("is_configured()", embeddings.is_configured() is True, str(embeddings.is_configured()))

            project = client.post("/projects", json={"path": args.project}).json()
            project_id = project["id"]
            index_status = client.get(f"/projects/{project_id}/index").json()
            check(
                "index status exposes embed_models",
                "embed_models" in index_status,
                str(index_status.get("embed_models")),
            )
            check(
                "active model echoed",
                index_status["active_embed_model"] == "bge-m3",
                str(index_status["active_embed_model"]),
            )
            check(
                "embedding_configured true",
                index_status["embedding_configured"] is True,
                str(index_status["embedding_configured"]),
            )

            job = client.post(f"/projects/{project_id}/index", json={"force": True}).json()
            job_id = job["job_id"]
            final = {}
            for _ in range(600):
                final = client.get(f"/jobs/{job_id}").json()
                if final["status"] in {"done", "error"}:
                    break
                time.sleep(0.2)
            check("reindex finished", final.get("status") == "done", str(final.get("status") or final.get("error")))
            stats = final.get("stats") or {}
            print(f"     reindex stats: {stats}")
            check("reindex embedded vectors", stats.get("vectors", 0) > 0, str(stats.get("vectors")))
            check("no embedding error", not stats.get("embedding_error"), str(stats.get("embedding_error")))

            after_index = client.get(f"/projects/{project_id}/index").json()
            check(
                "index recorded the embedding model",
                "bge-m3" in after_index["embed_models"],
                str(after_index["embed_models"]),
            )

            search = client.get(f"/projects/{project_id}/search", params={"q": "审批流程", "top_k": 3})
            check("search still answers", search.status_code == 200, str(search.status_code))
            hits = search.json()
            check("search returns hits", len(hits) > 0, f"{len(hits)} hits")
            retrievers = [h.get("retriever") for h in hits]
            print(f"     retrievers: {retrievers}")
            check("hybrid search uses vectors", any(r in {"vector", "hybrid"} for r in retrievers), str(retrievers))
    finally:
        server.should_exit = True
        ollama_server.shutdown()
        ollama_server.server_close()

    print()
    if failures:
        print(f"FAILURES {len(failures)}: {failures}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
