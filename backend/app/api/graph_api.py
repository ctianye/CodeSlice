"""Workflow-node configuration API.

Two endpoints back the sidebar "工作流" panel: one read that returns the graph
(nodes + edges + the last run's stats) and one write per node.

The edges used to be a hand-maintained list mirroring ``build_graph()``, which
meant adding a node silently left the panel drawing the old topology. They are now
derived from the compiled graph itself, so the panel cannot disagree with what
actually runs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.agent import graph as agent_graph
from app.agent import node_config
from app.api.deps import TokenDep

router = APIRouter(prefix="/agent/graph", tags=["agent"])

# Conditional edges need a label; the panel shows it on the edge. Keys are
# "from->to" because one node can have several conditional targets. Whether an
# edge *is* conditional comes from LangGraph itself (``Edge.conditional``), so this
# map only supplies wording — it cannot disagree with the compiled graph about
# topology.
_EDGE_LABELS: dict[str, str] = {
    "agent->run_one": "需要工具",
    "agent->run_readonly_batch": "只读工具",
    "agent->finalize": "回答完成",
    "run_one->agent": "继续推理",
    "run_one->run_one": "还有调用",
    "run_readonly_batch->agent": "继续推理",
    "run_readonly_batch->run_readonly_batch": "还有只读调用",
}


def _edges() -> list[dict[str, Any]]:
    """The workflow topology, read from the compiled graph.

    ``build_graph()`` is cached process-wide and compiling is cheap after the first
    call, so this is safe to call per request. Nothing is invoked here.
    """
    try:
        drawn = agent_graph.build_graph().get_graph()
    except Exception:  # pragma: no cover - a broken graph is a bigger problem elsewhere
        return []
    edges: list[dict[str, Any]] = []
    for edge in drawn.edges:
        key = f"{edge.source}->{edge.target}"
        edges.append(
            {
                "from": edge.source,
                "to": edge.target,
                "label": _EDGE_LABELS.get(key, ""),
                "conditional": bool(edge.conditional),
            }
        )
    # Graph edge order is not meaningful to a reader; keep it stable.
    edges.sort(key=lambda item: (item["from"], item["to"]))
    return edges


class NodeUpdate(BaseModel):
    # A partial patch: omitted keys keep their stored value.
    config: dict[str, Any]


@router.get("")
def get_graph(_: TokenDep) -> dict[str, Any]:
    """The whole workflow: node definitions, current config, run stats, edges."""
    return {"nodes": node_config.nodes_payload(), "edges": _edges()}


@router.put("/nodes/{node_id}")
def update_node(node_id: str, body: NodeUpdate, _: TokenDep) -> dict[str, Any]:
    """Persist one node's config and return it; takes effect on the next turn."""
    return node_config.save_node(node_id, body.config)
