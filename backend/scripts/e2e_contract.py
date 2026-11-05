"""End-to-end contract check against a *running* CodeSlice backend.

Unlike ``smoke.py`` (which starts nothing and only touches core endpoints), this
script mirrors the exact HTTP surface used by ``frontend/src/api.ts`` so a UI
regression caused by a backend change is caught before packaging.

Usage::

    python scripts/e2e_contract.py --base http://127.0.0.1:8765 --token devtoken \
        --project D:\\CodeSplit

Exit code 0 = every call matched the frontend's expectations.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

import httpx

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: Any = "") -> bool:
    if ok:
        PASS.append(name)
        print(f"  [ok]   {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} :: {detail}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--token", default="devtoken")
    parser.add_argument("--project", default=r"D:\CodeSplit")
    parser.add_argument("--frontend", default=r"D:\CodeSplit\frontend\dist")
    args = parser.parse_args()

    client = httpx.Client(
        base_url=args.base,
        headers={"X-Session-Token": args.token},
        timeout=60.0,
    )

    print("\n== auth + health ==")
    anon = httpx.get(f"{args.base}/api/health", timeout=10)
    check(
        "unauthenticated /api/health is reachable (contract exception)",
        anon.status_code == 200 and anon.json().get("status") == "ok",
        anon.status_code,
    )
    anon_projects = httpx.get(f"{args.base}/api/projects", timeout=10)
    check("unauthenticated /api/projects is 401", anon_projects.status_code == 401, anon_projects.status_code)
    bad_token = httpx.get(f"{args.base}/api/projects", headers={"X-Session-Token": "wrong"}, timeout=10)
    check("wrong token is 401", bad_token.status_code == 401, bad_token.status_code)
    health = client.get("/api/health")
    check(
        "GET /api/health",
        health.status_code == 200 and health.json().get("status") == "ok",
        health.text[:200],
    )

    print("\n== static frontend ==")
    index = httpx.get(f"{args.base}/", timeout=30)
    check(
        "GET / serves built index.html",
        index.status_code == 200 and '<div id="root">' in index.text,
        index.status_code,
    )

    print("\n== projects ==")
    created = client.post("/api/projects", json={"path": args.project})
    check("POST /api/projects", created.status_code in (200, 201), created.text[:200])
    project = created.json()
    project_id = project["id"]

    again = client.post("/api/projects", json={"path": args.project})
    check(
        "POST /api/projects is idempotent per path",
        again.status_code == 200 and again.json()["id"] == project_id,
        again.text[:200],
    )

    listed = client.get("/api/projects")
    check(
        "GET /api/projects lists it",
        listed.status_code == 200 and any(p["id"] == project_id for p in listed.json()),
        listed.status_code,
    )

    tree = client.get(f"/api/projects/{project_id}/tree")
    nodes = tree.json() if tree.status_code == 200 else []
    check("GET /projects/{id}/tree", tree.status_code == 200 and len(nodes) > 0, tree.text[:200])

    target = "backend/app/agent/graph.py"
    file_res = client.get(f"/api/projects/{project_id}/file", params={"path": target})
    body = file_res.json() if file_res.status_code == 200 else {}
    check(
        "GET /projects/{id}/file returns contract fields",
        file_res.status_code == 200
        and {"path", "language", "content", "size", "mtime", "truncated"} <= set(body)
        and body.get("size", 0) > 0,
        file_res.text[:200],
    )

    bad = client.get(f"/api/projects/{project_id}/file", params={"path": "../../etc/passwd"})
    check("path traversal is rejected", bad.status_code in (400, 404), bad.status_code)

    missing = client.get("/api/projects/999999")
    check("GET unknown project is 404", missing.status_code == 404, missing.status_code)

    print("\n== git ==")
    status_res = client.get(f"/api/projects/{project_id}/git/status")
    check("GET /git/status", status_res.status_code == 200, status_res.text[:200])
    diff_res = client.get(f"/api/projects/{project_id}/git/diff")
    check("GET /git/diff", diff_res.status_code == 200, diff_res.text[:200])

    print("\n== indexing ==")
    started = client.post(f"/api/projects/{project_id}/index", params={"force": "true"})
    check("POST /index returns job id", started.status_code == 200 and "job_id" in started.json(), started.text[:200])
    job_id = started.json()["job_id"]

    job: dict[str, Any] = {}
    deadline = time.time() + 180
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in ("done", "error", "failed"):
            break
        time.sleep(0.5)
    check(
        "indexing job finishes",
        job.get("status") == "done",
        json.dumps(job, ensure_ascii=False)[:300],
    )
    stats = job.get("stats") or {}
    check("job reports file/chunk stats", (stats.get("chunks") or 0) > 0, stats)

    summary = client.get(f"/api/projects/{project_id}/index")
    check(
        "GET /index summary",
        summary.status_code == 200 and "last_job" in summary.json(),
        summary.text[:200],
    )

    print("\n== search ==")
    found = client.get(f"/api/projects/{project_id}/search", params={"q": "interrupt approval", "top_k": 5})
    hits = found.json() if found.status_code == 200 else []
    check("GET /search returns chunks", found.status_code == 200 and len(hits) > 0, found.text[:200])
    if hits:
        first = hits[0]
        check(
            "search hit carries path/lines/content",
            {"path", "start_line", "end_line", "content"} <= set(first),
            list(first),
        )

    weird = client.get(f"/api/projects/{project_id}/search", params={"q": '"quoted" AND x* C++', "top_k": 3})
    check("FTS query with special chars does not 500", weird.status_code == 200, weird.text[:200])

    print("\n== settings ==")
    settings = client.get("/api/settings/model")
    payload = settings.json() if settings.status_code == 200 else {}
    check(
        "GET /settings/model",
        settings.status_code == 200 and "chat" in payload and "embedding" in payload,
        settings.text[:200],
    )
    check(
        "api_key is never echoed back",
        "api_key" not in payload.get("chat", {}) and "api_key" not in (payload.get("embedding") or {}),
        list(payload.get("chat", {})),
    )

    print("\n== conversations ==")
    conv = client.post(
        "/api/conversations",
        json={
            "project_id": project_id,
            "title": "e2e",
            "mode": "snippet",
            "file_path": target,
            "start_line": 1,
            "end_line": 60,
            "selected_code": "def x():\n    return 1\n",
        },
    )
    check("POST /conversations", conv.status_code in (200, 201), conv.text[:300])
    conversation = conv.json()
    conversation_id = conversation["id"]

    conv_list = client.get("/api/conversations", params={"project_id": project_id})
    check("GET /conversations?project_id", conv_list.status_code == 200, conv_list.text[:200])

    detail = client.get(f"/api/conversations/{conversation_id}")
    check(
        "GET /conversations/{id} has conversation+messages+pending_interrupt",
        detail.status_code == 200 and {"conversation", "messages", "pending_interrupt"} <= set(detail.json()),
        detail.text[:200],
    )

    # SSE plumbing: without a model configured the endpoint must fail fast with a
    # 4xx JSON error rather than hanging or emitting a broken stream.
    stream = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"content": "解释一下这个函数"},
    )
    check(
        "POST /messages without model -> 4xx JSON",
        stream.status_code == 400 and "detail" in stream.json(),
        f"{stream.status_code} {stream.text[:200]}",
    )

    resume = client.post(
        f"/api/conversations/{conversation_id}/resume",
        json={"interrupt_id": "no-such-interrupt", "decision": "approve"},
    )
    check(
        "POST /resume without a pending interrupt -> 4xx JSON",
        resume.status_code in (400, 404) and "detail" in resume.json(),
        f"{resume.status_code} {resume.text[:200]}",
    )

    bad_decision = client.post(
        f"/api/conversations/{conversation_id}/resume",
        json={"interrupt_id": "x", "decision": "maybe"},
    )
    check("POST /resume rejects an unknown decision", bad_decision.status_code == 400, bad_decision.status_code)

    interrupt = client.get(f"/api/conversations/{conversation_id}/interrupt")
    check("GET /interrupt", interrupt.status_code in (200, 404), interrupt.text[:200])

    print("\n== notes / anchors / command rules ==")
    note = client.post(
        "/api/notes",
        json={
            "project_id": project_id,
            "title": "e2e note",
            "body": "记住 agent 的审批流",
            "file_path": target,
            "start_line": 1,
            "end_line": 20,
        },
    )
    check("POST /notes", note.status_code in (200, 201), note.text[:300])
    note_id = note.json()["id"]

    note_list = client.get("/api/notes", params={"project_id": project_id})
    check("GET /notes", note_list.status_code == 200 and any(n["id"] == note_id for n in note_list.json()))

    note_put = client.put(f"/api/notes/{note_id}", json={"title": "e2e note v2"})
    check("PUT /notes/{id}", note_put.status_code == 200, note_put.text[:200])

    anchors = client.get(f"/api/projects/{project_id}/anchors")
    check("GET /projects/{id}/anchors", anchors.status_code == 200, anchors.text[:200])

    refresh = client.post(f"/api/projects/{project_id}/anchors/refresh")
    check("POST /anchors/refresh", refresh.status_code == 200, refresh.text[:200])

    rules = client.get(f"/api/projects/{project_id}/command-rules")
    check("GET /command-rules", rules.status_code == 200, rules.text[:200])

    rule = client.post(
        f"/api/projects/{project_id}/command-rules",
        json={"pattern": "pytest*", "scope": "always", "needs_confirm": False},
    )
    check("POST /command-rules", rule.status_code in (200, 201), rule.text[:300])
    if rule.status_code in (200, 201):
        rule_id = rule.json()["id"]
        rule_put = client.put(f"/api/projects/{project_id}/command-rules/{rule_id}", json={"needs_confirm": True})
        check("PUT /command-rules/{id}", rule_put.status_code == 200, rule_put.text[:200])
        rule_del = client.delete(f"/api/projects/{project_id}/command-rules/{rule_id}")
        check("DELETE /command-rules/{id}", rule_del.status_code == 200, rule_del.text[:200])

    note_del = client.delete(f"/api/notes/{note_id}")
    check("DELETE /notes/{id}", note_del.status_code == 200, note_del.text[:200])

    conv_del = client.delete(f"/api/conversations/{conversation_id}")
    check("DELETE /conversations/{id}", conv_del.status_code == 200, conv_del.text[:200])

    print()
    print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
    if FAIL:
        print("failed:", ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
