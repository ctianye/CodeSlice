"""Model settings, connectivity test and local Ollama embedding presets."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import TokenDep
from app.models import config as model_config
from app.services import ollama

router = APIRouter(prefix="/settings", tags=["settings"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class ChatBody(BaseModel):
    protocol: str | None = None
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=256, le=32000)
    api_key: str | None = None


class EmbeddingBody(BaseModel):
    model: str | None = None
    base_url: str | None = None
    dimensions: int | None = None
    api_key: str | None = None


class SettingsBody(BaseModel):
    chat: ChatBody | None = None
    embedding: EmbeddingBody | None = None


@router.get("/model")
def get_model_settings(_: TokenDep) -> dict[str, Any]:
    return model_config.public_settings()


@router.put("/model")
def put_model_settings(body: SettingsBody, _: TokenDep) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if body.chat is not None:
        payload["chat"] = {k: v for k, v in body.chat.model_dump().items() if v is not None}
    if body.embedding is not None:
        payload["embedding"] = {k: v for k, v in body.embedding.model_dump().items() if v is not None}
    model_config.update_settings(payload)
    return model_config.public_settings()


@router.delete("/model/key")
def clear_key(_: TokenDep, slot: str = "chat") -> dict[str, bool]:
    model_config.delete_api_key(slot)
    return {"ok": True}


@router.post("/model/test")
def test_model(_: TokenDep) -> dict[str, Any]:
    return model_config.test_model()


class OllamaPullBody(BaseModel):
    # No free-form model name: the service validates against its preset list too,
    # but rejecting here keeps a bad request from ever opening an SSE stream.
    model: str = Field(min_length=1, max_length=120)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/ollama/status")
def ollama_status(_: TokenDep) -> dict[str, Any]:
    """Probe the local Ollama daemon (~1.5s). Always 200, even when absent."""
    return ollama.status()


@router.post("/ollama/pull")
def ollama_pull(body: OllamaPullBody, _: TokenDep) -> StreamingResponse:
    """Download one preset model, streaming Ollama's progress as SSE."""
    if body.model not in ollama.PRESET_MODELS:
        raise HTTPException(status_code=400, detail=f"unsupported model: {body.model}")

    def stream() -> Iterator[str]:
        try:
            for event in ollama.pull(body.model):
                yield _sse(event)
        except Exception as exc:  # defensive: the generator should not raise
            yield _sse({"type": "error", "model": body.model, "detail": f"{type(exc).__name__}: {exc}"[:300]})
            yield _sse({"type": "done", "model": body.model})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)
