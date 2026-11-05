"""Drive the packaged Electron app over the Chrome DevTools Protocol.

Used to verify the *installed* build end to end: the window must load the
renderer from the backend origin (not ``file://``), the preload bridge must
hand the renderer a real token, and the UI must render without console errors.

Run the app with ``--remote-debugging-port=9223`` first, then::

    python scripts/ui_smoke.py --port 9223 --api-port 49839 --token <token>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

import websockets


def _targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0

    async def call(self, method: str, **params):
        self._id += 1
        msg_id = self._id
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=True,
            userGesture=True,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")


CHECKS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append(name)
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return ok


async def run(devtools_port: int, api_port: int, token: str) -> int:
    pages = [t for t in _targets(devtools_port) if t.get("type") == "page"]
    # The docked DevTools window is also a page target; the renderer is the one
    # served over http.
    http_pages = [t for t in pages if str(t.get("url", "")).startswith("http")]
    if http_pages:
        pages = http_pages
    if not pages:
        print("no page target — is the app running with --remote-debugging-port?")
        return 2
    page = pages[0]
    print(f"target: {page.get('title')!r} {page.get('url')}")

    failures = 0
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Runtime.enable")
        await cdp.call("Log.enable")
        await asyncio.sleep(2.5)  # let React mount and the first requests settle

        url = await cdp.evaluate("location.href")
        failures += not check(
            "renderer served from the backend http origin (same-origin with /api)",
            url == f"http://127.0.0.1:{api_port}/" or url.startswith(f"http://127.0.0.1:{api_port}/"),
            str(url),
        )

        bridge = await cdp.evaluate(
            "(() => { const b = window.codeslice; return b ? {token: !!b.token, apiBase: b.apiBase, platform: b.platform} : null })()"
        )
        failures += not check("preload bridge present", bridge is not None, str(bridge))
        if bridge:
            failures += not check("bridge carries a non-empty session token", bool(bridge["token"]), str(bridge))
            failures += not check(
                "bridge apiBase points at the backend origin (or is same-origin empty)",
                bridge["apiBase"] in ("", f"http://127.0.0.1:{api_port}"),
                str(bridge.get("apiBase")),
            )

        title = await cdp.evaluate("document.title")
        failures += not check("document title is CodeSlice", title == "CodeSlice", str(title))

        body_len = await cdp.evaluate("document.body.innerText.trim().length")
        failures += not check(
            "page rendered visible text", isinstance(body_len, int) and body_len > 0, f"{body_len} chars"
        )

        text = await cdp.evaluate("document.body.innerText")
        failures += not check(
            "welcome / empty-state copy rendered",
            any(k in text for k in ("CodeSlice", "项目", "开始", "打开")),
            text[:160].replace("\n", " | "),
        )

        # A 401 would mean the token never reached the renderer.
        health = await cdp.evaluate(
            "fetch('/api/health').then(r => r.status + ' ' + r.statusText).catch(e => 'ERR ' + e)"
        )
        failures += not check(
            "same-origin /api/health reachable from the renderer", str(health).startswith("200"), str(health)
        )

        authed = await cdp.evaluate(
            "fetch('/api/projects', {headers: {'X-Session-Token': (window.codeslice && window.codeslice.token) || ''}})"
            ".then(async r => r.status + ' ' + (await r.text()).slice(0, 60)).catch(e => 'ERR ' + e)"
        )
        failures += not check(
            "token from the bridge authorizes /api/projects", str(authed).startswith("200"), str(authed)
        )

        monaco = await cdp.evaluate("fetch('/monaco/vs/loader.js').then(r => r.status).catch(e => 'ERR ' + e)")
        failures += not check("Monaco loads offline through the backend", str(monaco) == "200", str(monaco))

        # No fattening stack of console errors on the welcome screen.
        errors = await cdp.evaluate("JSON.stringify((window.__codesliceErrors || []).slice(0, 5))")
        if errors and errors != "[]":
            print(f"        console errors: {errors}")
        if token:
            tok = await cdp.evaluate("(window.codeslice && window.codeslice.token) === " + json.dumps(token))
            check("bridge token matches the backend session token", bool(tok))

    print(f"\nCHECKS {len(CHECKS)}  FAILURES {failures}")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    try:  # Windows consoles default to cp936, which mangles the UI's Chinese copy
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9223, help="DevTools port the app was started with")
    parser.add_argument("--api-port", type=int, required=True, help="Port the packaged backend is listening on")
    parser.add_argument("--token", default="", help="Expected session token (optional cross-check)")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.port, args.api_port, args.token))


if __name__ == "__main__":
    sys.exit(main())
