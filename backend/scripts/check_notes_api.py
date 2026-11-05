"""End-to-end check of the note endpoints against the live backend.

Reads only through the public API so it exercises the same paths the desktop
app uses: real HTTP, real session token, real model provider.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8123"
TOKEN = "devtoken"


def call(method: str, path: str, body: dict | None = None, timeout: float = 180.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"X-Session-Token": TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read().decode("utf-8") or "null")


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(name: str, ok: bool, detail: object = "") -> None:
        nonlocal checks
        checks += 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if not ok else ""))
        if not ok:
            failures.append(name)

    status, projects = call("GET", "/api/projects")
    check("GET /api/projects", status == 200 and isinstance(projects, list), (status, projects))
    project_id = projects[0]["id"]

    status, conversations = call("GET", f"/api/conversations?project_id={project_id}")
    check("GET /api/conversations", status == 200 and bool(conversations), status)
    snippet = next((c for c in conversations if c.get("file_path")), None)
    check("a snippet conversation exists", snippet is not None, conversations[:2])
    if snippet is None:
        return 1
    print(f"      using conversation {snippet['title']!r} ({snippet['file_path']}:{snippet['start_line']})")

    # 1. Whole-conversation note: the title must describe a knowledge point and
    #    must not repeat the "about <file> lines N-M" location.
    status, whole = call("POST", f"/api/conversations/{snippet['id']}/note")
    check("POST /conversations/{id}/note -> 200", status == 200, (status, whole))
    if status == 200:
        title = whole.get("title") or ""
        print(f"      title={title!r}  summary_failed={whole.get('summary_failed')}")
        check("title is non-empty", bool(title.strip()), title)
        check("title has no file name", ".md" not in title and ".py" not in title, title)
        check("title has no line numbers", ":" not in title and "行" not in title, title)
        check("title does not start with 关于", not title.startswith("关于"), title)
        check("title is not the conversation title", title != snippet["title"], title)
        check(
            "note links code refs", "## 关联代码" in (whole.get("content_md") or ""), whole.get("content_md", "")[:200]
        )
        check("note is indexed", whole.get("indexed") is True, whole.get("indexed"))
        note_id = whole["id"]
        conversation_title = snippet["title"]

        # 2. Reading one note back, and the by-file cards carrying its title.
        status, one = call("GET", f"/api/notes/{note_id}")
        check("GET /api/notes/{id}", status == 200 and one.get("id") == note_id, status)
        check("GET /api/notes/{id} carries index state", "indexed" in (one or {}), one)

        status, cards = call("GET", f"/api/projects/{project_id}/conversations/by-file?path={snippet['file_path']}")
        card = next((c for c in cards if c["id"] == snippet["id"]), None) if status == 200 else None
        check("by-file card exists", card is not None, status)
        if card is not None:
            check("card carries note_title", card.get("note_title") == title, (card.get("note_title"), title))

        # 3. Retitling rewrites a legacy location title through the model.
        status, retitled = call("POST", f"/api/notes/{note_id}/retitle")
        check("POST /api/notes/{id}/retitle -> 200", status == 200, (status, retitled))
        if status == 200:
            print(f"      retitled={retitled.get('title')!r} failed={retitled.get('retitle_failed')}")
            check("retitle kept a title", bool((retitled.get("title") or "").strip()), retitled)
            check("retitle reports a flag", "retitle_failed" in retitled, retitled)

        # 4. A single-message note keeps the same note row for the conversation.
        status, detail = call("GET", f"/api/conversations/{snippet['id']}")
        answer = next((m for m in reversed(detail["messages"]) if m["role"] == "assistant"), None)
        check("conversation has an assistant message", answer is not None, status)
        if answer is not None:
            status, single = call("POST", f"/api/conversations/{snippet['id']}/messages/{answer['id']}/note")
            check("POST message note -> 200", status == 200, (status, single))
            if status == 200:
                check("both generations share one note", single["id"] == note_id, (single["id"], note_id))
                check(
                    "message note title is still a knowledge point",
                    ".md" not in (single.get("title") or "") and not (single.get("title") or "").startswith("关于"),
                    single.get("title"),
                )

        # A missing note still answers 404 rather than an empty body.
        status, missing = call("GET", "/api/notes/999999")
        check("GET missing note -> 404", status == 404, (status, missing))

        print(f"\n      conversation title was: {conversation_title!r}")
        print(f"      note title is:           {title!r}")

    print(f"\nCHECKS {checks} FAILURES {len(failures)}")
    for name in failures:
        print(f"  - {name}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
