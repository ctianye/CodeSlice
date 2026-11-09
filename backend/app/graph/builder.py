"""Build the code knowledge graph from source files via AST extraction.

This is the pipeline graphify uses, reduced to what this project needs:

    collect files → AST extract each (pass 1) → resolve across the corpus (pass 2)
                  → cluster → node/edge payload

**Pass 1** (``ast.engine.extract_file``) turns one file into scope nodes, rationale
nodes and *unresolved* call/import/inheritance facts. It never guesses what a name
refers to.

**Pass 2** (``ast.resolve.resolve``) has the whole project in view and resolves
those facts. Edges it can prove are ``EXTRACTED``; anything ambiguous is dropped
rather than inferred, because a confidently wrong caller is read as fact by the
user while a missing one merely leaves a gap.

The previous implementation derived the graph from the retrieval ``chunks`` table.
That was cheap but wrong in a specific way worth recording: chunks are sized for
embedding, not for structure, so a function could be split across chunks and a
name match could not distinguish a definition from a call. Reading the AST directly
gives exact line ranges, exact containment, and docstrings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.graph.ast import Extraction
from app.graph.ast.engine import extract_file
from app.graph.ast.languages import config_for
from app.graph.ast.resolve import resolve

log = logging.getLogger(__name__)

# Directories that are never source: mirrors the indexer's ignore list so the
# graph and the retrieval index describe the same corpus.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "dist", "build", "out", ".venv", "venv",
    "env", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea",
    ".vscode", ".next", ".nuxt", "target", "bin", "obj", "coverage", ".codeslice",
    ".devdata", "monaco", "release", "win-unpacked", "dist-backend", "_internal",
    "site-packages", "vendor", "third_party", "third-party", "graphify-out",
    ".tmp-preview-data", ".shots", "acl-recovery",
}

_SKIP_SUFFIX = {
    ".lock", ".map", ".pak", ".pem", ".crt", ".der", ".pfx", ".blockmap", ".log",
    ".snap", ".patch", ".diff", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".pdf", ".zip", ".gz", ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".pyc",
    ".class", ".jar", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav",
    ".sqlite", ".db", ".wasm",
}

MAX_FILE_BYTES = 1_500_000


def collect_sources(root: Path) -> list[tuple[str, bytes]]:
    """Read every indexable source file under ``root``.

    Returns ``(relative_path, bytes)`` pairs. Files without a known extractor are
    skipped here rather than emitted as empty file nodes: a file node with no
    symbols says nothing and only adds noise to the canvas.
    """
    out: list[tuple[str, bytes]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in _SKIP_SUFFIX:
            continue
        rel = relative.as_posix()
        if config_for(rel) is None:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            out.append((rel, path.read_bytes()))
        except OSError:
            continue
    out.sort(key=lambda item: item[0])
    return out


def extract_all(sources: list[tuple[str, bytes]]) -> list[Extraction]:
    """Pass 1 over every file. A broken file becomes an error entry, not a crash."""
    extractions: list[Extraction] = []
    errors = 0
    for rel, data in sources:
        config = config_for(rel)
        if config is None:
            continue
        extraction = extract_file(rel, data, config)
        if extraction.error:
            errors += 1
            log.debug("graph: %s -> %s", rel, extraction.error)
        extractions.append(extraction)
    if errors:
        log.info("graph: %d file(s) failed to parse", errors)
    return extractions


def build_graph(sources: list[tuple[str, bytes]] | None = None, *, root: Path | None = None) -> dict[str, Any]:
    """Full pipeline: read → extract → resolve. Returns nodes/edges/stats."""
    if sources is None:
        if root is None:
            raise ValueError("build_graph needs either sources or root")
        sources = collect_sources(root)
    extractions = extract_all(sources)
    return resolve(extractions)
