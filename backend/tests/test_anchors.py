"""Anchor status transitions: unchanged / modified / moved / deleted."""

from __future__ import annotations

from pathlib import Path

from app.services import anchors
from app.storage import repo


def _make_anchor(project: dict, conversation: dict, rel: str, start: int, end: int) -> dict:
    anchor = anchors.create_for_citation(
        project_id=project["id"],
        conversation_id=conversation["id"],
        file_path=rel,
        start_line=start,
        end_line=end,
    )
    assert anchor is not None
    return anchor


def test_create_anchor_stores_snippet_and_hash(project: dict, conversation: dict):
    anchor = _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    assert anchor["snippet"].strip() == "def bar(self, x):\n        return x + 1"
    assert anchor["code_hash"]
    assert anchor["status"] == "unchanged"


def test_refresh_reports_unchanged(project: dict, conversation: dict):
    _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    stats = anchors.refresh(project["id"])
    assert stats["deleted"] == 0 and stats["moved"] == 0
    # Already-unchanged anchors still count, so the UI can show "N 已核对".
    assert stats["unchanged"] == 1
    assert repo.list_anchors(project["id"])[0]["status"] == "unchanged"


def test_same_citation_twice_does_not_duplicate_the_anchor(project: dict, conversation: dict):
    first = _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    second = _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    assert first["id"] == second["id"]
    assert len(repo.list_anchors(project["id"])) == 1


def test_distinct_ranges_keep_distinct_anchors(project: dict, conversation: dict):
    _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    _make_anchor(project, conversation, "pkg/sample.py", 12, 13)
    assert len(repo.list_anchors(project["id"])) == 2


def test_refresh_detects_deleted_file(project: dict, conversation: dict):
    _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    Path(project["path"], "pkg", "sample.py").unlink()
    stats = anchors.refresh(project["id"])
    assert stats["deleted"] == 1
    anchor = repo.list_anchors(project["id"])[0]
    assert anchor["status"] == "deleted"
    # The snippet is preserved so the UI can still show what was discussed.
    assert anchor["snippet"]


def test_refresh_detects_modified(project: dict, conversation: dict):
    _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    path = Path(project["path"], "pkg", "sample.py")
    text = path.read_text(encoding="utf-8").replace("return x + 1", "return x + 2")
    path.write_text(text, encoding="utf-8")
    stats = anchors.refresh(project["id"])
    assert stats["updated"] == 1
    assert repo.list_anchors(project["id"])[0]["status"] == "modified"


def test_refresh_detects_moved_block(project: dict, conversation: dict):
    _make_anchor(project, conversation, "pkg/sample.py", 9, 10)
    path = Path(project["path"], "pkg", "sample.py")
    original = path.read_text(encoding="utf-8").splitlines()
    block = original[8:10]  # the two lines of bar()
    rest = original[:8] + original[10:]
    moved = ["# header", "# second", ""] + rest + ["", "# moved below"] + block
    path.write_text("\n".join(moved) + "\n", encoding="utf-8")
    stats = anchors.refresh(project["id"])
    assert stats["moved"] == 1
    anchor = repo.list_anchors(project["id"])[0]
    assert anchor["status"] == "moved"
    assert anchor["start_line"] > 9


def test_extract_snippet_bounds(project: dict):
    root = Path(project["path"])
    assert anchors.extract_snippet(root, "pkg/sample.py", 1, 1).startswith('"""Sample')
    assert anchors.extract_snippet(root, "pkg/sample.py", 999, 1200) is None
    assert anchors.extract_snippet(root, "../escape.py", 1, 2) is None


def test_traversal_is_rejected(project: dict, conversation: dict):
    _make_anchor(project, conversation, "../../etc/passwd", 1, 2)
    anchor = repo.list_anchors(project["id"])[0]
    assert anchor["snippet"] is None
