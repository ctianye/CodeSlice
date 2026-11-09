"""Markdown 预览相关：文件写回（PUT /file）与图片资源（GET /asset）。"""

from __future__ import annotations

import time
from pathlib import Path

from app.core.config import settings

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _file_url(project_id: int) -> str:
    return f"/api/projects/{project_id}/file"


def _asset_url(project_id: int) -> str:
    return f"/api/projects/{project_id}/asset"


def test_write_back_roundtrip(client, project):
    """读 md → 改内容 → 写回 → 再读，内容/大小/mtime 与返回体都对得上。"""
    read = client.get(_file_url(project["id"]), params={"path": "README.md"})
    assert read.status_code == 200
    original = read.json()
    assert original["language"] == "markdown"

    new_content = "# Demo\n\n改过的文档内容。\n\n- a\n- b\n"
    before = time.time()
    written = client.put(
        _file_url(project["id"]),
        json={"path": "README.md", "content": new_content},
    )
    assert written.status_code == 200
    body = written.json()

    # 响应与 GET /file 完全同构。
    assert set(body) == {"path", "language", "content", "size", "mtime", "truncated", "binary"}
    assert body["path"] == "README.md"
    assert body["language"] == "markdown"
    assert body["content"] == new_content
    assert body["size"] == len(new_content.encode("utf-8"))
    assert body["truncated"] is False
    assert body["binary"] is False
    assert body["mtime"] >= before - 1

    again = client.get(_file_url(project["id"]), params={"path": "README.md"}).json()
    assert again["content"] == new_content
    assert again["size"] == body["size"]
    assert again["mtime"] == body["mtime"]

    on_disk = (Path(project["path"]) / "README.md").read_text(encoding="utf-8")
    assert on_disk == new_content


def test_write_preserves_content_exactly(client, project):
    """不做换行转换，也不自动补尾换行。"""
    content = "line1\r\nline2\nline3"
    resp = client.put(_file_url(project["id"]), json={"path": "README.md", "content": content})
    assert resp.status_code == 200
    raw = (Path(project["path"]) / "README.md").read_bytes()
    assert raw == content.encode("utf-8")


def test_write_missing_file_is_404(client, project):
    resp = client.put(_file_url(project["id"]), json={"path": "nope.md", "content": "x"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "file not found: nope.md"
    assert not (Path(project["path"]) / "nope.md").exists()


def test_write_rejects_path_escape(client, project):
    resp = client.put(_file_url(project["id"]), json={"path": "../escape.md", "content": "x"})
    assert resp.status_code == 400


def test_write_rejects_oversized_content(client, project, monkeypatch):
    monkeypatch.setattr(settings, "max_file_bytes", 8)
    resp = client.put(_file_url(project["id"]), json={"path": "README.md", "content": "0123456789"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "file too large: 10 bytes"
    # 超限时磁盘内容不应被改动。
    assert "# Demo" in (Path(project["path"]) / "README.md").read_text(encoding="utf-8")


def test_write_conflict_leaves_file_untouched(client, project):
    original = client.get(_file_url(project["id"]), params={"path": "README.md"}).json()
    resp = client.put(
        _file_url(project["id"]),
        json={
            "path": "README.md",
            "content": "should not be written",
            "expected_mtime": original["mtime"] - 60,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "file changed on disk since it was opened"
    # 磁盘上保持原样（按字节比较，避免 Windows 文本模式的换行转换干扰）。
    raw = (Path(project["path"]) / "README.md").read_bytes().decode("utf-8")
    assert raw.replace("\r\n", "\n") == original["content"].replace("\r\n", "\n")


def test_write_with_matching_mtime_succeeds(client, project):
    current = client.get(_file_url(project["id"]), params={"path": "README.md"}).json()
    resp = client.put(
        _file_url(project["id"]),
        json={"path": "README.md", "content": "ok\n", "expected_mtime": current["mtime"]},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "ok\n"


def test_write_directory_is_400(client, project):
    resp = client.put(_file_url(project["id"]), json={"path": "pkg", "content": "x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "not a file: pkg"


def test_asset_serves_png_with_query_token(client, project):
    (Path(project["path"]) / "pic.png").write_bytes(PNG_BYTES)
    resp = client.get(
        _asset_url(project["id"]),
        params={"path": "pic.png", "token": "test-token"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.content == PNG_BYTES


def test_asset_rejects_bad_or_missing_token(client, project):
    (Path(project["path"]) / "pic.png").write_bytes(PNG_BYTES)
    wrong = client.get(_asset_url(project["id"]), params={"path": "pic.png", "token": "nope"})
    assert wrong.status_code == 401
    missing = client.get(_asset_url(project["id"]), params={"path": "pic.png"})
    assert missing.status_code == 401


def test_asset_rejects_non_image(client, project):
    resp = client.get(
        _asset_url(project["id"]),
        params={"path": "pkg/sample.py", "token": "test-token"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported asset type: .py"


def test_asset_missing_file_is_404(client, project):
    resp = client.get(
        _asset_url(project["id"]),
        params={"path": "ghost.png", "token": "test-token"},
    )
    assert resp.status_code == 404
