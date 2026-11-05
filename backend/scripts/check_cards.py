"""Live check of the per-file conversation cards against a running backend.

Covers the two endpoints the editor cards depend on:

* ``GET /projects/{id}/conversations/by-file`` — grouping, ordering, note flags
* ``POST /conversations/{id}/messages/{message_id}/note`` — note generation

Run the backend first (a mock model is enough), then:

    .venv\\Scripts\\python.exe scripts\\check_cards.py --port 8111 --token devtoken --project 1
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx


def sse_events(response: httpx.Response):
    for line in response.iter_lines():
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8111)
    parser.add_argument("--token", default="devtoken")
    parser.add_argument("--project", type=int, default=1)
    parser.add_argument("--path", default="docs/API_CONTRACT.md")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}/api"
    headers = {"X-Session-Token": args.token}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    with httpx.Client(base_url=base, headers=headers, timeout=180) as client:
        # --- two snippet conversations on the same file, deliberately out of order
        late = client.post(
            "/conversations",
            json={
                "project_id": args.project,
                "title": "卡片验证·后段",
                "mode": "snippet",
                "file_path": args.path,
                "start_line": 120,
                "end_line": 128,
                "selected_code": "x = 1",
            },
        ).json()
        early = client.post(
            "/conversations",
            json={
                "project_id": args.project,
                "title": "卡片验证·前段",
                "mode": "snippet",
                "file_path": args.path,
                "start_line": 12,
                "end_line": 20,
                "selected_code": "y = 2",
            },
        ).json()
        check("both snippet conversations created", bool(late.get("id") and early.get("id")))

        # --- by-file listing
        resp = client.get(
            f"/projects/{args.project}/conversations/by-file",
            params={"path": args.path},
        )
        check("by-file is 200", resp.status_code == 200, str(resp.status_code))
        rows = resp.json() if resp.status_code == 200 else []
        ids = [row["id"] for row in rows]
        check("by-file only lists this file", all(row["file_path"] == args.path for row in rows))
        check("by-file is ordered by start line", ids.index(early["id"]) < ids.index(late["id"]))
        # Older runs may have left their own conversations (and notes) on the same
        # file, so only the two rows created above are asserted here.
        fresh = [row for row in rows if row["id"] in (early["id"], late["id"])]
        check(
            "by-file reports has_note=false before a note exists",
            len(fresh) == 2 and all(row["has_note"] is False for row in fresh),
            f"{len(fresh)} fresh rows",
        )
        check("by-file carries project_name", all(row.get("project_name") for row in rows))

        # --- traversal is rejected
        bad = client.get(f"/projects/{args.project}/conversations/by-file", params={"path": "../outside.py"})
        check("by-file rejects ../outside.py", bad.status_code == 400, str(bad.status_code))
        missing = client.get(f"/projects/{args.project}/conversations/by-file", params={"path": "nope/missing.py"})
        check("by-file 404s for an unknown file", missing.status_code == 404, str(missing.status_code))

        # --- one real turn so there is an assistant answer to summarise
        events: list[dict] = []
        with client.stream(
            "POST",
            f"/conversations/{early['id']}/messages",
            json={"content": "这两行在做什么？"},
        ) as r:
            check("message stream is 200", r.status_code == 200, str(r.status_code))
            if r.status_code == 200:
                events = list(sse_events(r))

        done = [e for e in events if e.get("type") == "done"]
        check("stream ends with done", bool(done) and events[-1].get("type") == "done")

        detail = client.get(f"/conversations/{early['id']}").json()
        answers = [m for m in detail.get("messages", []) if m.get("role") == "assistant"]
        check("conversation has an assistant answer", bool(answers))

        if answers:
            note = client.post(f"/conversations/{early['id']}/messages/{answers[-1]['id']}/note")
            check("note endpoint is 200", note.status_code == 200, f"{note.status_code} {note.text[:120]}")
            body = note.json() if note.status_code == 200 else {}
            check("note is attached to the conversation", body.get("conversation_id") == early["id"])
            check("note has markdown content", bool((body.get("content_md") or "").strip()))

            rows = client.get(
                f"/projects/{args.project}/conversations/by-file",
                params={"path": args.path},
            ).json()
            with_note = [row for row in rows if row["id"] == early["id"]]
            check("card now reports has_note", bool(with_note) and with_note[0]["has_note"] is True)
            check("card exposes the note id", bool(with_note) and with_note[0].get("note_id") == body.get("id"))

        # --- sidebar grouping
        listed = client.get("/conversations", params={"project_id": args.project, "include_others": "true"}).json()
        check("include_others returns rows", bool(listed))
        check("include_others carries project_name", all(row.get("project_name") for row in listed))

        # --- cleanup
        for conversation in (late, early):
            client.delete(f"/conversations/{conversation['id']}")

    print()
    print(f"FAILURES: {len(failures)}")
    for name in failures:
        print(f"  - {name}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
