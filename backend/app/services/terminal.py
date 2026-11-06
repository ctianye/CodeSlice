"""Streaming shell execution for the integrated terminal.

The terminal is a *local developer convenience*, not a security boundary: it runs
the user's own commands with the user's own privileges. What it does enforce is
the project-root working directory and the dangerous-command denylist, so a
stray ``cd ..`` or a paste of ``rm -rf /`` fails loudly instead of doing damage.

Output is streamed while the process runs — two reader threads push lines into a
queue and the generator drains it, so a long ``pytest`` shows progress instead of
appearing all at once when it finishes.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.services import sandbox

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300
MAX_OUTPUT_BYTES = 200 * 1024
TAIL_LINES = 40

# Processes currently running, so ``POST /terminal/cancel`` and a client
# disconnect can actually stop the child instead of leaving it spinning.
_RUNNING: dict[int, set[subprocess.Popen[str]]] = {}
_RUNNING_LOCK = threading.Lock()


def _register(project_id: int | None, proc: subprocess.Popen[str]) -> None:
    if project_id is None:
        return
    with _RUNNING_LOCK:
        _RUNNING.setdefault(project_id, set()).add(proc)


def _unregister(project_id: int | None, proc: subprocess.Popen[str]) -> None:
    if project_id is None:
        return
    with _RUNNING_LOCK:
        group = _RUNNING.get(project_id)
        if group is None:
            return
        group.discard(proc)
        if not group:
            _RUNNING.pop(project_id, None)


def _kill(proc: subprocess.Popen[str]) -> None:
    with contextlib.suppress(OSError):  # already gone
        proc.kill()


def cancel(project_id: int) -> int:
    """Kill every terminal process still running for one project.

    Returns how many were signalled. The streams themselves notice the dead
    child, emit their final ``exit`` frame and finish normally.
    """
    with _RUNNING_LOCK:
        procs = list(_RUNNING.get(int(project_id), ()))
    for proc in procs:
        _kill(proc)
    return len(procs)


def has_running(project_id: int) -> bool:
    with _RUNNING_LOCK:
        return bool(_RUNNING.get(int(project_id)))


def _pump(stream: Any, name: str, sink: queue.Queue[tuple[str, str] | None]) -> None:
    """Read one pipe line by line until EOF, then signal completion."""
    try:
        for line in iter(stream.readline, ""):
            sink.put((name, line))
    except (OSError, ValueError):  # pipe closed under us
        pass
    finally:
        with contextlib.suppress(OSError, ValueError):
            stream.close()
        sink.put(None)


def run_stream(
    root: Path,
    command: str,
    cwd: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    project_id: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield ``start``/``output``/``exit``/``error`` frames for one command."""
    cwd_label = (cwd or "").strip() or "."
    yield {"type": "start", "command": command, "cwd": cwd_label}

    try:
        sandbox.check_command(command)
        workdir = sandbox.resolve_cwd(root, cwd)
    except AppError as exc:
        yield {"type": "error", "detail": exc.detail}
        return

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        yield {"type": "error", "detail": f"命令无法启动：{exc}"}
        return

    _register(project_id, proc)
    lines: queue.Queue[tuple[str, str] | None] = queue.Queue()
    threads = [
        threading.Thread(target=_pump, args=(proc.stdout, "stdout", lines), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, "stderr", lines), daemon=True),
    ]
    for thread in threads:
        thread.start()

    emitted = 0
    truncated = False
    tail: deque[str] = deque(maxlen=TAIL_LINES)
    timed_out = False
    pending = len(threads)
    deadline = started + max(1, int(timeout))

    try:
        while pending > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _kill(proc)
                break
            try:
                item = lines.get(timeout=min(0.2, max(0.01, remaining)))
            except queue.Empty:
                continue
            if item is None:
                pending -= 1
                continue
            name, raw = item
            text = raw.rstrip("\r\n")
            if not text:
                continue
            tail.append(text)
            if truncated:
                continue  # keep draining the pipes, drop the surplus
            size = len((text + "\n").encode("utf-8", errors="replace"))
            if emitted + size > MAX_OUTPUT_BYTES:
                truncated = True
                continue
            emitted += size
            yield {"type": "output", "stream": name, "text": text}
    except GeneratorExit:
        # Client closed the panel / aborted the fetch: do not leave the child.
        _kill(proc)
        raise
    finally:
        _unregister(project_id, proc)

    if timed_out:
        yield {"type": "error", "detail": f"命令超时（{int(timeout)} 秒）已终止"}

    code = proc.wait()
    duration_ms = int((time.monotonic() - started) * 1000)
    yield {"type": "exit", "code": code, "duration_ms": duration_ms, "truncated": truncated}

    if project_id is not None:
        try:
            sandbox.record(
                project_id=project_id,
                command=command,
                cwd=sandbox.cwd_label(root, workdir),
                source="user",
                exit_code=code,
                duration_ms=duration_ms,
                output_tail="\n".join(tail),
            )
        except Exception:  # history is best effort; never break the stream
            log.debug("terminal history record failed", exc_info=True)


__all__ = ["DEFAULT_TIMEOUT", "MAX_OUTPUT_BYTES", "cancel", "has_running", "run_stream"]
