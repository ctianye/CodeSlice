"""End-to-end check for the document edit/save path.

Drives the real renderer over CDP: switch README.md to edit mode, type a marker
line at the end of the buffer, save with Ctrl+S, then read the file back from
the backend API to prove it landed on disk.

Usage: python doc_save_test.py
"""

from __future__ import annotations

import json
import time
import urllib.request

from websockets.sync.client import connect

PORT = 9223
API = "http://127.0.0.1:8123"
TOKEN = "devtoken"
PROJECT_ID = 1
TARGET = "README.md"
MARKER = "<!-- codeslice save probe -->"


def cdp(ws, method: str, params: dict | None = None) -> dict:
    seq = cdp.seq = getattr(cdp, "seq", 0) + 1
    ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == seq:
            return message


def evaluate(ws, script: str):
    result = cdp(ws, "Runtime.evaluate", {"expression": script, "returnByValue": True, "awaitPromise": True})
    return result.get("result", {}).get("result", {}).get("value")


def read_file() -> str:
    url = f"{API}/api/projects/{PROJECT_ID}/file?path={TARGET}"
    request = urllib.request.Request(url, headers={"X-Session-Token": TOKEN})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))["content"]


def page_target() -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list") as response:
        pages = json.loads(response.read().decode("utf-8"))
    for page in pages:
        if page.get("type") == "page" and page.get("url", "").startswith("http"):
            return page["webSocketDebuggerUrl"]
    raise SystemExit("no renderer target found")


def main() -> None:
    original = read_file()
    print(f"before: {len(original)} bytes, marker={MARKER in original}")

    with connect(page_target(), max_size=None) as ws:
        cdp(ws, "Runtime.enable")

        # Make sure README.md is the active tab, then enter edit mode. A fresh
        # renderer may have no tabs at all, so fall back to the file tree.
        evaluate(
            ws,
            """
            (async () => {
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              let tab = Array.from(document.querySelectorAll('.editor-tab-label'))
                .find((n) => n.textContent.includes('README.md'));
              if (!tab) {
                const row = Array.from(document.querySelectorAll('.tree [role="treeitem"]'))
                  .find((n) => n.textContent.trim() === 'README.md');
                if (row) row.click();
                await sleep(1800);
                tab = Array.from(document.querySelectorAll('.editor-tab-label'))
                  .find((n) => n.textContent.includes('README.md'));
              }
              if (tab) tab.click();
              await sleep(1500);
              const edit = Array.from(document.querySelectorAll('.doc-mode button'))
                .find((n) => n.textContent.trim() === '编辑');
              if (edit) edit.click();
              await sleep(1500);
              return document.querySelector('.doc-mode button.active')?.textContent?.trim() ?? null;
            })()
            """,
        )

        # Put the caret at the very end of the buffer through Monaco's own API:
        # dispatching keystrokes would need ~20k characters of typing.
        typed = evaluate(
            ws,
            """
            (() => {
              const el = document.querySelector('.monaco-editor');
              if (!el) return 'no editor';
              return 'editor mounted';
            })()
            """,
        )
        print(f"editor: {typed}")

        # Focus the editor, then move to end with Ctrl+End and type the marker.
        cdp(
            ws,
            "Runtime.evaluate",
            {"expression": "document.querySelector('.monaco-editor textarea').focus()"},
        )
        time.sleep(0.3)
        for _ in range(2):
            cdp(
                ws,
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "modifiers": 2,
                    "key": "End",
                    "code": "End",
                    "windowsVirtualKeyCode": 35,
                    "nativeVirtualKeyCode": 35,
                },
            )
            cdp(
                ws,
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "modifiers": 2,
                    "key": "End",
                    "code": "End",
                    "windowsVirtualKeyCode": 35,
                    "nativeVirtualKeyCode": 35,
                },
            )
        cdp(ws, "Input.insertText", {"text": "\n" + MARKER + "\n"})
        time.sleep(0.5)

        dirty = evaluate(
            ws,
            """
            ({
              saveDisabled: document.querySelector('.doc-save')?.disabled ?? null,
              badge: document.querySelector('.editor-toolbar')?.textContent?.includes('未保存') ?? false,
            })
            """,
        )
        print(f"after typing: {dirty}")

        # Ctrl+S goes through the app's own shortcut handler.
        for kind in ("keyDown", "keyUp"):
            cdp(
                ws,
                "Input.dispatchKeyEvent",
                {
                    "type": kind,
                    "modifiers": 2,
                    "key": "s",
                    "code": "KeyS",
                    "windowsVirtualKeyCode": 83,
                    "nativeVirtualKeyCode": 83,
                    "text": "s" if kind == "keyDown" else None,
                },
            )
        time.sleep(2.5)

        after = evaluate(
            ws,
            """
            ({
              notice: document.querySelector('.editor-toolbar .tag.ok')?.textContent?.trim() ?? null,
              dirty: document.querySelector('.editor-toolbar')?.textContent?.includes('未保存') ?? false,
              error: document.querySelector('.feedback-error')?.textContent?.trim() ?? null,
            })
            """,
        )
        print(f"after save: {after}")

    saved = read_file()
    print(f"after: {len(saved)} bytes, marker={MARKER in saved}")
    if not saved.endswith(MARKER + "\n") and MARKER not in saved:
        raise SystemExit("FAIL: marker missing on disk")

    # Restore the file so the probe leaves no trace in the working tree.
    body = json.dumps({"path": TARGET, "content": original}).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/api/projects/{PROJECT_ID}/file",
        data=body,
        method="PUT",
        headers={"X-Session-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        restored = json.loads(response.read().decode("utf-8"))
    print(f"restored: {restored['size']} bytes")
    print("PASS")


if __name__ == "__main__":
    main()
