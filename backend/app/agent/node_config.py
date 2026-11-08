"""Configuration knobs for the agent workflow nodes.

The graph topology is fixed on purpose: one turn always runs
``prepare → agent → (run_one → agent)* → finalize``. What a user can tune is how
each node behaves, and those values live in the ``settings`` table under a single
JSON key so a change applies to the next turn without restarting the app — every
node reads its merged config when it runs.

``NODES`` is also the UI schema: ``GET /api/agent/graph`` serialises
``label``/``kind``/``editable`` verbatim, so adding a knob here is enough for the
workflow panel to render a field for it.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import Any

from app.core.errors import InvalidInput, NotFound
from app.storage import repo

log = logging.getLogger(__name__)

SETTINGS_KEY = "agent.node_config"

# Ordered as the graph runs; the panel renders them top to bottom.
NODES: dict[str, dict[str, Any]] = {
    "prepare": {
        "label": "准备上下文",
        "kind": "input",
        "description": "组装 system prompt、检索或读取选中代码",
        "editable": {
            "retrieval_top_k": {"type": "int", "min": 1, "max": 20},
            "include_git_branch": {"type": "bool"},
        },
        "defaults": {"retrieval_top_k": 6, "include_git_branch": True},
    },
    "agent": {
        "label": "模型推理",
        "kind": "llm",
        "description": "流式生成回答，必要时发起工具调用",
        "editable": {
            "model": {"type": "string"},
            "temperature": {"type": "float", "min": 0, "max": 2},
            "max_tokens": {"type": "int", "min": 1024, "max": 262144},
            "extra_prompt": {"type": "text"},
            "max_tool_rounds": {"type": "int", "min": 1, "max": 30},
        },
        "defaults": {
            "model": "",
            "temperature": 0.2,
            "max_tokens": 32768,
            "extra_prompt": "",
            "max_tool_rounds": 10,
        },
    },
    "run_one": {
        "label": "执行工具",
        "kind": "tool",
        "description": "逐个执行工具调用，危险操作需要授权",
        "editable": {
            "max_tool_rounds": {"type": "int", "min": 1, "max": 30},
            "auto_approve_readonly_tools": {"type": "bool"},
        },
        "defaults": {"max_tool_rounds": 10, "auto_approve_readonly_tools": True},
    },
    "finalize": {
        "label": "收尾",
        "kind": "output",
        "description": "保存回答、重拟占位标题",
        "editable": {"auto_retitle": {"type": "bool"}},
        "defaults": {"auto_retitle": True},
    },
}

_LOCK = threading.Lock()
# Last-run numbers per node. Process local on purpose: they describe this
# session's runs, not something worth persisting across restarts.
_STATS: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# reading / writing
# --------------------------------------------------------------------------- #
def _raw() -> dict[str, Any]:
    stored = repo.get_setting(SETTINGS_KEY, {})
    return stored if isinstance(stored, dict) else {}


def _merge(node: dict[str, Any], saved: Any) -> dict[str, Any]:
    """Defaults overlaid with the keys we know, so an old row never breaks a node."""
    config = dict(node["defaults"])
    if isinstance(saved, dict):
        for key in config:
            if key in saved:
                config[key] = saved[key]
    return config


def load() -> dict[str, dict[str, Any]]:
    """Merged config for every node; missing or legacy keys fall back to defaults."""
    raw = _raw()
    return {node_id: _merge(node, raw.get(node_id)) for node_id, node in NODES.items()}


def get_config(node_id: str) -> dict[str, Any]:
    """One node's effective config, as read by the graph at run time."""
    if node_id not in NODES:
        raise NotFound(f"unknown node: {node_id}")
    return _merge(NODES[node_id], _raw().get(node_id))


def save_node(node_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate a partial patch, merge it and persist. Returns the public node."""
    cleaned = validate(node_id, config)
    merged = load()
    merged[node_id] = {**merged[node_id], **cleaned}
    repo.set_setting(SETTINGS_KEY, merged)
    return get_node(node_id, merged[node_id])


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(node_id: str, config: Any) -> dict[str, Any]:
    """Check a patch against the node schema and return only the known keys."""
    node = NODES.get(node_id)
    if node is None:
        raise NotFound(f"unknown node: {node_id}")
    if not isinstance(config, dict):
        raise InvalidInput("config must be an object")
    editable: dict[str, dict[str, Any]] = node["editable"]
    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        spec = editable.get(key)
        if spec is None:
            raise InvalidInput(f"unknown config key for {node_id}: {key}")
        cleaned[key] = _check(node_id, key, spec, value)
    return cleaned


def _check(node_id: str, key: str, spec: dict[str, Any], value: Any) -> Any:
    kind = spec["type"]
    where = f"{node_id}.{key}"
    if kind == "bool":
        if not isinstance(value, bool):
            raise InvalidInput(f"{where} must be a boolean")
        return value
    if kind == "int":
        # bool is an int subclass; "true" for a number field is a mistake, not a 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidInput(f"{where} must be an integer")
        return _in_range(where, spec, value)
    if kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidInput(f"{where} must be a number")
        return _in_range(where, spec, float(value))
    if kind == "string":
        if not isinstance(value, str):
            raise InvalidInput(f"{where} must be a string")
        return value.strip()
    if kind == "text":
        if not isinstance(value, str):
            raise InvalidInput(f"{where} must be text")
        return value.rstrip()
    raise InvalidInput(f"unsupported field type for {where}")


def _in_range(where: str, spec: dict[str, Any], value: Any) -> Any:
    low = spec.get("min")
    high = spec.get("max")
    if low is not None and value < low:
        raise InvalidInput(f"{where} must be >= {low}")
    if high is not None and value > high:
        raise InvalidInput(f"{where} must be <= {high}")
    return value


# --------------------------------------------------------------------------- #
# run stats
# --------------------------------------------------------------------------- #
def record_run(node_id: str, **stats: Any) -> None:
    """Remember the last run's numbers for the panel. Never raises."""
    try:
        entry = {key: value for key, value in stats.items() if value is not None}
        entry["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        with _LOCK:
            _STATS[node_id] = entry
    except Exception:  # stats are cosmetic: never fail a turn over them
        log.debug("could not record stats for %s", node_id, exc_info=True)


def stats_for(node_id: str) -> dict[str, Any]:
    with _LOCK:
        return dict(_STATS.get(node_id) or {})


# --------------------------------------------------------------------------- #
# HTTP payloads
# --------------------------------------------------------------------------- #
def _editable_public(node: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {key: dict(spec) for key, spec in node["editable"].items()}


def get_node(node_id: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """One node in the shape ``GET /api/agent/graph`` documents."""
    node = NODES.get(node_id)
    if node is None:
        raise NotFound(f"unknown node: {node_id}")
    return {
        "id": node_id,
        "label": node["label"],
        "kind": node["kind"],
        "description": node["description"],
        "editable": _editable_public(node),
        # Extra key on purpose: the panel's "恢复默认" needs the values without a
        # second round trip, and defaults belong to the backend.
        "defaults": dict(node["defaults"]),
        "config": dict(config) if config is not None else get_config(node_id),
        "stats": stats_for(node_id),
    }


def nodes_payload() -> list[dict[str, Any]]:
    return [get_node(node_id) for node_id in NODES]
