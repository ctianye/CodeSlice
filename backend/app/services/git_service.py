"""Read-only Git integration (branch, status, diff, commit info)."""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT = 20


def _run(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout


def is_repo(root: Path) -> bool:
    if not (root / ".git").exists():
        return False
    return bool(_run(root, "rev-parse", "--is-inside-work-tree").strip())


def current_state(root: Path) -> dict:
    if not is_repo(root):
        return {
            "is_repo": False,
            "branch": None,
            "head": None,
            "dirty": False,
            "staged": [],
            "modified": [],
            "untracked": [],
        }
    branch = _run(root, "rev-parse", "--abbrev-ref", "HEAD").strip() or None
    head = _run(root, "rev-parse", "HEAD").strip() or None
    porcelain = _run(root, "status", "--porcelain=v1")
    staged, modified, untracked = [], [], []
    for line in porcelain.splitlines():
        if len(line) < 3:
            continue
        code, path = line[:2], line[3:].strip()
        if code == "??":
            untracked.append(path)
        else:
            if code[0] not in (" ", "?"):
                staged.append(path)
            if code[1] not in (" ", "?"):
                modified.append(path)
    return {
        "is_repo": True,
        "branch": branch,
        "head": head,
        "dirty": bool(staged or modified or untracked),
        "staged": staged,
        "modified": modified,
        "untracked": untracked,
    }


def diff(root: Path, rel_path: str | None = None, max_bytes: int = 200_000) -> str:
    if not is_repo(root):
        return ""
    args = ["diff", "--stat", "--patch", "HEAD"]
    if rel_path:
        args += ["--", rel_path]
    out = _run(root, *args)
    if not out:
        # Fresh repo without commits: fall back to working tree diff.
        args = ["diff", "--stat", "--patch"]
        if rel_path:
            args += ["--", rel_path]
        out = _run(root, *args)
    return out[:max_bytes]


def head_sha(root: Path) -> str | None:
    if not is_repo(root):
        return None
    return _run(root, "rev-parse", "HEAD").strip() or None


def file_at_head(root: Path, rel_path: str) -> str | None:
    if not is_repo(root):
        return None
    out = _run(root, "show", f"HEAD:{rel_path}")
    return out or None
