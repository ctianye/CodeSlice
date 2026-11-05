"""PyInstaller entry point for the CodeSlice local backend.

This module is *only* a launcher: it imports the real application package
(``backend/app``) and serves it with uvicorn. Keeping it separate from
``app/main.py`` means the frozen build does not have to guess how the app is
constructed, and it lets us own the start-up handshake.

Start-up handshake
------------------
When the server is actually accepting connections we print one single line to
stdout::

    CODESLICE_READY {"port": 8123, "token": "..."}

The Electron main process (and ``packaging/build_backend.ps1``) wait for that
line to learn the real port when ``CODESLICE_PORT=0`` (ephemeral port) and the
per-run session token.

Environment
-----------
CODESLICE_PORT      0 = pick a free port (default 0)
CODESLICE_HOST      127.0.0.1 only
CODESLICE_TOKEN     fixed token (otherwise a random one is generated)
CODESLICE_DATA_DIR  data directory (default %LOCALAPPDATA%\\CodeSlice)
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
import time
import traceback
from pathlib import Path

READY_PREFIX = "CODESLICE_READY"
ERROR_PREFIX = "CODESLICE_ERROR"


def _log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------
# Self-test mode
# --------------------------------------------------------------------------
# ``codeslice-backend.exe --selftest`` (or CODESLICE_SELFTEST=1) imports the
# app and every bundled tree-sitter grammar, then exits. This is the only cheap
# way to prove the compiled grammars survived packaging: a missing grammar does
# not crash the server, it silently degrades indexing to plain-text chunks.
SELFTEST_LANGUAGES = (
    "python", "javascript", "typescript", "tsx", "go", "rust",
    "java", "c", "cpp", "json", "bash",
)


def _selftest() -> int:
    _bootstrap_sys_path()
    failures: list[str] = []

    try:
        _load_app()
        _log("CODESLICE_SELFTEST app: ok")
    except SystemExit as exc:
        _log(f"CODESLICE_SELFTEST app: FAIL {exc}")
        failures.append("app")

    try:
        from app.indexing.languages import get_parser  # noqa: F401
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        get_parser = None  # type: ignore[assignment]

    for language in SELFTEST_LANGUAGES:
        try:
            if get_parser is None:
                raise RuntimeError("app.indexing.languages unavailable")
            parser = get_parser(language)
            if parser is None:
                raise RuntimeError("get_parser() returned None (grammar missing)")
            # Parsing a trivial snippet proves the compiled capsule actually loads.
            tree = parser.parse(b"x = 1\n")
            if tree.root_node is None:  # pragma: no cover
                raise RuntimeError("parser produced no tree")
            _log(f"CODESLICE_SELFTEST grammar {language}: ok")
        except Exception as exc:  # noqa: BLE001
            _log(f"CODESLICE_SELFTEST grammar {language}: FAIL {exc}")
            failures.append(language)

    # Keyring must resolve a real Windows backend, not the null/fail backend.
    try:
        import keyring

        backend = keyring.get_keyring()
        name = type(backend).__name__
        _log(f"CODESLICE_SELFTEST keyring: {name}")
        if "fail" in name.lower() or "null" in name.lower():
            failures.append(f"keyring:{name}")
    except Exception as exc:  # noqa: BLE001
        _log(f"CODESLICE_SELFTEST keyring: FAIL {exc}")
        failures.append("keyring")

    if failures:
        _log(f"{ERROR_PREFIX} selftest failed: {', '.join(failures)}")
        return 1
    _log(f"{READY_PREFIX} selftest ok")
    return 0


def _bootstrap_sys_path() -> None:
    """Make ``app`` importable both from source and from the frozen bundle."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # one-dir layout: <dist>/codeslice-backend/codeslice-backend.exe
        # modules live in <dist>/codeslice-backend/_internal (PyInstaller >= 6)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass))
        candidates.extend([exe_dir / "_internal", exe_dir, exe_dir.parent])
    else:
        here = Path(__file__).resolve().parent
        candidates.extend([here.parent / "backend", here])

    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved = str(candidate)
        if resolved not in sys.path:
            sys.path.insert(0, resolved)

    if getattr(sys, "frozen", False):
        # PyInstaller puts collected data next to the bundle; nothing else needed.
        pass


def _load_app():
    """Import the FastAPI application, tolerating ``app`` or ``create_app()``."""
    import importlib

    try:
        module = importlib.import_module("app.main")
    except ModuleNotFoundError as exc:  # pragma: no cover - build-time failure
        raise SystemExit(
            f"{ERROR_PREFIX} cannot import app.main ({exc}). "
            "backend/ must be on the bundle path and contain app/main.py."
        ) from exc

    app = getattr(module, "app", None)
    if app is None:
        factory = getattr(module, "create_app", None)
        if callable(factory):
            app = factory()
    if app is None:
        raise SystemExit(
            f"{ERROR_PREFIX} app.main defines neither 'app' nor a create_app() factory."
        )
    return app


def _read_settings():
    """Return (host, port, token) from the app's own settings module."""
    host = os.environ.get("CODESLICE_HOST", "127.0.0.1")
    port = int(os.environ.get("CODESLICE_PORT") or 0)
    token = os.environ.get("CODESLICE_TOKEN") or ""

    try:
        from app.core.config import settings  # type: ignore[import-not-found]

        host = getattr(settings, "host", host) or host
        port = int(getattr(settings, "port", port) or 0)
        token = getattr(settings, "token", token) or token
    except Exception:  # noqa: BLE001 - settings module is optional at this point
        traceback.print_exc()

    return host, port, token


def main() -> int:
    multiprocessing.freeze_support()

    if "--selftest" in sys.argv[1:] or os.environ.get("CODESLICE_SELFTEST") == "1":
        return _selftest()

    _bootstrap_sys_path()

    app = _load_app()
    host, port, token = _read_settings()

    import uvicorn

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.environ.get("CODESLICE_LOG_LEVEL", "info"),
        access_log=False,
        # The desktop shell kills the process; graceful signal handling on
        # Windows is unreliable, so let the lifespan shutdown be best effort.
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="codeslice-uvicorn", daemon=True)
    thread.start()

    deadline = time.monotonic() + 60.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)

    if not server.started:
        _log(f"{ERROR_PREFIX} server failed to start within 60s")
        return 1

    actual_port = port
    try:
        sockets = server.servers[0].sockets  # type: ignore[index]
        if sockets:
            actual_port = int(sockets[0].getsockname()[1])
    except Exception:  # noqa: BLE001 - port comes from config if we cannot introspect
        actual_port = config.port

    _log(f"{READY_PREFIX} {json.dumps({'port': actual_port, 'token': token})}")

    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 - always leave a parseable failure line
        traceback.print_exc()
        _log(f"{ERROR_PREFIX} unhandled exception during start-up")
        raise SystemExit(1) from None
