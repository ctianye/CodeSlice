"""Runtime configuration for the local CodeSlice service.

The service is single-user and runs on 127.0.0.1 only. A session token is
generated at start-up (or taken from ``CODESLICE_TOKEN``) and must be sent by
the desktop shell with every API request.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "CodeSlice"
VERSION = "0.2.6"


def _default_data_dir() -> Path:
    env = os.environ.get("CODESLICE_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    token: str = field(default_factory=lambda: os.environ.get("CODESLICE_TOKEN") or "")
    host: str = os.environ.get("CODESLICE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("CODESLICE_PORT") or 0)
    max_file_bytes: int = 2 * 1024 * 1024
    max_tree_entries: int = 500
    index_batch_size: int = 32

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if not self.token:
            self.token = secrets.token_urlsafe(24)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "codeslice.db"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
