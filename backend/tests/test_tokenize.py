"""Tokenizer / full-text behaviour.

SQLite's unicode61 tokenizer swallows whole CJK runs into one token, so the
index stores overlapping bigrams on both sides and matches on them. These tests
pin that contract: a Chinese question must find Chinese content, an English
word must survive case folding, and punctuation must never leak into the query.
"""

from __future__ import annotations

import pytest

from app.retrieval import tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("代码阅读", ["代码", "码阅", "阅读"]),
        ("入口", ["入口"]),
        ("口", ["口"]),
        ("FastAPI", ["fastapi"]),
        ("useChatStream 钩子", ["usechatstream", "钩子"]),
        ("", []),
        ("。。。", []),
        ("a b", []),  # single ASCII letters are dropped as noise
    ],
)
def test_search_tokens(text, expected):
    assert tokenize.search_tokens(text) == expected


def test_search_tokens_dedupes_within_one_text():
    assert tokenize.search_tokens("代码 代码") == ["代码"]


def test_search_tokens_limit():
    tokens = tokenize.search_tokens("本地优先的代码阅读助手", limit=3)
    assert tokens == ["本地", "地优", "优先"]


def test_index_text_joins_parts_and_dedupes():
    stored = tokenize.index_text("代码阅读", "README.md")
    assert stored.split() == ["代码", "码阅", "阅读", "readme", "md"]
    # The same token appearing in two parts must not be stored twice.
    assert tokenize.index_text("入口", "入口").split() == ["入口"]


def test_index_text_is_space_delimited_so_bigrams_never_merge():
    stored = tokenize.index_text("代码阅读")
    assert " 代" not in stored and "码阅" in stored.split()


def test_fts_query_quotes_every_token():
    query = tokenize.fts_query("FastAPI 入口")
    assert query == '"fastapi" OR "入口"'


def test_fts_query_keeps_operators_inside_quotes():
    # ``*``, ``:`` and parentheses are FTS operators; they must end up quoted as
    # literal text rather than being interpreted.
    query = tokenize.fts_query("x* OR (a:b) 索引")
    assert " OR " in query
    assert query.split(" OR ")[-1] == '"索引"'
    for part in query.split(" OR "):
        assert part.startswith('"') and part.endswith('"')


def test_fts_query_rejects_unusable_input():
    assert tokenize.fts_query("") == ""
    assert tokenize.fts_query("!!! ???") == ""


def test_fts_query_never_emits_sql_wildcards_as_operators():
    # ``*`` and ``:`` would be FTS syntax; every token is quoted instead.
    query = tokenize.fts_query("x* OR (a:b)")
    assert '"x"' not in query  # single letters are noise
    assert " column:" not in query


def test_roundtrip_content_is_findable_via_index_text():
    content = "本项目的入口在 backend/app/main.py"
    stored = tokenize.index_text(content)
    for token in tokenize.search_tokens("项目的入口"):
        assert f" {token} " in f" {stored} "
