"""End-to-end check of the citation → anchor chain against a *live* backend.

Reproduces the reported symptom ("锚点面板一直是空的"): a chat turn whose retrieval
finds Chinese content must emit ``citation`` events and leave rows in
``code_anchors``. Run the backend first, then:

    .venv\\Scripts\\python.exe scripts\\check_anchors.py --port 8111 --token devtoken
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
    parser.add_argument("--question", default="这个项目的入口在哪里？")
    args = parser.parse_args()

    base = f"http://127.0.0.1:{args.port}/api"
    headers = {"X-Session-Token": args.token}
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(name)

    with httpx.Client(base_url=base, headers=headers, timeout=180) as client:
        convo = client.post(
            "/conversations",
            json={"project_id": args.project, "title": "锚点验证", "mode": "project"},
        ).json()
        conversation_id = convo["id"]

        events: list[dict] = []
        with client.stream("POST", f"/conversations/{conversation_id}/messages", json={"content": args.question}) as r:
            check("message stream is 200", r.status_code == 200, str(r.status_code))
            events = list(sse_events(r))

        kinds = [e.get("type") for e in events]
        citations = [e for e in events if e.get("type") == "citation"]
        check("citations emitted", bool(citations), f"{len(citations)} citation events")
        check("done event last", kinds and kinds[-1] == "done", ",".join(kinds[-3:]))
        text = "".join(e.get("delta", "") for e in events if e.get("type") == "token")
        check("streamed text non-empty", bool(text.strip()), f"{len(text)} chars")

        anchors = client.get(f"/projects/{args.project}/anchors").json()
        check("anchors persisted", bool(anchors), f"{len(anchors)} anchor rows")
        if anchors:
            for a in anchors[:4]:
                print(f"    {a['file_path']}:{a['start_line']}-{a['end_line']} [{a.get('status')}]")
            check(
                "anchor status valid",
                all(a["status"] in {"unchanged", "modified", "moved", "deleted"} for a in anchors),
            )
            check("anchor has snippet", all(a.get("snippet") for a in anchors))

        detail = client.get(f"/conversations/{conversation_id}").json()
        check(
            "conversation detail has messages", bool(detail.get("messages")), f"{len(detail.get('messages', []))} msgs"
        )
        check("assistant message stored", any(m["role"] == "assistant" for m in detail.get("messages", [])))

        refreshed = client.post(f"/projects/{args.project}/anchors/refresh").json()
        check("refresh reports a summary", isinstance(refreshed, dict), json.dumps(refreshed, ensure_ascii=False)[:120])

        client.delete(f"/conversations/{conversation_id}")

    print(f"\nFAILURES {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
