"""Read-only catalog of the user's own accepted H3 recipes.

Recipes are JSON records in the Studio data folder ($H3_MAC_DATA_DIR/recipes). A fresh Studio has none: the catalog is
empty and video/world swap use their built-in settings."""
from __future__ import annotations

import json
import hashlib
import re
import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .config import DATA_ROOT

RECIPE_DIR = DATA_ROOT / "recipes"


def validated_recipes() -> list[dict]:
    result = []
    if not RECIPE_DIR.is_dir():
        return result
    for path in sorted(RECIPE_DIR.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not str(item.get("status", "")).startswith("approved_"):
            continue
        if not all(item.get(key) for key in ("id", "job_id", "request_sha256", "output_sha256", "request")):
            continue
        if not isinstance(item["request"], dict) or not all(
            isinstance(item[key], str) and re.fullmatch(r"[a-f0-9]{64}", item[key])
            for key in ("request_sha256", "output_sha256")):
            continue
        # Historical recipes hash the original UTF-8 request file, not canonical JSON.
        payload = (json.dumps(item["request"], ensure_ascii=False, indent=2) + "\n").encode()
        if hashlib.sha256(payload).hexdigest() != item["request_sha256"]:
            continue
        result.append(item)
    return result


def accepted_recipes() -> list[dict]:
    return [{**{key: item.get(key) for key in (
            "id", "title", "status", "approved_on", "job_id", "request_sha256",
            "output_sha256", "compiled_profile", "scope", "assets")},
             "request_integrity": "verified", "historically_accepted": True,
             "environment_matches": None} for item in validated_recipes()]


def make_router(store=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/recipes/accepted")
    async def list_accepted_recipes() -> list[dict]:
        return accepted_recipes()

    @router.get("/api/recipes/accepted/{recipe_id}")
    async def get_accepted_recipe(recipe_id: str) -> dict:
        for item in validated_recipes():
            if item["id"] == recipe_id:
                return item
        raise HTTPException(404, "Принятый рецепт не найден")

    @router.get("/api/recipes/accepted/{recipe_id}/verify")
    async def verify_recipe(recipe_id: str) -> dict:
        item = await get_accepted_recipe(recipe_id)
        if store is None:
            raise HTTPException(503, "Хранилище Studio недоступно")
        def check():
            checks = []
            for expected in item.get("assets", []):
                asset = store.get_asset(expected["asset_id"])
                path = Path(asset["path"]) if asset else None
                actual = None
                if path and path.is_file():
                    with path.open("rb") as stream:
                        actual = hashlib.file_digest(stream, "sha256").hexdigest()
                checks.append({"asset_id": expected["asset_id"], "matches": actual == expected["sha256"]})
            job = store.get_job(item["job_id"])
            output = Path(job["output_path"]) if job and job.get("output_path") else None
            actual_output = None
            if output and output.is_file():
                with output.open("rb") as stream:
                    actual_output = hashlib.file_digest(stream, "sha256").hexdigest()
            return {"recipe_id": recipe_id, "request_integrity": "verified",
                    "historically_accepted": True, "assets": checks,
                    "assets_available": bool(checks) and all(row["matches"] for row in checks),
                    "output_matches": actual_output == item["output_sha256"],
                    "environment_matches": None, "gpu_submitted": False}
        return await asyncio.to_thread(check)

    return router
