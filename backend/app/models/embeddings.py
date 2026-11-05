"""Embedding helpers: batch embedding with a pluggable client and a safe no-op
when embeddings are not configured (retrieval then runs keyword-only).
"""

from __future__ import annotations

import array

from app.models import config as model_config


def embed_texts(texts: list[str], batch_size: int = 32) -> list[list[float]] | None:
    client = model_config.build_embeddings()
    if client is None:
        return None
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = [t[:6000] for t in texts[start : start + batch_size]]
        vectors = client.embed_documents(batch)
        out.extend([list(map(float, v)) for v in vectors])
    return out


def embed_query(text: str) -> list[float] | None:
    client = model_config.build_embeddings()
    if client is None:
        return None
    try:
        vector = client.embed_query(text[:6000])
    except Exception:
        return None
    return list(map(float, vector))


def pack(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    arr = array.array("f")
    arr.frombytes(blob)
    return arr.tolist()


def embedding_model_name() -> str | None:
    ms = model_config.load_settings()
    if ms.embedding and ms.embedding.model:
        return ms.embedding.model
    return None


def is_configured() -> bool:
    return (
        embedding_model_name() is not None
        and model_config.has_api_key("embedding")
        or (model_config.has_api_key("chat") and embedding_model_name() is not None)
    )
