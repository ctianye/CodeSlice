"""Capture a CDP screenshot (and optionally run JS first)."""

from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.request

from websockets.sync.client import connect


def targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def pick(port: int, title_contains: str = "") -> dict:
    pages = [t for t in targets(port) if t.get("type") == "page" and not t["url"].startswith("devtools://")]
    if title_contains:
        pages = [
            p for p in pages if title_contains.lower() in (p.get("title") or "").lower() or title_contains in p["url"]
        ]
    if not pages:
        raise SystemExit("no matching page")
    return pages[0]


class CDP:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.ws = connect(url, max_size=64 * 1024 * 1024, open_timeout=timeout, legacy=True)
        self.n = 0
        self.timeout = timeout

    def send(self, method: str, **params) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        deadline = time.time() + self.timeout
        while True:
            if time.time() > deadline:
                raise SystemExit(f"{method}: timed out after {self.timeout}s")
            msg = json.loads(self.ws.recv(timeout=max(0.1, deadline - time.time())))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise SystemExit(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def eval(self, expr: str, await_promise: bool = True):
        res = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        if res.get("exceptionDetails"):
            return {"$exception": str(res["exceptionDetails"].get("text") or res["exceptionDetails"])[:400]}
        return res.get("result", {}).get("value")

    def shot(self, path: pathlib.Path) -> None:
        data = self.send("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9223)
    ap.add_argument("--out", default="D:/CodeSplit/.shots/ui.png")
    ap.add_argument("--js", default=None)
    ap.add_argument("--js-file", default=None)
    ap.add_argument("--wait", type=float, default=0.6)
    args = ap.parse_args()

    page = pick(args.port)
    cdp = CDP(page["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    script = args.js
    if args.js_file:
        script = pathlib.Path(args.js_file).read_text(encoding="utf-8")
    if script:
        print("eval:", cdp.eval(script))
        time.sleep(args.wait)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cdp.shot(out)
    print(f"saved {out} ({out.stat().st_size} bytes) url={page['url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
