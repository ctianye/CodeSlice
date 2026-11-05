"""Chunker coverage: structure slices, fallbacks and language detection."""

from __future__ import annotations

from app.indexing import chunker, languages

PY_SAMPLE = '''"""Module docstring."""

import os


class Foo:
    """A demo class."""

    def bar(self, x):
        return x + 1

    def baz(self):
        return self.bar(2)


def top():
    return os.getcwd()
'''


def test_detects_language_from_extension():
    assert languages.detect_language("a/b.py", "") == "python"
    assert languages.detect_language("x.tsx", "") == "tsx"
    assert languages.detect_language("x.unknownext", "") == "text"
    assert languages.detect_language("Dockerfile", "") == "text"
    assert languages.detect_language("run", "#!/usr/bin/env python3\n") == "python"


def test_chunks_python_by_structure():
    chunks = chunker.chunk_file("pkg/sample.py", "python", PY_SAMPLE)
    symbols = [c["symbol"] for c in chunks]
    assert "Foo" in symbols
    assert "bar" in symbols
    assert "baz" in symbols
    assert "top" in symbols

    bar = next(c for c in chunks if c["symbol"] == "bar")
    assert bar["kind"] == "function"
    assert bar["parent_symbol"] == "Foo"
    assert bar["start_line"] == 9
    assert bar["end_line"] == 10
    assert "return x + 1" in bar["content"]
    assert bar["tokens"] > 0
    assert len(bar["hash"]) == 64

    foo = next(c for c in chunks if c["symbol"] == "Foo")
    assert foo["kind"] == "class"
    # The docstring directly under the class header is part of the slice.
    assert "A demo class." in foo["content"]


def test_chunk_ranges_are_inside_the_file():
    lines = PY_SAMPLE.splitlines()
    for chunk in chunker.chunk_file("pkg/sample.py", "python", PY_SAMPLE):
        assert 1 <= chunk["start_line"] <= chunk["end_line"] <= len(lines)
        for offset, text in enumerate(chunk["content"].splitlines()):
            assert text == lines[chunk["start_line"] - 1 + offset]


def test_unsupported_language_falls_back_to_text_bands():
    text = "\n".join(f"line {i}" for i in range(150))
    chunks = chunker.chunk_file("notes.weird", None, text)
    assert chunks
    assert all(c["kind"] == "block" for c in chunks)
    # FALLBACK_LINES is 60, so 150 lines produce three bands.
    assert len(chunks) == 3
    assert chunks[0]["start_line"] == 1
    assert chunks[-1]["end_line"] == 150


def test_markdown_splits_on_headings():
    text = "# Title\n\nintro\n\n## Section A\n\nalpha\n\n## Section B\n\nbeta\n"
    chunks = chunker.chunk_markdown("docs/x.md", text)
    symbols = [c["symbol"] for c in chunks]
    assert "Section A" in symbols
    assert "Section B" in symbols
    assert all(c["kind"] == "doc" for c in chunks)


def test_empty_file_yields_no_chunks():
    assert chunker.chunk_file("empty.py", "python", "") == []


def test_oversized_structure_is_split_or_truncated():
    body = "\n".join(f"    x_{i} = {i}" for i in range(400))
    text = f"def huge():\n{body}\n"
    chunks = chunker.chunk_file("huge.py", "python", text)
    assert chunks
    assert max(len(c["content"]) for c in chunks) <= 8000 + 64
