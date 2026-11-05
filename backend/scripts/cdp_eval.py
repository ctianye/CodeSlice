"""Probe the running app's DOM over CDP and print a structural outline.

Ad-hoc debugging helper for the UI smoke runs — not part of the test suite.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

import websockets


async def run(port: int, expression: str) -> int:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        pages = [t for t in json.loads(resp.read().decode("utf-8")) if t.get("type") == "page"]
    if not pages:
        print("no page target")
        return 2
    async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Runtime.enable"}))
        await ws.send(
            json.dumps(
                {
                    "id": 2,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                        "userGesture": True,
                    },
                }
            )
        )
        while True:
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if data.get("id") == 2:
                result = data.get("result", {})
                if result.get("exceptionDetails"):
                    print(json.dumps(result["exceptionDetails"], ensure_ascii=False, indent=2))
                    return 1
                value = result.get("result", {}).get("value")
                print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
                return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("js", help="JavaScript expression to evaluate in the page")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.port, args.js))


if __name__ == "__main__":
    sys.exit(main())
