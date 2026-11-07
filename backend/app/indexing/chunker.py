"""Code + document chunking.

Strategy, in order:
1. ``tree_sitter`` parse → emit one chunk per function / class / method / struct,
   merging node types listed in ``STRUCT_CONTROL`` when they nest (e.g. JS
   arrow constants stay with their declaration) and keeping a leading docstring
   or comment block attached.
2. Language without a grammar, or a file that fails to parse → line-banded text
   chunks (``FALLBACK_LINES``), preserving line ranges so citations still work.

Every chunk is dict-shaped:
``{path, language, kind, symbol, parent_symbol, start_line, end_line, content, hash, tokens}``
with 1-based inclusive line numbers.
"""

from __future__ import annotations

import hashlib
import re

from app.indexing.languages import get_parser

FALLBACK_LINES = 60
MAX_CHUNK_LINES = 220
MAX_CHUNK_CHARS = 8000

FUNCTION_TYPES = {
    "python": {"function_definition"},
    "javascript": {
        "function_declaration",
        "function_expression",
        "method_definition",
        "arrow_function",
        "generator_function_declaration",
    },
    "typescript": {
        "function_declaration",
        "method_definition",
        "function_expression",
        "arrow_function",
        "abstract_method_signature",
    },
    "tsx": {"function_declaration", "method_definition", "arrow_function", "function_expression"},
    "go": {"function_declaration", "method_declaration", "func_literal"},
    "rust": {"function_item", "function_signature_item"},
    "java": {"method_declaration", "constructor_declaration"},
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "bash": {"function_definition"},
}

