"""The notes-only graph. Both the canvas and AI use this exact topology."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.storage import repo

_REFERENCE = re.compile(
    r"[\w./\\-]+\.(?:tsx?|jsx?|py|rs|go|java|kt|swift|c|cpp|h|hpp|cs|vue|svelte|"
    r"html|css|scss|json|ya?ml|toml|md|sql|sh|ps1|rb|php)\b",
    re.IGNORECASE,
)


def build() -> dict[str, Any]:
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
