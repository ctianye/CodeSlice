"""Hybrid retrieval: FTS5 keyword search + vector cosine similarity fused with
Reciprocal Rank Fusion (RRF). Falls back to keyword-only when no vectors exist.

The ``VectorStore`` protocol keeps the storage layer swappable (numpy/SQLite now,
Chroma later) without touching call sites.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from app.core.db import get_conn
from app.models import embeddings as emb
from app.retrieval import tokenize

RRF_K = 60
FUZZY_LIMIT = 2000


class VectorStore(Protocol):
    def search(self, project_id: int, query_vector: list[float], limit: int) -> list[tuple[int, float]]: ...


class SqliteVectorStore:
    """Brute-force cosine over vectors stored in the chunks table."""

    def __init__(self, project_id: int) -> None:
        self.project_id = project_id
        model_name = emb.embedding_model_name()
        with get_conn() as conn:
            if model_name:
                rows = conn.execute(
                    "SELECT id, vector FROM chunks "
                    "WHERE project_id = ? AND vector IS NOT NULL AND embed_model = ?",
                    (project_id, model_name),
                ).fetchall()
            else:
                rows = []
        self._vectors = [(r["id"], emb.unpack(r["vector"])) for r in rows]

    def search(self, query_vector: list[float], limit: int = 20) -> list[tuple[int, float]]:
        if not query_vector or not self._vectors:
            return []
        qn = math.sqrt(sum(v * v for v in query_vector)) or 1.0
        scored: list[tuple[int, float]] = []
        for chunk_id, vector in self._vectors:
            if len(vector) != len(query_vector):
                continue
            dot = sum(a * b for a, b in zip(query_vector, vector, strict=False))
            vn = math.sqrt(sum(v * v for v in vector)) or 1.0
            scored.append((chunk_id, dot / (qn * vn)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]


def _fts_query(text: str) -> str:
    """Sanitize a user query into an FTS5 OR-query of indexed tokens."""
    return tokenize.fts_query(text)


def _row_to_chunk(row: Any, score: float, retriever: str) -> dict[str, Any]:
    return {
        "chunk_id": row["id"],
        "path": row["path"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "symbol": row["symbol"],
        "kind": row["kind"],
        "language": row["language"],
        "content": row["content"],
        "score": round(float(score), 5),
        "retriever": retriever,
    }


def keyword_search(project_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
    match = _fts_query(query)
    tokens = tokenize.search_tokens(query, limit=tokenize.MAX_QUERY_TOKENS)
    if not match or not tokens:
        return []
    # Pull a wider candidate set: bm25 alone ranks a long document that repeats
    # one common token above a short chunk that covers the whole query.
    candidates = max(limit * 5, 40)
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """SELECT c.*, bm25(chunks_fts) AS rank,
                          chunks_fts.content AS fts_content,
                          chunks_fts.path AS fts_path,
                          chunks_fts.symbol AS fts_symbol
                   FROM chunks_fts
                   JOIN chunks c ON c.id = chunks_fts.rowid
                   WHERE chunks_fts MATCH ? AND c.project_id = ?
                   ORDER BY rank LIMIT ?""",
                (match, project_id, candidates),
            ).fetchall()
        except Exception:
            return []

    scored: list[tuple[float, Any]] = []
    token_count = len(tokens)
    for row in rows:
        rank = row["rank"] or 0.0
        # The FTS columns already hold tokenized text, so coverage is a set test.
        haystack = f" {row['fts_content']} {row['fts_path'] or ''} {row['fts_symbol'] or ''} "
        present = [t for t in tokens if f" {t} " in haystack]
        coverage = len(present) / token_count
        # bm25 is lower-is-better and negative; normalize then blend.
        bm25_part = 1.0 / (1.0 + max(0.0, -rank))
        scored.append((0.75 * coverage + 0.25 * bm25_part, row))
    scored.sort(key=lambda item: -item[0])

    return [_row_to_chunk(row, score, "keyword") for score, row in scored[:limit]]


def vector_search(project_id: int, query: str, limit: int = 20) -> list[dict[str, Any]]:
    vector = emb.embed_query(query)
    if not vector:
        return []
    store = SqliteVectorStore(project_id)
    hits = store.search(vector, limit=limit)
    if not hits:
        return []
    ids = [chunk_id for chunk_id, _ in hits]
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = {r["id"]: r for r in conn.execute(f"SELECT * FROM chunks WHERE id IN ({placeholders})", ids)}
    out = []
    for chunk_id, score in hits:
        row = rows.get(chunk_id)
        if row is not None:
            out.append(_row_to_chunk(row, score, "vector"))
    return out


def _rrf_fuse(result_sets: list[list[dict[str, Any]]], limit: int) -> list[dict[str, Any]]:
    scores: dict[int, float] = {}
    store: dict[int, dict[str, Any]] = {}
    for results in result_sets:
        for rank, item in enumerate(results, start=1):
            cid = item["chunk_id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
            if cid not in store:
                store[cid] = dict(item)
            else:
                store[cid]["retriever"] = "hybrid"
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    out = []
    for cid, fused in ordered:
        item = dict(store[cid])
        item["score"] = round(fused, 5)
        item["retriever"] = "hybrid"
        out.append(item)
    return out


def search(project_id: int, query: str, top_k: int = 8) -> list[dict[str, Any]]:
    if not query.strip():
        return []
    kw = keyword_search(project_id, query, limit=max(top_k, 12))
    vec = vector_search(project_id, query, limit=max(top_k, 12))
    merged = _rrf_fuse([kw, vec], top_k) if kw and vec else (kw or vec)[:top_k]
    # Trim payload for the UI while keeping enough for the model.
    for item in merged:
        item.pop("chunk_id", None)
        if len(item["content"]) > 4000:
            item["content"] = item["content"][:4000] + "\n…(截断)"
    return merged


def context_for_prompt(chunks: list[dict[str, Any]], budget_chars: int = 12000) -> str:
    """Render retrieved chunks into a compact, cited context block."""
    parts: list[str] = []
    used = 0
    for chunk in chunks:
        header = f"### {chunk['path']}:{chunk['start_line']}-{chunk['end_line']}"
        if chunk.get("symbol"):
            header += f" ({chunk['kind']} {chunk['symbol']})"
        body = chunk["content"]
        block = f"{header}\n```{chunk.get('language') or ''}\n{body}\n```"
        if used + len(block) > budget_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)