CLASS_TYPES = {
    "python": {"class_definition"},
    "javascript": {"class_declaration", "class"},
    "typescript": {"class_declaration", "interface_declaration", "class"},
    "tsx": {"class_declaration", "interface_declaration", "class"},
    "go": {"type_declaration", "struct_type"},
    "rust": {"struct_item", "impl_item", "trait_item", "enum_item"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration"},
    "c": {"struct_specifier", "union_specifier"},
    "cpp": {"class_specifier", "struct_specifier", "namespace_definition"},
    "json": set(),
    "bash": set(),
}

DOC_TYPES = {"comment", "block_comment", "line_comment"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _node_name(node, source: bytes) -> str | None:
    """Best-effort symbol name for a syntax node."""
    for field in ("name", "declarator"):
        child = node.child_by_field_name(field)
        if child is not None:
            text = source[child.start_byte : child.end_byte].decode("utf-8", "replace").strip()
            if text:
                return text[:120]
    for child in node.children:
        if child.type in {"identifier", "type_identifier", "property_identifier"}:
            return source[child.start_byte : child.end_byte].decode("utf-8", "replace").strip()[:120]
    return None


def _enclosing_class(node) -> str | None:
    parent = node.parent
    depth = 0
    while parent is not None and depth < 8:
        if parent.type in {
            "class_definition",
            "class_declaration",
            "class",
            "impl_item",
            "struct_item",
            "interface_declaration",
            "namespace_definition",
        }:
            name = parent.child_by_field_name("name")
            if name is not None:
                return None  # name resolved by caller with source; placeholder
        parent = parent.parent
        depth += 1
    return None


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _slice(lines: list[str], start_line: int, end_line: int) -> str:
    start = max(0, start_line - 1)
    return "\n".join(lines[start:end_line])


def _make_chunk(
    path: str,
    language: str,
    kind: str,
    symbol: str | None,
    parent_symbol: str | None,
    start_line: int,
    end_line: int,
    content: str,
) -> dict:
    return {
        "path": path,
        "language": language,
        "kind": kind,
        "symbol": symbol,
        "parent_symbol": parent_symbol,
        "start_line": int(start_line),
        "end_line": int(end_line),
        "content": content,
        "hash": _hash(content),
        "tokens": _estimate_tokens(content),
    }


def _text_chunks(
    path: str,
    language: str,
    text: str,
    kind: str = "block",
    start_line: int = 1,
) -> list[dict]:
    lines = _lines(text)
    chunks: list[dict] = []
    if not lines:
        return chunks
    step = FALLBACK_LINES
    for start in range(0, len(lines), step):
        end = min(len(lines), start + step)
        content = "\n".join(lines[start:end])
        if not content.strip():
            continue
        chunks.append(
            _make_chunk(
                path,
                language,
                kind,
                None,
                None,
                start_line + start,
                start_line + end - 1,
                content[:MAX_CHUNK_CHARS],
            )
        )
    return chunks


def chunk_markdown(path: str, text: str) -> list[dict]:
    """Split Markdown by headings, keeping each section within the size budget."""
    lines = _lines(text)
    if not lines:
        return []
    chunks: list[dict] = []
    buf_start = 0
    heading = None
    current: list[str] = []

    def flush(end_idx: int) -> None:
        nonlocal current, buf_start
        content = "\n".join(current).strip("\n")
        if content:
            chunks.append(
                _make_chunk(
                    path,
                    "markdown",
                    "doc",
                    heading,
                    None,
                    buf_start + 1,
                    end_idx,
                    content[:MAX_CHUNK_CHARS],
                )
            )
        current = []

    for idx, line in enumerate(lines):
        if line.startswith("#") and current:
            flush(idx)
            buf_start = idx
            heading = line.lstrip("#").strip()[:120]
        if not current:
            buf_start = idx
        if line.startswith("#") and heading is None:
            heading = line.lstrip("#").strip()[:120]
        current.append(line)
        if len(current) >= MAX_CHUNK_LINES:
            flush(idx + 1)
            buf_start = idx + 1
    flush(len(lines))
    return chunks


def chunk_code(path: str, language: str, text: str) -> list[dict]:
    """Parse with Tree-sitter and emit structural chunks; fall back to text bands."""
    parser = get_parser(language)
    if parser is None:
        if language == "markdown":
            return chunk_markdown(path, text)
        return _text_chunks(path, language, text)

    source = text.encode("utf-8", "replace")
    tree = parser.parse(source)
    root = tree.root_node
    if root is None or root.has_error:
        # Broken/incomplete file: still useful, but line-band it.
        return _text_chunks(path, language, text)

    lines = _lines(text)
    fn_types = FUNCTION_TYPES.get(language, set())
    cls_types = CLASS_TYPES.get(language, set())
    chunks: list[dict] = []
    covered: list[tuple[int, int]] = []

    def line_of(node) -> tuple[int, int]:
        return node.start_point[0] + 1, node.end_point[0] + 1

    def walk(node, parent_symbol: str | None, depth: int) -> None:
        if depth > 6:
            return
        for child in node.children:
            ctype = child.type
            if ctype in cls_types:
                name = _node_name(child, source)
                start, end = line_of(child)
                if end - start + 1 <= MAX_CHUNK_LINES:
                    body = _slice(lines, start, end)
                    chunks.append(
                        _make_chunk(
                            path,
                            language,
                            "class",
                            name,
                            parent_symbol,
                            start,
                            end,
                            body[:MAX_CHUNK_CHARS],
                        )
                    )
                    covered.append((start, end))
                walk(child, name or parent_symbol, depth + 1)
            elif ctype in fn_types:
                name = _node_name(child, source)
                start, end = line_of(child)
                # Attach immediately preceding docstring/comment block.
                prev = child.prev_sibling
                while prev is not None and prev.type in DOC_TYPES and start - line_of(prev)[0] <= 3:
                    start = line_of(prev)[0]
                    prev = prev.prev_sibling
                if end - start + 1 <= MAX_CHUNK_LINES:
                    body = _slice(lines, start, end)
                    chunks.append(
                        _make_chunk(
                            path,
                            language,
                            "function",
                            name,
                            parent_symbol,
                            start,
                            end,
                            body[:MAX_CHUNK_CHARS],
                        )
                    )
                    covered.append((start, end))
                else:
                    walk(child, name or parent_symbol, depth + 1)
            else:
                walk(child, parent_symbol, depth + 1)

    walk(root, None, 0)

    if not chunks:
        return _text_chunks(path, language, text)

    # Keep each module-level gap separate so citations point at the actual lines.
    covered.sort()
    cursor = 1
    module_chunks: list[dict] = []

    def add_module_gap(first: int, last: int) -> None:
        while first <= last and not lines[first - 1].strip():
            first += 1
        while last >= first and not lines[last - 1].strip():
            last -= 1
        if first > last:
            return
        body = _slice(lines, first, last)
        if len(body) <= 40:
            return
        module_chunks.extend(
            _text_chunks(path, language, body, kind="module", start_line=first)
        )

    for start, end in covered:
        if start > cursor:
            add_module_gap(cursor, start - 1)
        cursor = max(cursor, end + 1)
    if cursor <= len(lines):
        add_module_gap(cursor, len(lines))
    chunks.extend(module_chunks)
    chunks.sort(key=lambda chunk: (chunk["start_line"], chunk["end_line"], chunk["kind"] != "module"))

    # Guard against single huge symbols.
    out: list[dict] = []
    for chunk in chunks:
        if chunk["end_line"] - chunk["start_line"] + 1 > MAX_CHUNK_LINES:
            out.extend(_text_chunks(chunk["path"], language, chunk["content"]))
        else:
            out.append(chunk)
    return out


_IMPORT_RE = re.compile(r"^(import |from |require\(|#include|use |using )", re.MULTILINE)


def chunk_file(path: str, language: str, text: str) -> list[dict]:
    if language == "markdown":
        return chunk_markdown(path, text)
    return chunk_code(path, language, text)
