"""Index, jobs and search endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import ProjectDep, TokenDep
from app.core.db import get_conn
from app.core.errors import NotFound
from app.indexing.indexer import start_index_job
from app.retrieval import hybrid
from app.storage import repo

router = APIRouter(tags=["index"])


@router.post("/projects/{project_id}/index")
def start_index(project: ProjectDep, _: TokenDep, force: bool = Query(default=False)) -> dict[str, int]:
    job_id = start_index_job(project["id"], force=force)
    return {"job_id": job_id}


@router.get("/projects/{project_id}/index")
def index_status(project: ProjectDep, _: TokenDep) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE project_id = ? AND kind = 'index' ORDER BY id DESC LIMIT 1",
            (project["id"],),
        ).fetchone()
    job = repo.get_job(int(row["id"])) if row else None
    with get_conn() as conn:
        indexed = conn.execute(
            "SELECT COUNT(*) AS n, MAX(indexed_at) AS last FROM indexed_files WHERE project_id = ?",
            (project["id"],),
        ).fetchone()
    return {
        "project": project,
        "last_job": job,
        "files": int(indexed["n"]) if indexed else 0,
        "indexed_at": indexed["last"] if indexed else None,
    }


@router.get("/projects/{project_id}/search")
def search(
    project: ProjectDep,
    _: TokenDep,
    q: str = Query(min_length=1),
    top_k: int = Query(default=8, ge=1, le=30),
) -> list[dict[str, Any]]:
    return hybrid.search(project["id"], q, top_k=top_k)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, _: TokenDep) -> dict[str, Any]:
    job = repo.get_job(job_id)
    if job is None:
        raise NotFound("job not found")
    return job
