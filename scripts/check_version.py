"""Fail when the shipped version declarations disagree.

Version numbers live in several files for different consumers: the installer
metadata reads ``desktop/package.json``, the service reports
``backend/app/core/config.py``, and the CDN update fallback serves
``version.json``. The v0.3.1 release bumped the package files but not
``config.py``, so the installed app reported 0.3.0: Settings showed the old
version and the update check kept offering the release the user was running.

Run this before building or tagging a release:

    python scripts/check_version.py
    python scripts/check_version.py --tag v0.3.2

Exit code is 0 when every source agrees, 1 otherwise.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_NAME = "code-learning-agent-backend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _backend_config_version() -> str:
    tree = ast.parse(_read(ROOT / "backend/app/core/config.py"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise ValueError("backend/app/core/config.py has no VERSION string constant")


def _backend_project_version() -> str:
    return tomllib.loads(_read(ROOT / "backend/pyproject.toml"))["project"]["version"]


def _backend_lock_version() -> str:
    lock = tomllib.loads(_read(ROOT / "backend/uv.lock"))
    for package in lock.get("package", []):
        if package.get("name") == PROJECT_NAME:
            return package["version"]
    raise ValueError(f"backend/uv.lock has no package {PROJECT_NAME!r}")


def _package_version(path: str) -> str:
    return json.loads(_read(ROOT / path))["version"]


def _lock_version(path: str) -> str:
    lock = json.loads(_read(ROOT / path))
    return lock["packages"][""]["version"]


def _version_json() -> dict:
    return json.loads(_read(ROOT / "version.json"))


def collect() -> tuple[dict[str, str], list[str]]:
    """Every version source, plus problems only this script can spot."""
    sources: dict[str, str] = {
        "backend/app/core/config.py:VERSION": _backend_config_version(),
        "backend/pyproject.toml:project.version": _backend_project_version(),
        "backend/uv.lock:project package": _backend_lock_version(),
        "desktop/package.json:version": _package_version("desktop/package.json"),
        "desktop/package-lock.json:version": _lock_version("desktop/package-lock.json"),
        "frontend/package.json:version": _package_version("frontend/package.json"),
        "frontend/package-lock.json:version": _lock_version("frontend/package-lock.json"),
        "version.json:version": _version_json()["version"],
    }

    problems: list[str] = []
    cdn = _version_json()
    tag = cdn.get("tag")
    if tag != f"v{cdn.get('version')}":
        problems.append(f"version.json: tag {tag!r} does not match version {cdn.get('version')!r} (expected 'v<version>')")
    url = cdn.get("url")
    expected_url = f"https://github.com/ctianye/CodeSlice/releases/tag/{tag}"
    if url != expected_url:
        problems.append(f"version.json: url {url!r} does not match tag {tag!r}")

    # A hard-coded fallback here once shipped a stale VERSIONINFO; make sure
    # nobody reintroduces one.
    ps1 = _read(ROOT / "packaging/build_desktop.ps1")
    if re.search(r"^\s*\$appVersion\s*=\s*['\"]\d", ps1, re.MULTILINE):
        problems.append("packaging/build_desktop.ps1: $appVersion must come from package.json, not a literal")

    return sources, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify all CodeSlice version declarations agree.")
    parser.add_argument("--tag", help="Release tag to compare against, e.g. v0.3.2")
    args = parser.parse_args()

    sources, problems = collect()
    distinct = sorted(set(sources.values()))
    versions = ", ".join(distinct)
    width = max(len(name) for name in sources)
    for name, version in sources.items():
        print(f"{name.ljust(width)}  {version}")

    if len(distinct) != 1:
        problems.insert(0, f"version mismatch across sources: {versions}")
    else:
        version = distinct[0]
        if args.tag and args.tag != f"v{version}":
            problems.append(f"release tag {args.tag!r} does not match version {version!r} (expected 'v{version}')")

    if problems:
        print("\nFAIL:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"\nOK: all sources agree on {distinct[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
