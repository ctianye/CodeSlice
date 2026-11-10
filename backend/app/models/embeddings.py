"""Embedding helpers: batch embedding with a pluggable client and a safe no-op
when embeddings are not configured (retrieval then runs keyword-only).

Batch calls are all-or-nothing on the wire: providers reject the *whole* request
when a single input exceeds their context (Ollama answers ``413 the input length
exceeds the context length``). One bad chunk therefore used to leave a project
with no vectors at all, and because nothing was persisted the next index run
retried the same set forever. A failing batch is now retried input by input, so
only the offending text degrades — and it degrades in steps, keeping whatever
prefix the model can still read.
"""

from __future__ import annotations

import array
import logging
from collections.abc import Callable

from app.models import config as model_config

log = logging.getLogger(__name__)

# Characters per input. Providers count tokens, not characters, so this is a
# guard against pathological files rather than an exact limit.
MAX_EMBED_CHARS = 6000
# Shortening steps used when a single input is rejected as too long.
FALLBACK_CHARS = (MAX_EMBED_CHARS, 3000, 1500)


def _floats(vector: object) -> list[float]:
    return [float(value) for value in vector]  # type: ignore[union-attr]


def embed_texts(
    texts: list[str],
    batch_size: int = 32,
    on_batch: Callable[[int, int], None] | None = None,
) -> list[list[float] | None] | None:
    """One vector per input, or ``None`` for inputs no model would accept.

    ``on_batch(done, total)`` fires after each batch so a caller can report
    progress while the work happens instead of only when it ends.
    """
    client = model_config.build_embeddings()
    if client is None:
        return None
    out: list[list[float] | None] = []
    total = len(texts)
    for start in range(0, total, batch_size):
        batch = [text[:MAX_EMBED_CHARS] for text in texts[start : start + batch_size]]
        out.extend(_embed_batch(client, batch))
        if on_batch is not None:
            on_batch(min(start + batch_size, total), total)
    return out


def _embed_batch(client: object, batch: list[str]) -> list[list[float] | None]:
    try:
        return [_floats(vector) for vector in client.embed_documents(batch)]  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 - isolate the offending input
        log.debug("embedding batch of %d failed: %s", len(batch), exc)
    return [_embed_one(client, text) for text in batch]


def _embed_one(client: object, text: str) -> list[float] | None:
    for limit in FALLBACK_CHARS:
        try:
            return _floats(client.embed_documents([text[:limit]])[0])  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - try a shorter prefix next
            continue
    return None


def embed_query(text: str) -> list[float] | None:
    client = model_config.build_embeddings()
    if client is None:
        return None
    try:
        vector = client.embed_query(text[:MAX_EMBED_CHARS])
    except Exception:
        return None
    return _floats(vector)


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
