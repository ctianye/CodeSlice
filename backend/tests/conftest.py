"""Shared test fixtures.

Every test module gets an isolated data dir *and* an isolated SQLite file, so
nothing touches ``%LOCALAPPDATA%\\CodeSlice``. ``CODESLICE_DATA_DIR`` must be set
before ``app.core.config`` is imported, hence the env juggling at module level.

``CODESLICE_KEY_SERVICE`` matters just as much: without it the settings tests
would write to (and delete from) the developer's real Windows Credential Manager
entry, silently wiping the API key of a running installation.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="codeslice-tests-"))
os.environ["CODESLICE_DATA_DIR"] = str(_TMP / "data")
os.environ["CODESLICE_TOKEN"] = "test-token"
os.environ["CODESLICE_KEY_SERVICE"] = "codeslice-test"
os.environ.setdefault("CODESLICE_PORT", "0")

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.config import settings

TEST_TOKEN = "test-token"


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    settings.ensure_dirs()
    db.init_db()


@pytest.fixture
def conn_reset():
    """Wipe business tables between tests that create projects."""
    yield
    with db.write_conn() as conn:
        for table in (
            "code_anchors",
            "notes",
            "note_folders",
            "command_rules",
            "approvals",
            "messages",
            "conversations",
            "chunks_fts",
            "chunks",
            "indexed_files",
            "jobs",
            # Persisted graph caches are keyed by project id; a direct table wipe
            # would otherwise leave a graph that a later test could be served.
            "graphs",
            "projects",
        ):
            conn.execute(f"DELETE FROM {table}")


@pytest.fixture
def project(tmp_path: Path, conn_reset):
    """A tiny throwaway project with git-free files."""
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "sample.py").write_text(
        '"""Sample module."""\n'
        "\n"
        "import os\n"
        "\n"
        "\n"
        "class Foo:\n"
        '    """A demo class."""\n'
        "\n"
        "    def bar(self, x):\n"
        "        return x + 1\n"
        "\n"
        "    def baz(self):\n"
        "        return self.bar(2)\n"
        "\n"
        "\n"
        "def top():\n"
        "    return os.getcwd()\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("# Demo\n\nSome docs about bars and bazs.\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "skip.js").write_text("ignored();\n", encoding="utf-8")

    from app.storage import repo

    record = repo.create_project("proj", str(root))
    yield record


@pytest.fixture
def conversation(project: dict):
    from app.storage import repo

    return repo.create_conversation(
        project_id=project["id"],
        title="t",
        mode="snippet",
        file_path="pkg/sample.py",
        start_line=9,
        end_line=10,
    )


@pytest.fixture
def client():
    from app.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        test_client.headers.update({"X-Session-Token": TEST_TOKEN})
        yield test_client
