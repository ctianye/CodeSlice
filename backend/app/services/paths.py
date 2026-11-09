"""Path safety helpers: every path handed to the service must resolve inside
the project root. Symlinks are resolved before the check.
"""

from __future__ import annotations

from pathlib import Path

from app.core.errors import InvalidInput

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "dist",
    "build",
    "out",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    ".next",
    ".nuxt",
    "target",
    "bin",
    "obj",
    "coverage",
    ".DS_Store",
    ".codeslice",
    # Local app data (dev scratch dirs, job logs, sqlite copies).
    ".devdata",
    # Generated in this repository: screenshots, graph previews, scratch trees.
    # The graph builder already skips these; the retrieval index must agree or
    # a project's top hits become debug artifacts instead of source.
    ".shots",
    ".tmp-preview-data",
    ".acl-fix",
    "acl-recovery",
    "graphify-out",
    # Vendored Monaco editor bundle copied in by the frontend build.
    "monaco",
    # Installed/bundled app output — indexing these drowns real source in vendor code.
    "release",
    "win-unpacked",
    "dist-backend",
    "_internal",
    "site-packages",
    "vendor",
    "third_party",
    "third-party",
}

# Files that are technically text but never useful as reading material.
IGNORED_FILENAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
    "go.sum",
    "RECORD",
    "METADATA",
    ".DS_Store",
    "thumbs.db",
}

# Suffixes whose content is machine data even when it decodes as text.
IGNORED_SUFFIXES = {
    ".lock",
    ".map",
    ".pak",
    ".pem",
    ".crt",
    ".der",
    ".pfx",
    ".blockmap",
    ".log",
    ".snap",
    ".patch",
    ".diff",
}

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".bmp",
    ".pdf",
    ".zip",
    ".gz",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".pyc",
    ".class",
    ".jar",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".sqlite",
    ".db",
}


def resolve_root(project_path: str) -> Path:
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise InvalidInput(f"project path is not a directory: {project_path}")
    return root


def resolve_in_project(root: Path, rel: str | None) -> Path:
    """Resolve ``rel`` under ``root``; rejects traversal and absolute escape."""
    if rel in (None, "", ".", "/"):
        return root
    candidate = (root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise InvalidInput(f"path escapes project root: {rel}") from None
    return candidate


def relative_to(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def is_ignored(name: str) -> bool:
    return name in DEFAULT_IGNORES


def skip_file(path_name: str) -> bool:
    """True when a *file* should never be indexed (secrets, lockfiles, maps…)."""
    lowered = path_name.lower()
    # Environment files hold credentials; neither the retrieval index nor the
    # embedding provider should receive them. This also covers .env.local etc.
    if lowered == ".env" or lowered.startswith(".env."):
        return True
    if lowered in {n.lower() for n in IGNORED_FILENAMES}:
        return True
    return Path(lowered).suffix in IGNORED_SUFFIXES
