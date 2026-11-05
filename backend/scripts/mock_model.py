"""A tiny OpenAI-compatible mock model server.

Lets the whole stack (LangGraph -> ChatOpenAI -> SSE -> renderer) be exercised
without a real API key or network access. It answers every chat completion with
a canned streaming reply that cites a file, so citation chips, the anchor
writer, and the token stream all get real data.

    python scripts/mock_model.py --port 8899
    # then configure: protocol=openai-compatible, base_url=http://127.0.0.1:8899/v1

Not shipped in the app — a test harness only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_REPLY = (
    "这是一个本地模拟模型，用于验证端到端链路。\n\n"
    "关键位置在 `backend/app/main.py:1-40`：那里创建 FastAPI 应用并挂载路由。\n"
    "另外 `backend/app/agent/graph.py:1-30` 定义了 LangGraph 的状态与节点。"
)


def _has_tool_result(payload: dict) -> bool:
    """True once the conversation already contains a tool result.

    Prevents an infinite propose-call loop: first turn proposes the tool, the
    turn after the tool runs gets a plain answer.
    """
    for message in payload.get("messages") or []:
        if message.get("role") == "tool":
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    reply: str = DEFAULT_REPLY
    tool: str = ""
    tool_file: str = ".shots/mock-write.txt"

    def tool_args(self) -> str:
        if self.tool == "write_file":
            args = {
                "path": self.tool_file,
                "content": "由 mock 模型写入，用于验证审批卡片。\n",
                "reason": "验证写入文件的审批界面",
            }
        elif self.tool == "run_command":
            args = {
                "command": "git status --short",
                "reason": "验证命令执行的审批界面",
            }
        else:
            args = {}
        return json.dumps(args, ensure_ascii=False)

    def log_message(self, fmt: str, *args) -> None:  # keep the console quiet
        print(f"[mock] {fmt % args}", flush=True)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(
                200,
                {
                    "object": "list",
                    "data": [{"id": "mock-model", "object": "model", "owned_by": "codeslice"}],
                },
            )
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}

        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._json(404, {"error": {"message": f"unsupported path {self.path}"}})
            return

        stream = bool(payload.get("stream"))
        if not stream:
            self._json(
                200,
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": payload.get("model", "mock-model"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": self.reply},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
                },
            )
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(obj: dict) -> None:
            data = f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()
            self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()

        base = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": payload.get("model", "mock-model"),
        }
        emit({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})
        # Chunk by character groups so the UI shows a real typing effect.
        text = self.reply
        for i in range(0, len(text), 6):
            emit({**base, "choices": [{"index": 0, "delta": {"content": text[i : i + 6]}, "finish_reason": None}]})
            time.sleep(0.012)

        if self.tool and not _has_tool_result(payload):
            # Emit one tool call so the approval UI can be exercised. The shape
            # mirrors what OpenAI-compatible providers stream: a tool_calls delta
            # carrying name + a JSON-encoded argument string.
            call = {
                "index": 0,
                "id": "call_mock_1",
                "type": "function",
                "function": {"name": self.tool, "arguments": self.tool_args()},
            }
            emit({**base, "choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]})
            emit({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            emit({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        done = b"data: [DONE]\n\n"
        self.wfile.write(f"{len(done):X}\r\n".encode() + done + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--reply", default=DEFAULT_REPLY)
    parser.add_argument(
        "--tool",
        default="",
        choices=["", "write_file", "run_command"],
        help="also stream one tool call so the approval UI can be exercised",
    )
    parser.add_argument("--tool-file", default=".shots/mock-write.txt", help="path for the write_file tool call")
    args = parser.parse_args(argv)

    Handler.reply = args.reply
    Handler.tool = args.tool
    Handler.tool_file = args.tool_file
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[mock] listening on http://127.0.0.1:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
