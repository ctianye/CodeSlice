"""Model settings and connectivity test."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import TokenDep
from app.models import config as model_config

router = APIRouter(prefix="/settings", tags=["settings"])


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
