"""Check note export + retrieval against a *packaged* backend binary.

The packaged backend is the one thing the test suite never touches: it runs from a
PyInstaller bundle where the working directory, the static mount and the data dir
all differ from a source checkout. This script drives the HTTP API directly so it
also works when the renderer is not available.

    codeslice-backend.exe                     (prints CODESLICE_READY {port, token})
    python scripts/check_packaged_notes.py --port 8199 --token pkgtoken

Non-ASCII bodies must go through httpx rather than the PowerShell console, which
re-encodes JSON bodies and turns Chinese titles into question marks.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

TITLE = "打包版笔记检查"
MARKER = "quokka_packaged_marker"


def check(name: str, ok: bool, detail: str = "") -> bool:
    suffix = f" -- {detail}" if detail else ""
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{suffix}")
    return ok


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8199/api")
    parser.add_argument("--token", default="pkgtoken")
    parser.add_argument("--project-path", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args()

    failures = 0
    with httpx.Client(base_url=args.base, headers={"X-Session-Token": args.token}, timeout=180) as client:
        health = client.get("/health")
        failures += not check("backend answers /api/health", health.status_code == 200, str(health.status_code))

        project = client.post("/projects", json={"path": args.project_path}).json()
        failures += not check("project registered", bool(project.get("id")), args.project_path)

        note = client.post(
            "/notes",
            json={
                "project_id": project["id"],
                "title": TITLE,
                "content_md": f"# {TITLE}\n\n{MARKER} 说明。",
            },
        ).json()
        failures += not check("note title survives the round-trip", note.get("title") == TITLE, repr(note.get("title")))
        failures += not check("note entered the knowledge base", note.get("indexed") is True)

        export = client.post(f"/notes/{note['id']}/export").json()
        failures += not check(
            "export filename keeps the Chinese title",
            export.get("filename") == f"{TITLE}.md",
            export.get("filename"),
        )
        exported = Path(export["path"])
        failures += not check("exported markdown exists on disk", exported.is_file(), str(exported))
        text = exported.read_text(encoding="utf-8") if exported.is_file() else ""
        failures += not check("exported markdown starts with the title", text.startswith(f"# {TITLE}"))
        failures += not check(
            "exported markdown has one title heading", text.count(f"# {TITLE}") == 1, str(text.count(f"# {TITLE}"))
        )
        failures += not check("exported markdown keeps the Chinese body", f"{MARKER} 说明" in text)

        hits = client.get(f"/projects/{project['id']}/search", params={"q": MARKER, "top_k": 3}).json()
        note_hits = [row for row in hits if row["path"] == f"note://{note['id']}"]
        failures += not check("search returns the note chunk", bool(note_hits))
        if note_hits:
            failures += not check(
                "note chunk is marked kind=note", note_hits[0]["kind"] == "note", note_hits[0]["kind"]
            )
            body = note_hits[0]["content"]
            failures += not check(
                "note chunk does not repeat the title", body.count(f"# {TITLE}") == 1, str(body.count(f"# {TITLE}"))
            )

    print("PACKAGED NOTE CHECK OK" if failures == 0 else f"PACKAGED NOTE CHECK FAILED ({failures})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
