"""Opening an existing profile during an upgrade must preserve user content."""

from app.core import db


def test_existing_profile_survives_schema_upgrade_and_restart(tmp_path):
    path = tmp_path / "codeslice.db"
    # The previous release has notes and conversations, but no annotations table.
    with db.write_conn(path) as conn:
        conn.executescript("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                excluded TEXT, created_at TEXT NOT NULL, last_opened_at TEXT, indexed_at TEXT
            );
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'project', file_path TEXT,
                start_line INTEGER, end_line INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role TEXT NOT NULL, content TEXT NOT NULL, citations TEXT, meta TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id),
                conversation_id TEXT, title TEXT NOT NULL, content_md TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO projects VALUES (1, 'Existing project', 'C:/existing', NULL, 'before', NULL, NULL);
            INSERT INTO conversations VALUES (
                'chat', 1, 'My conversation', 'project', NULL, NULL, NULL, 'before', 'before'
            );
            INSERT INTO messages VALUES ('msg', 'chat', 'assistant', 'Existing answer', NULL, NULL, 'before');
            INSERT INTO notes VALUES (1, 1, 'chat', 'My note', '# Saved knowledge', 'before', 'before');
        """)

    db.init_db(path)
    with db.write_conn(path) as conn:
        assert conn.execute("SELECT title, content_md FROM notes").fetchone()[:] == ("My note", "# Saved knowledge")
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "Existing answer"
        assert conn.execute("SELECT title FROM conversations").fetchone()[0] == "My conversation"
        conn.execute(
            "INSERT INTO code_annotations "
            "(project_id, file_path, start_line, end_line, body, created_at, updated_at) "
            "VALUES (1, 'app.py', 2, 3, 'My annotation', 'after', 'after')"
        )

    # Subsequent startup must be idempotent and retain newly added content too.
    db.init_db(path)
    with db.get_conn(path) as conn:
        assert conn.execute("SELECT count(*) FROM notes").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT content FROM messages").fetchone()[0] == "Existing answer"
        assert conn.execute("SELECT body FROM code_annotations").fetchone()[0] == "My annotation"


def test_existing_annotations_get_a_title_during_upgrade(tmp_path):
    """The title column is additive; old annotation bodies stay intact."""
    path = tmp_path / "codeslice.db"
    with db.write_conn(path) as conn:
        conn.executescript("""
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
                excluded TEXT, created_at TEXT NOT NULL, last_opened_at TEXT, indexed_at TEXT
            );
            CREATE TABLE code_annotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                selected_code TEXT NOT NULL DEFAULT '',
                body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO projects VALUES (1, 'Existing project', 'C:/existing', NULL, 'before', NULL, NULL);
            INSERT INTO code_annotations
                (project_id, file_path, start_line, end_line, selected_code, body, created_at, updated_at)
            VALUES (1, 'app.py', 2, 3, 'code', '旧批注标题
正文', 'before', 'before');
        """)

    db.init_db(path)

    with db.get_conn(path) as conn:
        row = conn.execute("SELECT title, body FROM code_annotations").fetchone()
    assert row["title"] == "旧批注标题"
    assert row["body"] == "旧批注标题\n正文"
