"""Ollama integration: local embedding presets, status probe and model pulls.

Ollama is an *optional* local runtime the user installs themselves. CodeSlice
never bundles it and never guesses a remote endpoint: the only address we talk
to is ``http://127.0.0.1:11434`` (overridable with ``CODESLICE_OLLAMA_URL`` for
tests), reached with ``trust_env=False`` so a system HTTP proxy cannot hijack a
loopback call.

Two surfaces are exposed here:

* :func:`status` — a fast (~1.5s) probe used by the settings screen.
* :func:`pull` — a generator of progress events that the router forwards as
  Server-Sent Events. The model name is validated against :data:`PRESETS`, so a
  caller can never make the backend fetch an arbitrary registry tag.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
PROBE_TIMEOUT = 1.5
PULL_CONNECT_TIMEOUT = 10.0

#: The only models this app knows how to configure. Keeping the list closed is
#: both a UX decision (two good defaults beat a registry browser) and a security
#: one: ``POST /api/pull`` forwards the name to Ollama, so an open model string
#: would let a caller make the local daemon fetch anything it can reach.
PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "bge-m3",
        "model": "bge-m3",
        "label": "bge-m3",
        "size": "1.2 GB",
        "size_bytes": 1_200_000_000,
        "context": "8K",
        "params": "567M",
        "description": "中文与代码的综合表现最均衡，长文档召回稳，适合第一次配置。",
        "requirement": "CPU 可跑",
    },
    {
        "id": "qwen3-embedding:0.6b",
        "model": "qwen3-embedding:0.6b",
        "label": "qwen3-embedding 0.6B",
        "size": "639 MB",
        "size_bytes": 639_000_000,
        "context": "32K",
        "params": "0.6B",
        "description": "代码检索更强、上下文更长，体积只有 bge-m3 的一半。",
        "requirement": "CPU 可跑",
    },
)

PRESET_MODELS: frozenset[str] = frozenset(item["model"] for item in PRESETS)

_PULL_LOCK = threading.Lock()
_PULL_STATE: dict[str, Any] = {
    "state": "idle",
    "model": None,
    "percent": 0.0,
    "detail": "",
    "total": 0,
    "completed": 0,
    "error": None,
    "sequence": 0,
}
_PULL_EVENTS: list[dict[str, Any]] = []


def base_url() -> str:
    """Loopback endpoint of the local Ollama daemon (no trailing slash)."""
    return (os.environ.get("CODESLICE_OLLAMA_URL") or DEFAULT_BASE_URL).rstrip("/")


def openai_base_url() -> str:
    """The OpenAI-compatible prefix stored in the embedding config."""
    return f"{base_url()}/v1"


def preset_for(model: str) -> dict[str, Any] | None:
    for item in PRESETS:
        if item["model"] == model:
            return item
    return None


def _client(timeout: float | httpx.Timeout = PROBE_TIMEOUT) -> httpx.Client:
    # trust_env=False keeps NO_PROXY/HTTP_PROXY out of the way: a loopback call
    # must never be routed through a proxy.
    return httpx.Client(timeout=timeout, trust_env=False)


def _installed_models(client: httpx.Client) -> list[str]:
    response = client.get(f"{base_url()}/api/tags")
    if response.status_code >= 400:
        return []
    payload = response.json()
    models: list[str] = []
    for item in payload.get("models") or []:
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name:
            models.append(name)
    return models


def status() -> dict[str, Any]:
    """Probe the local daemon; never raises, always answers in one shape."""
    result: dict[str, Any] = {
        "installed": False,
        "version": None,
        "models": [],
        "base_url": base_url(),
        "openai_base_url": openai_base_url(),
        "presets": list(PRESETS),
        "error": None,
    }
    try:
        with _client() as client:
            version_response = client.get(f"{base_url()}/api/version")
            if version_response.status_code >= 400:
                result["error"] = f"HTTP {version_response.status_code}"
                return result
            version = version_response.json().get("version")
            result["installed"] = True
            result["version"] = version if isinstance(version, str) else None
            result["models"] = _installed_models(client)
    except Exception as exc:  # connection refused, timeout, bad JSON
        result["error"] = f"{type(exc).__name__}: {exc}"[:200]
    return result


def is_installed(model: str, models: list[str]) -> bool:
    """Ollama reports ``bge-m3:latest`` even when you pull plain ``bge-m3``."""
    if model in models:
        return True
    return any(name == f"{model}:latest" for name in models)


def pull_status() -> dict[str, Any]:
    """Return the process-wide pull state so the UI can reconnect after remount."""
    with _PULL_LOCK:
        return {key: value for key, value in _PULL_STATE.items() if key != "sequence"}


def start_pull(model: str) -> None:
    """Start one background pull; disconnecting an SSE client does not stop it."""
    global _PULL_EVENTS
    if model not in PRESET_MODELS:
        raise ValueError(f"unsupported model: {model}")
    with _PULL_LOCK:
        if _PULL_STATE["state"] == "running":
            if _PULL_STATE["model"] == model:
                return
            raise RuntimeError(f"another model is downloading: {_PULL_STATE['model']}")
        _PULL_STATE.update(
            state="running",
            model=model,
            percent=0.0,
            detail="正在连接 Ollama…",
            total=0,
            completed=0,
            error=None,
            sequence=0,
        )
        _PULL_EVENTS = []

    def run() -> None:
        try:
            for event in pull(model):
                with _PULL_LOCK:
                    if event["type"] == "progress":
                        _PULL_STATE.update(
                            state="running",
                            percent=float(event.get("percent") or 0),
                            detail=str(event.get("detail") or event.get("status") or ""),
                            total=int(event.get("total") or 0),
                            completed=int(event.get("completed") or 0),
                        )
                    elif event["type"] == "error":
                        _PULL_STATE.update(state="error", error=str(event.get("detail") or "下载失败"))
                        _PULL_STATE["detail"] = _PULL_STATE["error"]
                    elif _PULL_STATE["state"] != "error":
                        _PULL_STATE.update(state="done", percent=100.0, detail="下载完成")
                    _PULL_STATE["sequence"] = int(_PULL_STATE["sequence"]) + 1
                    _PULL_EVENTS.append(dict(event))
        except Exception as exc:  # a worker failure must remain visible to a reconnecting UI
            with _PULL_LOCK:
                _PULL_STATE.update(
                    state="error",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                    detail=f"{type(exc).__name__}: {exc}"[:300],
                )
                _PULL_STATE["sequence"] = int(_PULL_STATE["sequence"]) + 1
                _PULL_EVENTS.append(
                    {"type": "error", "model": model, "detail": _PULL_STATE["error"]}
                )

    threading.Thread(target=run, name=f"ollama-pull-{model}", daemon=True).start()


def stream_pull_status(model: str) -> Iterator[dict[str, Any]]:
    """Stream snapshots from the background worker until it reaches a terminal state."""
    last_sequence = 0
    while True:
        with _PULL_LOCK:
            snapshot = dict(_PULL_STATE)
            events = list(_PULL_EVENTS[last_sequence:])
            last_sequence += len(events)
        if snapshot.get("model") != model:
            yield {"type": "error", "model": model, "detail": "下载任务已被替换"}
            yield {"type": "done", "model": model}
            return
        yield from events
        if snapshot["state"] in {"done", "error"} and last_sequence >= int(snapshot["sequence"]):
            if not events or events[-1].get("type") != "done":
                yield {"type": "done", "model": model}
            return
        time.sleep(0.25)


def pull(model: str) -> Iterator[dict[str, Any]]:
    """Stream download progress for one preset model.

    Yields dicts shaped like the rest of our SSE traffic: ``progress`` events
    carry ``total``/``completed``/``percent``, then exactly one ``done`` or
    ``error`` terminates the stream.
    """
    if model not in PRESET_MODELS:
        # Guarded again in the router (400 before the stream opens); the check
        # here keeps this module safe to call directly.
        yield {"type": "error", "detail": f"unsupported model: {model}"}
        yield {"type": "done", "model": model}
        return

    payload = {"model": model, "stream": True}
    url = f"{base_url()}/api/pull"
    started = False
    # Ollama interleaves byte-progress lines with pure status lines
    # ("verifying sha256 digest", "writing manifest") that carry no total. Holding
    # the last known percentage keeps the UI bar from snapping back to 0%.
    last_percent = 0.0
    try:
        with (
            _client(httpx.Timeout(PULL_CONNECT_TIMEOUT, read=None)) as client,
            client.stream("POST", url, json=payload) as response,
        ):
            if response.status_code >= 400:
                detail = response.read().decode("utf-8", "replace")[:300]
                yield {"type": "error", "model": model, "detail": f"HTTP {response.status_code}: {detail}"}
                yield {"type": "done", "model": model}
                return
            for raw in response.iter_lines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    yield {"type": "error", "model": model, "detail": str(chunk["error"])[:300]}
                    yield {"type": "done", "model": model}
                    return
                status_text = str(chunk.get("status") or "")
                total = _as_int(chunk.get("total"))
                completed = _as_int(chunk.get("completed"))
                if total and completed:
                    percent = round(min(completed, total) / total * 100, 1)
                    last_percent = percent
                else:
                    percent = last_percent
                started = True
                yield {
                    "type": "progress",
                    "model": model,
                    "status": status_text,
                    "detail": status_text,
                    "total": total,
                    "completed": completed,
                    "percent": percent,
                }
                if status_text == "success":
                    break
    except Exception as exc:
        log.info("ollama pull failed for %s: %s", model, exc)
        hint = "未连接到 Ollama（请确认已启动 ollama serve）" if not started else "下载中断"
        yield {"type": "error", "model": model, "detail": f"{hint}：{type(exc).__name__}: {exc}"[:300]}
        yield {"type": "done", "model": model}
        return

    yield {"type": "done", "model": model}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "PRESETS",
    "PRESET_MODELS",
    "base_url",
    "is_installed",
    "openai_base_url",
    "preset_for",
    "pull",
    "status",
]
