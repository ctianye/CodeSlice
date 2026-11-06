"""Reload the CDP page and wait for it to come back, without relying on eval.

`location.reload()` from JS drops the execution context mid-call, which used to
leave the socket hanging. This sends `Page.reload` through the protocol with the
HTTP cache disabled, then polls for the new script tag. That matters because the
renderer keeps serving a stale bundle otherwise.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

from websockets.sync.client import connect


def pick_page(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        pages = json.load(resp)
    for page in pages:
        if str(page.get("url", "")).startswith("http"):
            return page["webSocketDebuggerUrl"]
    raise SystemExit("no http page found")


class CDP:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.ws = connect(url, max_size=64 * 1024 * 1024, open_timeout=timeout, legacy=True)
        self.n = 0

    def send(self, method: str, **params) -> dict:
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            raw = self.ws.recv(timeout=30)
            data = json.loads(raw)
            if data.get("id") != mid:
                continue
            if "error" in data:
                raise SystemExit(f"{method}: {data['error']}")
            return data.get("result", {})

    def eval(self, expression: str):
        result = self.send("Runtime.evaluate", expression=expression, returnByValue=True)
        return result.get("result", {}).get("value")


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9223
    cdp = CDP(pick_page(port))
    cdp.send("Page.enable")
    cdp.send("Network.enable")
    cdp.send("Network.setCacheDisabled", cacheDisabled=True)
    cdp.send("Page.reload", ignoreCache=True)

    # The reload tears down the execution context; poll until a document answers.
    time.sleep(4)
    for _ in range(15):
        try:
            cdp.eval("1")
            break
        except Exception:
            time.sleep(1)
    else:
        raise SystemExit("renderer never came back")

    scripts = cdp.eval("JSON.stringify([...document.querySelectorAll('script[src]')].map(s=>s.getAttribute('src')))")
    print("scripts:", scripts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
