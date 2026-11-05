"""Query/text tokenization shared by the indexer and the retriever.

SQLite's ``unicode61`` tokenizer treats a whole run of CJK characters as a single
token, so a query like ``代码阅读`` can never match ``本地优先的代码阅读助手``.
Emitting overlapping CJK bigrams on both sides fixes that without pulling in a
segmentation library: ``本地优先的代码阅读助手`` becomes
``本地 地优 优先 先的 的代 代码 码阅 阅读 读助 助手``, and a two-character query
like ``入口`` matches wherever those two characters are adjacent.
"""

from __future__ import annotations

import re

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_RUN_RE = re.compile(rf"[A-Za-z_][A-Za-z0-9_]*|[{_CJK}]+")

MAX_QUERY_TOKENS = 24

# Bump when the tokenization scheme changes; ``core.db`` rebuilds chunks_fts then.
INDEX_VERSION = 2


def _cjk_bigrams(run: str) -> list[str]:
    if len(run) == 1:
        return [run]
    return [run[i : i + 2] for i in range(len(run) - 1)]


def search_tokens(text: str, *, limit: int | None = None) -> list[str]:
    """Split ``text`` into FTS-friendly tokens (ASCII words + CJK bigrams)."""
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _RUN_RE.finditer(text or ""):
        run = match.group(0)
        if run[0].isascii():
            token = run.lower()
            if len(token) < 2:
                continue
            parts = [token]
        else:
            parts = _cjk_bigrams(run)
        for part in parts:
            if part not in seen:
                seen.add(part)
                tokens.append(part)
        if limit and len(tokens) >= limit:
            return tokens[:limit]
    return tokens


def index_text(*parts: str) -> str:
    """Build the value stored in the FTS column: a bag of tokens, space separated."""
    tokens: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for token in search_tokens(part or ""):
            if token not in seen:
                seen.add(token)
                tokens.append(token)
    return " ".join(tokens)


def fts_query(text: str, limit: int = MAX_QUERY_TOKENS) -> str:
    """Turn a user question into a quoted FTS5 OR-query; empty string when unusable."""
    tokens = search_tokens(text, limit=limit)
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)
