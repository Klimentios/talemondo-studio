"""Read-only researched visual methods; no profile installs or GPU actions."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .production_spec import VisualProfile

CATALOG_PATH = Path(__file__).resolve().parents[1] / "app" / "visual_profiles.json"


def visual_profiles() -> list[dict]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return [{**entry, "visual_profile": VisualProfile.model_validate(entry["visual_profile"]).model_dump()}
            for entry in catalog["profiles"]]


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/visual-profiles")
    async def list_visual_profiles() -> list[dict]:
        return visual_profiles()

    @router.get("/api/visual-profiles/{profile_id}")
    async def get_visual_profile(profile_id: str) -> dict:
        for entry in visual_profiles():
            if entry["visual_profile"]["id"] == profile_id:
                return entry
        raise HTTPException(404, "Визуальный профиль не найден")

    return router
