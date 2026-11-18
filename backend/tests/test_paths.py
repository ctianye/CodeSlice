"""Path safety helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.errors import InvalidInput
from app.services import paths

# An absolute path outside any project. ``Path("C:/...").is_absolute()`` is False
# on POSIX, so a Windows literal resolves *inside* the project there and never
# raises; the traversal case still needs a real absolute path on each platform.
ABSOLUTE_OUTSIDE = "C:/Windows/system.ini" if os.name == "nt" else "/etc/passwd"


def test_resolve_root_requires_directory(tmp_path: Path):
    with pytest.raises(InvalidInput):
        paths.resolve_root(str(tmp_path / "nope"))
    assert paths.resolve_root(str(tmp_path)) == tmp_path.resolve()


def test_resolve_in_project_allows_nested(tmp_path: Path):
    (tmp_path / "a").mkdir()
    target = tmp_path / "a" / "b.py"
    target.write_text("x = 1\n", encoding="utf-8")
    assert paths.resolve_in_project(tmp_path, "a/b.py") == target.resolve()


@pytest.mark.parametrize("bad", ["../escape.py", ABSOLUTE_OUTSIDE])
def test_resolve_in_project_rejects_traversal(tmp_path: Path, bad: str):
    (tmp_path / "inner").mkdir()
    with pytest.raises(InvalidInput):
        paths.resolve_in_project(tmp_path / "inner", bad)


def test_resolve_in_project_empty_returns_root(tmp_path: Path):
    assert paths.resolve_in_project(tmp_path, "") == tmp_path
    assert paths.resolve_in_project(tmp_path, None) == tmp_path


def test_relative_to_roundtrip(tmp_path: Path):
    (tmp_path / "d").mkdir()
    file = tmp_path / "d" / "x.py"
    file.write_text("", encoding="utf-8")
    assert paths.relative_to(tmp_path, file) == "d/x.py"


def test_is_ignored_matches_default_set():
    assert paths.is_ignored("node_modules")
    assert paths.is_ignored(".git")
    assert paths.is_ignored("dist-backend-linux")
    assert paths.is_ignored("release-linux")
    assert paths.is_ignored("release-clean")
    assert not paths.is_ignored("src")


@pytest.mark.parametrize(
    "name",
    ["release", "release-021", "release-linux-021", "release-clean", "linux-unpacked", "dist-electron"],
)
def test_build_output_dirs_are_ignored(name: str):
    """electron-builder renames its output dir; an exact match on "release" misses it.

    Bundled binaries under those trees used to enter the corpus and broke
    embedding for the whole project (see test_index_retrieval).
    """
    assert paths.is_ignored_dir(name)


@pytest.mark.parametrize("name", ["release-notes", "releases", "src", "desktop", "docs"])
def test_source_dirs_are_not_mistaken_for_build_output(name: str):
    assert not paths.is_ignored_dir(name)


def test_looks_binary_detects_blobs():
    assert paths.looks_binary(b"\x7fELF\x00\x00\x00")
    assert not paths.looks_binary(b"def main():\n    return 0\n")
