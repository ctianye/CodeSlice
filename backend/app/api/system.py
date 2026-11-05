from __future__ import annotations

from fastapi import APIRouter

from app.core.config import APP_NAME, VERSION, settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": VERSION,
        "app": APP_NAME,
        "data_dir": str(settings.data_dir),
    }
