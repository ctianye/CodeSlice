"""mtime conflict check for the document editor.

Opens README.md in edit mode, changes the file underneath the editor, then saves
from the UI. The backend must reject the write and the app must surface the
reason instead of overwriting someone else's change.

Usage: python doc_conflict_test.py
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
MARKER = "<!-- codeslice conflict probe -->"


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


def fetch_file() -> dict:
    request = urllib.request.Request(
        f"{API}/api/projects/{PROJECT_ID}/file?path={TARGET}",
        headers={"X-Session-Token": TOKEN},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def write_file(content: str) -> dict:
    body = json.dumps({"path": TARGET, "content": content}).encode("utf-8")
    request = urllib.request.Request(
        f"{API}/api/projects/{PROJECT_ID}/file",
        data=body,
        method="PUT",
        headers={"X-Session-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def page_target() -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list") as response:
        pages = json.loads(response.read().decode("utf-8"))
    for page in pages:
        if page.get("type") == "page" and page.get("url", "").startswith("http"):
            return page["webSocketDebuggerUrl"]
    raise SystemExit("no renderer target found")


def main() -> None:
    original = fetch_file()["content"]

    with connect(page_target(), max_size=None) as ws:
        cdp(ws, "Runtime.enable")
        opened = evaluate(
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
        print(f"mode: {opened}")

        # Somebody else writes the file while the editor holds the old mtime.
        written = write_file(original + "\n" + MARKER + "\n")
        print(f"external write: {written['size']} bytes, mtime={written['mtime']:.3f}")

        # Type into the editor so there is something to save, then Ctrl+S.
        cdp(ws, "Runtime.evaluate", {"expression": "document.querySelector('.monaco-editor textarea').focus()"})
        time.sleep(0.3)
        cdp(ws, "Input.insertText", {"text": "x"})
        time.sleep(0.4)
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
        time.sleep(2.0)

        result = evaluate(
            ws,
            """
            ({
              error: document.querySelector('.error-note .err-body')?.textContent?.trim() ?? null,
              stillDirty: document.querySelector('.editor-toolbar')?.textContent?.includes('未保存') ?? false,
              notice: document.querySelector('.editor-toolbar .tag.ok')?.textContent?.trim() ?? null,
            })
            """,
        )
        print(f"ui after rejected save: {result}")

    on_disk = fetch_file()["content"]
    print(f"disk still has external marker: {MARKER in on_disk}")

    # Restore the pristine file.
    write_file(original)
    print(f"restored: {len(original)} chars")

    if not result.get("error"):
        raise SystemExit("FAIL: no error surfaced for a rejected save")
    # The UI localises the backend's reason; accept either wording so this probe
    # does not break when a translation lands.
    reason = result["error"]
    conflict = "file changed on disk" in reason or "\u6587\u4ef6\u5728\u6253\u5f00\u540e\u88ab\u6539\u52a8" in reason
    if not conflict:
        raise SystemExit(f"FAIL: error text does not explain the mtime conflict: {reason!r}")
    if MARKER not in on_disk:
        raise SystemExit("FAIL: the rejected save clobbered the external change")
    print("PASS")


if __name__ == "__main__":
    main()
