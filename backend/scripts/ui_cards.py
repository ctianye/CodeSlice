"""Drive the *conversation cards* feature through the real UI over CDP.

Checks the parts unit tests cannot: that the sidebar splits conversations into
"this project" and "other projects", and that a file with existing snippet
conversations renders code cards that jump back into the chat pane.

The app must be running with ``--remote-debugging-port=9223`` (dev or packaged),
and its backend must be reachable on the same origin::

    python scripts/ui_cards.py --port 9223 --project D:\\CodeSplit --file docs/API_CONTRACT.md
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
        targets = [t for t in json.loads(resp.read().decode("utf-8")) if t.get("type") == "page"]
    # Electron also exposes its DevTools window as a page target.
    return [t for t in targets if str(t.get("url", "")).startswith("http")] or targets


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

    async def wait_for(self, expression: str, timeout: float = 30.0, interval: float = 0.35):
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


async def open_file(cdp: CDP, file_path: str) -> bool:
    """Switch to the files tab and click through to *file_path* in the tree."""
    # The workspace may have been restored directly, in which case only a reload
    # is needed; make sure we are not still on the welcome screen.
    if await cdp.js("document.querySelectorAll('.project-card').length > 0"):
        await cdp.js("(() => { const c = document.querySelector('.project-card'); c.click(); return true; })()")
        await cdp.wait_for("!!document.querySelector('.sidebar')", timeout=20)

    await cdp.js(
        "(() => { const tabs = Array.from(document.querySelectorAll('.sidebar button'));"
        " const t = tabs.find(b => b.innerText.trim() === '文件'); if (t) t.click(); return !!t; })()"
    )
    await cdp.wait_for("document.querySelectorAll('.tree button').length > 0", timeout=20)
    parts = file_path.split("/")
    for index, name in enumerate(parts[:-1]):
        # Directories collapse when reopened, so expand one level at a time.
        await cdp.js(
            "(() => { const rows = Array.from(document.querySelectorAll('.tree button'));"
            f" const d = rows.find(r => r.innerText.trim() === {json.dumps(name)});"
            " if (d) d.click(); return !!d; })()"
        )
        await asyncio.sleep(0.9 if index == 0 else 0.6)
    opened = await cdp.js(
        "(() => { const rows = Array.from(document.querySelectorAll('.tree button'));"
        f" const f = rows.find(r => r.innerText.trim() === {json.dumps(parts[-1])});"
        " if (f) f.click(); return !!f; })()"
    )
    await asyncio.sleep(2.2)
    return bool(opened)


async def run(devtools_port: int, project_path: str, file_path: str) -> int:
    pages = _pages(devtools_port)
    if not pages:
        print("no page target — is the app running with --remote-debugging-port?")
        return 2

    async with websockets.connect(pages[0]["webSocketDebuggerUrl"], max_size=32 * 1024 * 1024) as ws:
        cdp = CDP(ws)
        await cdp.call("Runtime.enable")
        await cdp.call("Page.enable")
        api = "const T = window.codeslice.token;const H = {'X-Session-Token': T, 'Content-Type': 'application/json'};"

        # --- seed: two snippet conversations on the same file, plus a second project
        seeded = await cdp.js(
            f"(async () => {{ {api}"
            f"  const r = await fetch('/api/projects', {{method: 'POST', headers: H,"
            f"    body: JSON.stringify({{path: {json.dumps(project_path)}}})}});"
            "  return r.status + ' ' + (await r.text()).slice(0, 200); })()"
        )
        if not str(seeded).startswith("200"):
            print(f"cannot register project: {seeded}")
            return 1
        project_id = json.loads(str(seeded).split(" ", 1)[1])["id"]

        for start, end, title in ((12, 20, "卡片检查·前段"), (120, 128, "卡片检查·后段")):
            created = await cdp.js(
                f"(async () => {{ {api}"
                "  const r = await fetch('/api/conversations', {method: 'POST', headers: H, body: JSON.stringify({"
                f"    project_id: {project_id}, title: {json.dumps(title)}, mode: 'snippet',"
                f"    file_path: {json.dumps(file_path)}, start_line: {start}, end_line: {end},"
                "    selected_code: 'x'})});"
                "  return r.status; })()"
            )
            check(f"seeded snippet conversation {start}-{end}", int(created) == 200, str(created))

        # --- reload so the renderer picks up the new conversations
        await cdp.call("Page.reload", ignoreCache=False)
        await asyncio.sleep(2.5)

        # 1) open the project: the app restores the last project, so the welcome
        #    screen is only shown on a fresh profile.
        at_welcome = await cdp.wait_for(
            "document.querySelectorAll('.project-card').length > 0 || !!document.querySelector('.sidebar')", timeout=30
        )
        if not at_welcome:
            print("neither the welcome screen nor the workspace rendered")
            return 1
        if await cdp.js("document.querySelectorAll('.project-card').length > 0"):
            clicked = await cdp.js(
                "(() => { const cards = Array.from(document.querySelectorAll('.project-card'));"
                f" const target = cards.find(c => c.innerText.includes({json.dumps(file_path.split('/')[0])})) || cards[0];"
                " target.click(); return true; })()"
            )
            check("project card is clickable", bool(clicked))
        else:
            print("        (workspace already open, skipping the welcome screen)")
        check("workspace opens", await cdp.wait_for("!!document.querySelector('.sidebar')", timeout=20))

        # 2) sidebar: two labelled groups, the current project's rows first
        await cdp.js(
            "(() => { const tabs = Array.from(document.querySelectorAll('.sidebar [role=tab], .sidebar button'));"
            " const t = tabs.find(b => b.innerText.trim() === '对话'); if (t) t.click(); return !!t; })()"
        )
        grouped = await cdp.wait_for("document.querySelectorAll('.list-section-head').length >= 1", timeout=15)
        check("sidebar renders section headers", bool(grouped))
        heads = await cdp.js(
            "Array.from(document.querySelectorAll('.list-section-head')).map(h => h.innerText.replace(/\\n/g, ' ').trim())"
        )
        check("sections are labelled", isinstance(heads, list) and len(heads) >= 1, str(heads))
        mine = await cdp.js(
            "(() => { const h = document.querySelector('.list-section-head'); return h ? h.innerText.trim() : ''; })()"
        )
        check("first section is this project", "本项目" in str(mine), str(mine))
        rows = await cdp.js("document.querySelectorAll('.list-row').length")
        check("seeded conversations are listed", isinstance(rows, int) and rows >= 2, f"{rows} rows")

        # 3) files tab → open the file that has conversations
        opened_file = await open_file(cdp, file_path)
        check(f"file {file_path} is openable from the tree", bool(opened_file))

        # 4) the card strip appears above the editor and lists one card per range
        cards_ready = await cdp.wait_for("document.querySelectorAll('.code-card').length >= 2", timeout=25)
        check("conversation cards render above the editor", bool(cards_ready))
        cards = await cdp.js(
            "Array.from(document.querySelectorAll('.code-card')).map(c => c.innerText.replace(/\\n/g, ' ').trim())"
        )
        check(
            "cards show conversation titles",
            isinstance(cards, list) and any("卡片检查" in c for c in cards),
            str(cards)[:200],
        )
        ranges = await cdp.js("Array.from(document.querySelectorAll('.code-card-range')).map(r => r.innerText.trim())")
        check("cards show their line ranges", isinstance(ranges, list) and len(ranges) >= 2, str(ranges))

        # 5) clicking a card opens that conversation in the chat pane
        jumped = await cdp.js(
            "(() => { const c = document.querySelector('.code-card'); if (!c) return false; c.click(); return true; })()"
        )
        check("card is clickable", bool(jumped))
        await asyncio.sleep(1.5)
        active = await cdp.js(
            "Array.from(document.querySelectorAll('.list-row')).filter(r => r.innerHTML.includes('active') ||"
            " r.className.includes('active')).length"
        )
        title_shown = await cdp.js(
            "(() => { const h = document.querySelector('.chat-header'); return h ? h.innerText : ''; })()"
        )
        check("chat pane switched to the conversation", "卡片检查" in str(title_shown), str(title_shown)[:120])
        check("sidebar highlights the active row", isinstance(active, int), f"{active} active rows")

        # 6) hovering a card tints the covered lines inside Monaco (never an overlay).
        #    A synthetic MouseEvent does not drive React's enter/leave plugin, so
        #    move the real cursor over the card instead.
        box = await cdp.js(
            "(() => { const c = document.querySelector('.code-card'); if (!c) return null;"
            " const r = c.getBoundingClientRect();"
            " return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()"
        )
        if box:
            await cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=box["x"], y=box["y"])
        tinted = await cdp.wait_for("document.querySelectorAll('.conversation-card-highlight').length > 0", timeout=8)
        lines = await cdp.js("document.querySelectorAll('.conversation-card-highlight').length")
        check("hovering a card tints the covered lines", bool(tinted), f"{lines} lines")

        # 7) the note button turns an answer into a note, then flips to "open note"
        model_ready = await cdp.js(
            f"(async () => {{ {api}"
            "  const r = await fetch('/api/settings/model', {headers: H}); const d = await r.json();"
            "  return !!(d.chat && d.chat.api_key_configured); })()"
        )
        if not model_ready:
            print("        (skipping note generation: no chat model configured)")
        else:
            # seed an answered conversation so there is an assistant message to summarise
            note_body = {
                "project_id": project_id,
                "title": "卡片检查·笔记",
                "mode": "snippet",
                "file_path": file_path,
                "start_line": 30,
                "end_line": 34,
                "selected_code": "x",
            }
            seeded_chat = await cdp.js(
                "(async () => {"
                "  const T = window.codeslice.token;"
                "  const H = {'X-Session-Token': T, 'Content-Type': 'application/json'};"
                f"  const created = await fetch('/api/conversations', {{method: 'POST', headers: H,"
                f"    body: JSON.stringify({json.dumps(note_body, ensure_ascii=False)})}});"
                "  if (created.status !== 200) return 'create ' + created.status;"
                "  const conv = await created.json();"
                "  const sent = await fetch('/api/conversations/' + conv.id + '/messages', {method: 'POST', headers: H,"
                "    body: JSON.stringify({content: '这段在做什么？'})});"
                "  const text = await sent.text();"
                "  return sent.status + ' ' + (text.includes('\"type\": \"done\"') ? 'done' : 'no-done'); })()"
            )
            check("seeded an answered conversation for the note", str(seeded_chat).startswith("200"), str(seeded_chat))

            await cdp.call("Page.reload", ignoreCache=False)
            await asyncio.sleep(2.5)
            await open_file(cdp, file_path)
            clicked_note = await cdp.js(
                "(() => { const cards = Array.from(document.querySelectorAll('.code-card'));"
                " const card = cards.find(c => c.querySelector('.code-card-title')?.innerText === '卡片检查·笔记');"
                " if (!card) return 'no-card'; const btn = card.querySelector('.icon.note');"
                " if (!btn) return 'no-button'; const label = btn.getAttribute('aria-label') || '';"
                " btn.click(); return label; })()"
            )
            check(
                "note button is present on the card",
                str(clicked_note) not in ("no-card", "no-button"),
                str(clicked_note),
            )
            if str(clicked_note) not in ("no-card", "no-button"):
                flipped = await cdp.wait_for(
                    "(() => { const cards = Array.from(document.querySelectorAll('.code-card'));"
                    " const card = cards.find(c => c.querySelector('.code-card-title')?.innerText === '卡片检查·笔记');"
                    " return !!card && card.querySelector('.icon.note')?.getAttribute('aria-label') === '打开笔记'; })()",
                    timeout=40,
                )
                check("generating a note flips the button to 'open note'", bool(flipped))
                landed = await cdp.js(
                    "(() => { const tab = document.querySelector('.sidebar [aria-selected=true]');"
                    " const text = document.querySelector('.sidebar')?.innerText || '';"
                    " return (tab ? tab.innerText : '') + '|' + (text.includes('卡片检查·笔记') ? 'note-visible' : 'no-note'); })()"
                )
                check(
                    "the new note opens in the notes tab",
                    "笔记" in str(landed) and "note-visible" in str(landed),
                    str(landed),
                )

        errors = await cdp.js("JSON.stringify((window.__codesliceErrors || []).slice(0, 5))")
        if errors and errors != "[]":
            print(f"        console errors: {errors}")

    total = len(RESULTS)
    failed = sum(1 for ok, _, _ in RESULTS if not ok)
    print(f"\nCHECKS {total}  FAILURES {failed}")
    for ok, name, detail in RESULTS:
        if not ok:
            print(f"  - {name} {detail}")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--project", default=r"D:\CodeSplit")
    parser.add_argument("--file", default="docs/API_CONTRACT.md")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.port, args.project, args.file))


if __name__ == "__main__":
    sys.exit(main())
