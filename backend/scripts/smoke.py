"""Quick manual smoke test against a running dev backend.

Usage:  python scripts/smoke.py [port] [token]
Creates (or reuses) a project for ``backend`` itself, indexes it, runs a search
and prints a summary. Exits non-zero on the first failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

PORT = sys.argv[1] if len(sys.argv) > 1 else "64525"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken"
BASE = f"http://127.0.0.1:{PORT}/api"
HEADERS = {"X-Session-Token": TOKEN}
ROOT = Path(__file__).resolve().parent.parent.parent


def check(label: str, ok: bool, extra: object = "") -> None:
    print(("PASS " if ok else "FAIL ") + label + (f"  {extra}" if extra else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    with httpx.Client(base_url=BASE, headers=HEADERS, timeout=60) as client:
        health = client.get("/health")
        check("health", health.status_code == 200, health.json())

        unauth = httpx.get(f"{BASE}/projects", timeout=10)
        check("project list requires token", unauth.status_code == 401, unauth.status_code)

        created = client.post("/projects", json={"path": str(ROOT), "name": "CodeSlice"})
        if created.status_code == 409:
            projects = client.get("/projects").json()
            project = next(p for p in projects if p["path"].lower() == str(ROOT).lower())
        else:
            check("create project", created.status_code == 200, created.text[:200])
            project = created.json()
        pid = project["id"]
        check("project id", isinstance(pid, int), pid)

        tree = client.get(f"/projects/{pid}/tree", params={"depth": 2})
        entries = tree.json()
        check("tree", tree.status_code == 200 and isinstance(entries, list) and entries,
              [e["name"] for e in entries[:5]])

        file_resp = client.get(f"/projects/{pid}/file", params={"path": "backend/app/main.py"})
        check("read file", file_resp.status_code == 200 and "create_app" in file_resp.json()["content"])

        job = client.post(f"/projects/{pid}/index")
        check("index start", job.status_code == 200, job.text[:200])
        job_id = job.json().get("job_id")
        import time

        for _ in range(120):
            status = client.get(f"/jobs/{job_id}").json()
            if status["status"] in ("done", "error"):
                break
            time.sleep(1)
        check("index done", status["status"] == "done", status)
        stats = client.get(f"/projects/{pid}/index").json()
        last = stats.get("last_job") or {}
        check("index stats", ((last.get("stats") or {}).get("chunks", 0)) > 0, last.get("stats"))

        hits = client.get(f"/projects/{pid}/search", params={"q": "build_chat_model", "top_k": 5}).json()
        check("search hits", len(hits) > 0, [h["path"] for h in hits[:3]])

        settings = client.get("/settings/model")
        check("settings", settings.status_code == 200, settings.json())

        conv = client.post(
            "/conversations",
            json={"project_id": pid, "mode": "snippet", "file_path": "backend/app/main.py",
                  "start_line": 1, "end_line": 20, "selected_code": "def create_app():"},
        )
        check("create conversation", conv.status_code == 200, conv.text[:200])
        cid = conv.json()["id"]
        check("conversation public", "selected_code" not in conv.json())
        msgs = client.get(f"/conversations/{cid}").json()
        check("conversation detail", msgs["conversation"]["id"] == cid and msgs["pending_interrupt"] is None)

        print(json.dumps({"project_id": pid, "conversation_id": cid, "search_top": hits[0]["path"]}, indent=2))


if __name__ == "__main__":
    main()
