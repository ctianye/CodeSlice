"""Shared FastAPI dependencies: session token check and project lookup."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException

from app.core.config import settings
from app.core.errors import NotFound
from app.storage import repo


def require_token(
    x_session_token: Annotated[str | None, Header(alias="X-Session-Token")] = None,
) -> None:
    if not x_session_token or x_session_token != settings.token:
        raise HTTPException(status_code=401, detail="invalid session token")


TokenDep = Annotated[None, Depends(require_token)]


def get_project_or_404(project_id: int) -> dict:
    project = repo.get_project(project_id)
    if project is None:
        raise NotFound("project not found")
    return project


ProjectDep = Annotated[dict, Depends(get_project_or_404)]
