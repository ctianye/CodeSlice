"""Workflow-node configuration API.

Two endpoints back the sidebar "工作流" panel: one read that returns the fixed
graph (nodes + edges + the last run's stats) and one write per node.

The edges are hard-coded here because the topology is deliberately not editable —
``graph.py`` builds it, and this is its mirror for display.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent import node_config
from app.api.deps import TokenDep

router = APIRouter(prefix="/agent/graph", tags=["agent"])

# Kept in sync with build_graph() in app/agent/graph.py.
EDGES: list[dict[str, Any]] = [
    {"from": "__start__", "to": "prepare"},
    {"from": "prepare", "to": "agent"},
    {"from": "agent", "to": "run_one", "label": "需要工具", "conditional": True},
    {"from": "agent", "to": "finalize", "label": "回答完成", "conditional": True},
    {"from": "run_one", "to": "agent", "label": "继续推理", "conditional": True},
    {"from": "run_one", "to": "run_one", "label": "还有调用", "conditional": True},
    {"from": "finalize", "to": "__end__"},
]


class NodeUpdate(BaseModel):
    # A partial patch: omitted keys keep their stored value.
    config: dict[str, Any]


@router.get("")
def get_graph(_: TokenDep) -> dict[str, Any]:
    """The whole workflow: node definitions, current config, run stats, edges."""
    return {"nodes": node_config.nodes_payload(), "edges": EDGES}


@router.put("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate, _: TokenDep) -> dict[str, Any]:
    """Persist one node's config and return it; takes effect on the next turn."""
    return node_config.save_node(node_id, body.config)
