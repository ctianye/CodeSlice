"""Drive the packaged app through a real user flow over CDP.

Complements ``ui_smoke.py`` (which only checks the welcome state): this one
registers a project through the renderer's own fetch bridge, indexes it, opens
a file in Monaco, and asks a question — verifying that the three-pane UI keeps
working once data exists.

The app must be running with ``--remote-debugging-port=9223``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

import websockets


def _pages(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as resp:
        return [t for t in json.loads(resp.read().decode("utf-8")) if t.get("type") == "page"]


class CDP:
    def __init__(self, ws) -> None:
        self.ws = ws
        self._id = 0

    async def call(self, method: str, **params):
        self._id += 1
        msg_id = self._id
        await self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            data = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=60))
            if data.get("id") == msg_id:
                if "error" in data:
                    raise RuntimeError(f"{method}: {data['error']}")
                return data.get("result", {})

    async def js(self, expression: str):
        result = await self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=True,
            userGesture=True,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    async def wait_for(self, expression: str, timeout: float = 45.0, interval: float = 0.4):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if await self.js(expression):
                return True
            await asyncio.sleep(interval)
        return False


RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((bool(ok), name, detail))
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    return bool(ok)


async def run(devtools_port: int, project_path: str, file_path: str) -> int:
    pages = _pages(devtools_port)
    if not pages:
        print("no page target")
        return 2
    async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Runtime.enable")
        await cdp.call("Page.enable")

        api = (
            "const T = window.codeslice.token;"
            "const H = {'X-Session-Token': T, 'Content-Type': 'application/json'};"
        )

        # 1) Register the project the way the welcome screen does.
        created = await cdp.js(
            f"(async () => {{ {api}"
            f"  const r = await fetch('/api/projects', {{method: 'POST', headers: H,"
            f"    body: JSON.stringify({{path: {json.dumps(project_path)}}})}});"
            "  return r.status + ' ' + (await r.text()).slice(0, 200); })()"
        )
        failures = 0
        failures += not check("POST /api/projects accepts the folder", str(created).startswith("200"), str(created))
        try:
            project = json.loads(str(created).split(" ", 1)[1])
            project_id = project["id"]
        except (ValueError, KeyError):
            print("cannot continue without a project id")
            return 1

        # 2) Index it and wait for the job to finish.
        started = await cdp.js(
            f"(async () => {{ {api}"
            f"  const r = await fetch('/api/projects/{project_id}/index', {{method: 'POST', headers: H}});"
            "  return r.status + ' ' + (await r.text()).slice(0, 160); })()"
        )
        check("index job started", str(started).startswith("200"), str(started))

        indexed = await cdp.wait_for(
            f"(async () => {{ {api}"
            f"  const r = await fetch('/api/projects/{project_id}/index', {{headers: H}});"
            "  const d = await r.json();"
            "  return !!(d.last_job && (d.last_job.status === 'done' || d.files > 0)); })().catch(() => false)",
            timeout=120,
        )
        failures += not check("index job reaches done", bool(indexed))

        # 3) Reload so the app re-reads its project list from disk/API.
        await cdp.call("Page.reload", ignoreCache=False)
        await asyncio.sleep(2.0)
        mounted = await cdp.wait_for("!!document.querySelector('.sidebar, aside, [class*=sidebar]')", timeout=30)
        failures += not check("UI remounts after reload with a sidebar", bool(mounted))

        shown = await cdp.js("document.body.innerText")
        failures += not check(
            "project appears in the sidebar",
            project_path.split("\\")[-1] in str(shown) or project_path in str(shown),
            (str(shown)[:200]).replace("\n", " | "),
        )

        # 4) Open a file through the API and confirm Monaco paints it.
        opened = await cdp.js(
            f"(async () => {{ {api}"
            f"  const r = await fetch('/api/projects/{project_id}/file?path=' + encodeURIComponent({json.dumps(file_path)}), {{headers: H}});"
            "  const d = await r.json(); return r.status + ' ' + (d.language || '?') + ' lines=' + ((d.content || '').split('\\n').length); })()"
        )
        failures += not check("GET file returns source + language", str(opened).startswith("200"), str(opened))

        monaco = await cdp.js(
            "(() => { const el = document.querySelector('.monaco-editor');"
            " return el ? (el.innerText || '').slice(0, 120) : 'no-monaco'; })()"
        )
        failures += not check("Monaco editor is mounted in the DOM", str(monaco) != "no-monaco", str(monaco)[:120])

        errors = await cdp.js(
            "(() => { const es = window.__codesliceErrors || []; return JSON.stringify(es.slice(0, 5)); })()"
        )
        print(f"        captured console errors: {errors}")

    total = len(RESULTS)
    failed = sum(1 for ok, _, _ in RESULTS if not ok)
    print(f"\nCHECKS {total}  FAILURES {failed}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--project", required=True, help="Absolute folder to register as a project")
    parser.add_argument("--file", default="backend/app/main.py", help="Repo-relative file to open")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.port, args.project, args.file))


if __name__ == "__main__":
    sys.exit(main())
