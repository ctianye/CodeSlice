"""Bring the CDP page to front and (optionally) capture a screenshot."""

from __future__ import annotations

import base64
import json
import pathlib
import sys
import time
import urllib.request

from websockets.sync.client import connect


def pick(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        pages = json.loads(resp.read().decode("utf-8"))
    pages = [t for t in pages if t.get("type") == "page" and not t["url"].startswith("devtools://")]
    if not pages:
        raise SystemExit("no page")
    return pages[0]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9223
    out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
    page = pick(port)
    print("url:", page["url"])
    with connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024, open_timeout=20) as ws:
        n = 0

        def send(method: str, **params):
            nonlocal n
            n += 1
            ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv(timeout=25))
                if msg.get("id") == n:
                    if "error" in msg:
                        raise SystemExit(f"{method}: {msg['error']}")
                    return msg.get("result", {})

        print("bring-to-front:", send("Page.bringToFront"))
        time.sleep(0.5)
        info = send("Runtime.evaluate", expression="document.visibilityState", returnByValue=True)
        print("visibility:", info.get("result", {}).get("value"))
        if out is not None:
            t0 = time.time()
            data = send("Page.captureScreenshot", format="png", fromSurface=True)["data"]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(base64.b64decode(data))
            print(f"saved {out} ({out.stat().st_size} bytes) in {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
