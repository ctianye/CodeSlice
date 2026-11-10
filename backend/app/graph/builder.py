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
import os
from pathlib import Path
from typing import Any

from app.graph.ast import Extraction
from app.graph.ast.engine import extract_file
from app.graph.ast.languages import config_for
from app.graph.ast.resolve import resolve
from app.services import paths

log = logging.getLogger(__name__)

# Directories that are never source. Shared with the indexer through
# ``paths.is_ignored_dir`` so a stray ``release-021`` tree cannot make the graph
# and the retrieval index describe different corpora (that mismatch is how 1.5k
# bundled files became graph nodes).
_SKIP_SUFFIX = paths.BINARY_SUFFIXES | paths.IGNORED_SUFFIXES

MAX_FILE_BYTES = 1_500_000


def collect_sources(root: Path) -> list[tuple[str, bytes]]:
    """Read every indexable source file under ``root``.

    Returns ``(relative_path, bytes)`` pairs. Files without a known extractor are
    skipped here rather than emitted as empty file nodes: a file node with no
    symbols says nothing and only adds noise to the canvas.
    """
    out: list[tuple[str, bytes]] = []
    # os.walk so ignored trees are never entered: rglob stats every entry, and a
    # locked binary in a build-output folder raised OSError mid-scan.
    for current, dirnames, filenames in os.walk(root, onerror=lambda _: None):
        dirnames[:] = [name for name in dirnames if not paths.is_ignored_dir(name)]
        base = Path(current)
        for name in filenames:
            path = base / name
            if path.suffix.lower() in _SKIP_SUFFIX:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                continue
            if config_for(rel) is None:
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if paths.looks_binary(data):
                continue
            out.append((rel, data))
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
