"""FastAPI application for the local CodeSlice service.

Startup contract (the Electron main process relies on it):
  * binds 127.0.0.1 only
  * when ``CODESLICE_PORT`` is 0 a free port is chosen
  * prints one line ``CODESLICE_READY {"port": ..., "token": ...}`` on stdout
    once it is actually listening, so the shell can connect without guessing
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import conversations, graph_api, indexing, notes, projects, system, terminal
from app.api import settings as settings_api
from app.core.config import APP_NAME, VERSION, settings
from app.core.db import init_db
from app.core.errors import AppError
from app.indexing.indexer import stop_project_watchers
from app.services import notegen
from app.storage import repo

log = logging.getLogger("codeslice")

DEV_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "file://",
]


def _configure_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    with suppress(OSError):
        handlers.append(logging.FileHandler(settings.logs_dir / "backend.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    _configure_logging()
    settings.ensure_dirs()
    init_db()
    # Opening the app is a good moment to drop conversations the user started and
    # never typed into; only ones older than an hour go, so a half-written draft
    # survives a restart.
    try:
        removed = repo.purge_empty_conversations(older_than_minutes=60)
        if removed:
            log.info("removed %d abandoned conversation(s)", removed)
    except Exception:  # cleanup must never block startup
        log.warning("empty-conversation cleanup failed", exc_info=True)
    # Conversations from before titles came from the answer keep their placeholder
    # forever, because only a new turn re-titles a conversation. Fixing them once
    # here costs one pass over the table and leaves user-typed titles alone.
    try:
        retitled = notegen.backfill_conversation_titles()
        if retitled:
            log.info("retitled %d conversation(s) from their answers", retitled)
    except Exception:  # a stale title is cosmetic, never a startup failure
        log.warning("conversation title backfill failed", exc_info=True)
    log.info("%s %s starting, data_dir=%s", APP_NAME, VERSION, settings.data_dir)
    yield
    stop_project_watchers()
    log.info("shutting down")


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan, docs_url="/api/docs")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):\d+$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    for module in (
        system.router,
        projects.router,
        indexing.router,
        settings_api.router,
        conversations.router,
        notes.router,
        terminal.router,
        graph_api.router,
    ):
        app.include_router(module, prefix="/api")

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built renderer when it exists (production shell)."""
    candidates: list[Path] = []
    env = os.environ.get("CODESLICE_FRONTEND_DIST")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parent.parent / "frontend" / "dist",  # repo layout
            here.parent.parent.parent / "frontend" / "dist",  # packaged layout
        ]
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            app.mount("/", StaticFiles(directory=str(candidate), html=True), name="frontend")
            log.info("serving frontend from %s", candidate)
            return


app = create_app()


def _pick_port(preferred: int) -> int:
    if preferred:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


READY_PREFIX = "CODESLICE_READY"


def main(argv: list[str] | None = None) -> Any:
    import uvicorn

    argv = argv if argv is not None else sys.argv[1:]
    port = _pick_port(settings.port)

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=port,
        log_level="info",
        access_log=False,
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="codeslice-uvicorn", daemon=True)
    thread.start()

    # Announce only once the socket is really accepting connections: the desktop
    # shell launches this process and has no other way to learn port + token.
    deadline = time.monotonic() + 60.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)

    if not server.started:
        print(f"{READY_PREFIX} error: server failed to start", file=sys.stderr, flush=True)
        return 1

    actual_port = port
    with suppress(Exception):
        sockets = server.servers[0].sockets
        if sockets:
            actual_port = int(sockets[0].getsockname()[1])

    announcement = {"port": actual_port, "token": settings.token, "version": VERSION}
    print(f"{READY_PREFIX} {json.dumps(announcement)}", flush=True)

    while thread.is_alive():
        thread.join(0.5)
    return 0


if __name__ == "__main__":
    main()
