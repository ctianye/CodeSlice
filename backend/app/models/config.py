"""Model / Embedding configuration, API-key storage and client construction.

Keys never touch SQLite: they live in the OS credential store (Windows
Credential Manager via ``keyring``), service name ``codeslice``. If keyring is
unavailable (headless test runs) we fall back to a 0600 file under the data dir
and never log its contents.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.core.errors import ConfigMissing, InvalidInput
from app.storage.repo import get_setting, set_setting

SERVICE = os.environ.get("CODESLICE_KEY_SERVICE", "codeslice")
SETTINGS_KEY = "model_config"
FALLBACK_KEY_FILE = "secrets.json"

Protocol = Literal["openai-compatible", "anthropic"]


@dataclass
class ModelConfig:
    protocol: str = "openai-compatible"
    model: str = ""
    base_url: str | None = None
    temperature: float = 0.2
    max_tokens: int = 32768
    system_prompt: str = ""
    qa_prompt: str = ""
    note_prompt: str = ""

    def public(self, key_configured: bool) -> dict[str, Any]:
        data = asdict(self)
        data["api_key_configured"] = key_configured
        return data


@dataclass
class EmbeddingConfig:
    model: str = ""
    base_url: str | None = None
    dims: int | None = None

    def public(self, key_configured: bool) -> dict[str, Any]:
        # ``dimensions`` mirrors the renderer type; ``dims`` is the storage name.
        return {
            "protocol": "openai-compatible",
            "model": self.model,
            "base_url": self.base_url,
            "dimensions": self.dims,
            "api_key_configured": key_configured,
        }


@dataclass
class ModelSettings:
    chat: ModelConfig = field(default_factory=ModelConfig)
    embedding: EmbeddingConfig | None = None


# --------------------------------------------------------------------------- #
# credential store
# --------------------------------------------------------------------------- #
def _fallback_path() -> Path:
    from app.core.config import settings

    return settings.data_dir / FALLBACK_KEY_FILE


def _fallback_read() -> dict[str, str]:
    path = _fallback_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _fallback_write(data: dict[str, str]) -> None:
    path = _fallback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    with contextlib.suppress(OSError):  # pragma: no cover - Windows ACLs differ
        os.chmod(path, 0o600)


def set_api_key(slot: str, api_key: str) -> None:
    """slot = 'chat' | 'embedding'."""
    name = f"{SERVICE}:{slot}"
    try:
        import keyring

        keyring.set_password(SERVICE, slot, api_key)
        return
    except Exception:
        pass
    data = _fallback_read()
    data[name] = api_key
    _fallback_write(data)


def get_api_key(slot: str) -> str | None:
    try:
        import keyring

        value = keyring.get_password(SERVICE, slot)
        if value:
            return value
    except Exception:
        pass
    return _fallback_read().get(f"{SERVICE}:{slot}")


def has_api_key(slot: str) -> bool:
    return bool(get_api_key(slot))


def delete_api_key(slot: str) -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE, slot)
    except Exception:
        pass
    data = _fallback_read()
    data.pop(f"{SERVICE}:{slot}", None)
    _fallback_write(data)


# --------------------------------------------------------------------------- #
# persisted (non-secret) settings
# --------------------------------------------------------------------------- #
def load_settings() -> ModelSettings:
    raw = get_setting(SETTINGS_KEY) or {}
    chat_raw = raw.get("chat") or {}
    chat = ModelConfig(
        protocol=chat_raw.get("protocol", "openai-compatible"),
        model=chat_raw.get("model", ""),
        base_url=chat_raw.get("base_url") or None,
        temperature=float(chat_raw.get("temperature", 0.2)),
        max_tokens=int(chat_raw.get("max_tokens", 32768)),
        system_prompt=str(chat_raw.get("system_prompt", "")),
        qa_prompt=str(chat_raw.get("qa_prompt", "")),
        note_prompt=str(chat_raw.get("note_prompt", "")),
    )
    emb_raw = raw.get("embedding")
    embedding = None
    if emb_raw:
        embedding = EmbeddingConfig(
            model=emb_raw.get("model", ""),
            base_url=emb_raw.get("base_url") or None,
            dims=emb_raw.get("dims"),
        )
    return ModelSettings(chat=chat, embedding=embedding)


def save_settings(ms: ModelSettings) -> None:
    payload: dict[str, Any] = {"chat": asdict(ms.chat)}
    payload["embedding"] = asdict(ms.embedding) if ms.embedding else None
    set_setting(SETTINGS_KEY, payload)


def update_settings(payload: dict[str, Any]) -> ModelSettings:
    """Apply a partial PUT body; ``api_key`` is routed to the credential store."""
    current = load_settings()
    chat_payload = payload.get("chat") or {}
    if chat_payload:
        protocol = chat_payload.get("protocol", current.chat.protocol)
        if protocol not in ("openai-compatible", "anthropic"):
            raise InvalidInput(f"unsupported protocol: {protocol}")
        current.chat = ModelConfig(
            protocol=protocol,
            model=chat_payload.get("model", current.chat.model),
            base_url=chat_payload.get("base_url", current.chat.base_url) or None,
            temperature=float(chat_payload.get("temperature", current.chat.temperature)),
            max_tokens=int(chat_payload.get("max_tokens", current.chat.max_tokens)),
            system_prompt=str(chat_payload.get("system_prompt", current.chat.system_prompt)),
            qa_prompt=str(chat_payload.get("qa_prompt", current.chat.qa_prompt)),
            note_prompt=str(chat_payload.get("note_prompt", current.chat.note_prompt)),
        )
        key = chat_payload.get("api_key")
        if key:
            set_api_key("chat", key)

    emb_payload = payload.get("embedding", "absent")
    if emb_payload != "absent":
        if emb_payload is None:
            current.embedding = None
        else:
            current.embedding = EmbeddingConfig(
                model=emb_payload.get("model", current.embedding.model if current.embedding else ""),
                base_url=(emb_payload.get("base_url") or (current.embedding.base_url if current.embedding else None)),
                dims=emb_payload.get(
                    "dimensions",
                    emb_payload.get("dims", current.embedding.dims if current.embedding else None),
                ),
            )
            key = emb_payload.get("api_key")
            if key:
                set_api_key("embedding", key)
    save_settings(current)
    return current


def public_settings() -> dict[str, Any]:
    ms = load_settings()
    from app.agent.prompts import BASE, DEFAULT_QA_PROMPT
    from app.services.notegen import DEFAULT_NOTE_PROMPT

    return {
        "chat": ms.chat.public(has_api_key("chat")),
        "default_system_prompt": BASE,
        "default_qa_prompt": DEFAULT_QA_PROMPT,
        "default_note_prompt": DEFAULT_NOTE_PROMPT,
        "embedding": ms.embedding.public(has_api_key("embedding")) if ms.embedding else None,
    }


# --------------------------------------------------------------------------- #
# clients
# --------------------------------------------------------------------------- #
def build_chat_model(temperature: float | None = None, max_tokens: int | None = None):
    """Return a LangChain chat model for the configured provider.

    ``max_tokens`` overrides the saved budget for one call. Reasoning models bill
    their hidden reasoning against the same limit, so a call that needs a long
    visible answer can ask for more room than the user's default.
    """
    ms = load_settings()
    cfg = ms.chat
    if not cfg.model:
        raise ConfigMissing("model not configured")
    api_key = get_api_key("chat")
    if not api_key:
        raise ConfigMissing("api key not configured")
    temp = cfg.temperature if temperature is None else temperature
    budget = cfg.max_tokens if max_tokens is None else max_tokens

    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")

    if cfg.protocol == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "model": cfg.model,
            "api_key": api_key,
            "temperature": temp,
            "max_tokens": budget,
        }
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return ChatAnthropic(**kwargs)

    from langchain_openai import ChatOpenAI

    kwargs = {
        "model": cfg.model,
        "api_key": api_key,
        "temperature": temp,
        "max_tokens": budget,
        "timeout": 120,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return ChatOpenAI(**kwargs)


def build_embeddings():
    """Return an embeddings client or ``None`` when not configured."""
    ms = load_settings()
    cfg = ms.embedding
    if cfg is None or not cfg.model:
        return None
    api_key = get_api_key("embedding") or get_api_key("chat")
    if not api_key:
        return None
    from langchain_openai import OpenAIEmbeddings

    kwargs: dict[str, Any] = {"model": cfg.model, "api_key": api_key}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return OpenAIEmbeddings(**kwargs)


def test_model() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        model = build_chat_model(temperature=0)
        reply = model.invoke("回复两个字：可用")
        latency = int((time.perf_counter() - started) * 1000)
        text = getattr(reply, "content", "")
        if isinstance(text, list):
            text = " ".join(str(part) for part in text)
        return {"ok": True, "latency_ms": latency, "detail": str(text)[:200]}
    except Exception as exc:  # surface the reason to the settings screen
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "latency_ms": latency, "detail": f"{type(exc).__name__}: {exc}"[:400]}
