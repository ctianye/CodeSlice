"""The notes-only graph. Both the canvas and AI use this exact topology.

The topology is derived from the ``notes`` and ``note_folders`` tables, and it is
rebuilt on *every* note list change in the old code. It is now persisted in the
``graphs`` table under ``scope='notes'``: building is cheap but pointless on a
click, and the response shape is what the canvas draws.

Invalidation rule: ``app.storage.repo`` deletes the row inside the very
transaction that creates/updates/deletes a note or a folder, so a read after a
mutation rebuilds and a read without one is a single primary-key lookup. The row
is also treated as missing when ``NOTES_SCHEMA_VERSION`` changes, so editing the
derivation rules retires older payloads.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any

from app.core.db import (
    GRAPH_SCOPE_NOTES,
    NOTES_GRAPH_PROJECT_ID,
    get_conn,
    utcnow,
    write_conn,
)
from app.storage import repo

log = logging.getLogger(__name__)

# Bumped when the *topology rules* below change (a new relation, a different
# grouping). A stored payload with another version is rebuilt instead of served.
NOTES_SCHEMA_VERSION = 2

_REFERENCE = re.compile(
    r"[\w./\\-]+\.(?:tsx?|jsx?|py|rs|go|java|kt|swift|c|cpp|h|hpp|cs|vue|svelte|"
    r"html|css|scss|json|ya?ml|toml|md|sql|sh|ps1|rb|php)\b",
    re.IGNORECASE,
)


def _compose() -> dict[str, Any]:
    notes = sorted(repo.list_all_notes(), key=lambda note: note["id"])
    folders = {folder["id"]: folder["name"] for folder in repo.list_note_folders()}
    groups: dict[str, list[dict]] = defaultdict(list)
    references: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for note in notes:
        category = (
            f"folder:{note['folder_id']}" if note.get("folder_id") is not None else f"project:{note['project_id']}"
        )
        groups[category].append(note)
        for path in set(_REFERENCE.findall(note["content_md"])):
            normalized = path.replace("\\", "/").removeprefix("./")
            references[(note["project_id"], normalized)].append(note)

    nodes, communities = [], []
    for cid, (category, members) in enumerate(sorted(groups.items())):
        first = members[0]
        label = (
            folders.get(first["folder_id"], "未分类")
            if first.get("folder_id") is not None
            else f"{first.get('project_name') or '项目'} · 未分类"
        )
        for note in members:
            nodes.append(
                {
                    "id": f"note://{note['id']}",
                    "label": note["title"],
                    "kind": "note",
                    "path": f"note://{note['id']}",
                    "start_line": 0,
                    "end_line": 0,
                    "degree": 0,
                    "community": cid,
                    "community_label": label,
                    "note_id": note["id"],
                    "content_md": note["content_md"],
                    "project_name": note.get("project_name"),
                }
            )
        communities.append(
            {
                "id": cid,
                "label": label,
                "size": len(members),
                "hub": f"note://{first['id']}",
                "kind": "note",
                "pooled": False,
                "category": category,
            }
        )

    # Preserve the existing sparse chain: one link records a shared folder/file,
    # not a claim of semantic similarity between every pair in the category.
    links: dict[tuple[str, str, str], dict] = {}

    def connect(members: list[dict], relation: str, evidence: str) -> None:
        unique = sorted({note["id"] for note in members})
        for a, b in zip(unique, unique[1:], strict=False):
            source, target = f"note://{a}", f"note://{b}"
            key = (source, target, relation)
            edge = links.setdefault(
                key,
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "confidence": "EXTRACTED",
                    "evidence": [],
                },
            )
            edge["evidence"].append(evidence)

    for category, members in groups.items():
        connect(members, "same_folder" if category.startswith("folder:") else "same_project", category)
    for (_pid, path), members in references.items():
        connect(members, "shared_reference", path)
    by_id = {node["id"]: node for node in nodes}
    for edge in links.values():
        by_id[edge["source"]]["degree"] += 1
        by_id[edge["target"]]["degree"] += 1
    return {
        "nodes": nodes,
        "edges": list(links.values()),
        "communities": communities,
        "stats": {
            "files": 0,
            "symbols": len(nodes),
            "edges": len(links),
            "extracted": len(links),
            "inferred": 0,
            "communities": len(communities),
        },
    }


def _decode(row) -> dict[str, Any] | None:
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        log.warning("discarding unreadable notes graph payload", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def invalidate() -> None:
    """Drop the persisted notes graph; the next read rebuilds it.

    Normally the mutation path deletes the row in its own transaction (see
    ``app.storage.repo``); this is for callers that mutate notes outside repo.
    """
    with write_conn() as conn:
        conn.execute(
            "DELETE FROM graphs WHERE project_id = ? AND scope = ?",
            (NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES),
        )


def build(use_cache: bool = True) -> dict[str, Any]:
    """The notes graph, using the persisted copy unless it was invalidated."""
    if use_cache:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT payload, schema_version, node_count FROM graphs WHERE project_id = ? AND scope = ?",
                (NOTES_GRAPH_PROJECT_ID, GRAPH_SCOPE_NOTES),
            ).fetchone()
        if row is not None and int(row["schema_version"]) == NOTES_SCHEMA_VERSION:
            cached = _decode(row)
            if cached is not None:
                log.debug("notes graph cache hit (%s nodes)", row["node_count"])
                return cached
            log.info("notes graph payload unreadable, rebuilding")
        else:
            log.debug("notes graph cache miss, rebuilding")

    # Compose and persist while holding the process-wide write lock. Mutations in
    # ``app.storage.repo`` delete this row inside that same lock, so a note edited
    # between our read and our insert cannot be overwritten by a payload computed
    # from the older state: either the edit lands first (we read it), or it waits
    # and deletes the row we just wrote, forcing the next read to rebuild.
    with write_conn() as conn:
        payload = _compose()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        conn.execute(
            """INSERT INTO graphs
                   (project_id, scope, payload, built_hash, schema_version,
                    node_count, edge_count, built_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id, scope) DO UPDATE SET
                   payload = excluded.payload,
                   built_hash = excluded.built_hash,
                   schema_version = excluded.schema_version,
                   node_count = excluded.node_count,
                   edge_count = excluded.edge_count,
                   built_at = excluded.built_at""",
            (
                NOTES_GRAPH_PROJECT_ID,
                GRAPH_SCOPE_NOTES,
                body,
                None,
                NOTES_SCHEMA_VERSION,
                len(payload["nodes"]),
                len(payload["edges"]),
                utcnow(),
            ),
        )
    log.info(
        "built notes graph: %d nodes, %d edges, %d communities",
        len(payload["nodes"]),
        len(payload["edges"]),
        len(payload["communities"]),
    )
    return payload

