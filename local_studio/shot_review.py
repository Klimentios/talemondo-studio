"""Mac route for the cinematographer's shot-list check; pure rules, works without GPU or a model."""
from __future__ import annotations

from fastapi import APIRouter

from app.shot_lint import ShotList, lint_shots


def make_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/shot-lint")
    async def shot_lint(payload: ShotList) -> dict:
        return lint_shots(payload)

    return router
