"""Versioned production descriptions; execution compilation lives in production_compile."""
from __future__ import annotations

from typing import Literal
from app.fingerprints import json_sha256

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.director_config import normalize_director_config


class StrictSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CharacterSpec(StrictSpec):
    id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    identity: str = Field(min_length=1, max_length=2000)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=16)
    physical_constraints: str = Field(default="", max_length=2000)
    individual_behavior: str = Field(default="", max_length=2000)


class BehaviorPattern(StrictSpec):
    id: str = Field(min_length=1, max_length=120)
    actor_id: str = Field(min_length=1, max_length=120)
    trigger: str = Field(min_length=1, max_length=1000)
    observed_body_action: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=16)
    status: Literal["draft", "verified"] = "draft"


class Beat(StrictSpec):
    id: str = Field(min_length=1, max_length=120)
    actor_ids: list[str] = Field(min_length=1, max_length=16)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    cause: str = Field(min_length=1, max_length=1000)
    cause_beat_id: str | None = None
    observed_body_action: str = Field(min_length=1, max_length=2000)
    effect: str = Field(min_length=1, max_length=1000)
    behavior_pattern_ids: list[str] = Field(default_factory=list, max_length=16)


class VisualProfile(StrictSpec):
    id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=200)
    sources: list[str] = Field(default_factory=list, max_length=20)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=10)
    frame_direction: str = Field(default="", max_length=1500)
    video_direction: str = Field(default="", max_length=1500)
    director_config: dict = Field(default_factory=dict)

    @field_validator("director_config")
    @classmethod
    def known_direction(cls, value: dict) -> dict:
        return normalize_director_config(value)


class ShotSpec(StrictSpec):
    id: str = Field(min_length=1, max_length=120)
    revision: int = Field(ge=1)
    duration: float = Field(gt=0, le=600, allow_inf_nan=False)
    purpose: str = Field(min_length=1, max_length=1000)
    state_in: str = Field(min_length=1, max_length=2000)
    state_out: str = Field(min_length=1, max_length=2000)
    first_frame_description: str = Field(default="", max_length=2000)
    spatial_layout: str = Field(default="", max_length=2000)
    director_config: dict = Field(default_factory=dict)
    beats: list[Beat] = Field(min_length=1, max_length=120)

    @field_validator("director_config")
    @classmethod
    def known_direction(cls, value: dict) -> dict:
        return normalize_director_config(value)


class ProductionSpec(StrictSpec):
    schema_version: Literal[1] = 1
    franchise_id: str = Field(min_length=1, max_length=120)
    characters: list[CharacterSpec] = Field(min_length=1, max_length=32)
    behavior_patterns: list[BehaviorPattern] = Field(default_factory=list, max_length=120)
    shot: ShotSpec
    visual_profile: VisualProfile | None = None


def validate_spec(spec: ProductionSpec) -> dict:
    issues = []

    def issue(code: str, item_id: str):
        issues.append({"code": code, "item_id": item_id})

    actors = {actor.id for actor in spec.characters}
    patterns = {pattern.id: pattern for pattern in spec.behavior_patterns}
    beats = {beat.id: beat for beat in spec.shot.beats}
    for label, rows in (("character", spec.characters), ("behavior", spec.behavior_patterns), ("beat", spec.shot.beats)):
        seen = set()
        for row in rows:
            if row.id in seen:
                issue(f"duplicate_{label}_id", row.id)
            seen.add(row.id)
    for pattern in spec.behavior_patterns:
        if pattern.actor_id not in actors:
            issue("unknown_behavior_actor", pattern.id)
        if pattern.status == "verified" and not (pattern.source and pattern.evidence):
            issue("verified_behavior_requires_evidence", pattern.id)
    for beat in spec.shot.beats:
        if beat.end <= beat.start or beat.end > spec.shot.duration:
            issue("beat_outside_duration", beat.id)
        if set(beat.actor_ids) - actors:
            issue("unknown_beat_actor", beat.id)
        if beat.cause_beat_id:
            cause = beats.get(beat.cause_beat_id)
            if cause is None or cause.id == beat.id or cause.end > beat.start:
                issue("invalid_cause_reference", beat.id)
        for pattern_id in beat.behavior_pattern_ids:
            pattern = patterns.get(pattern_id)
            if pattern is None or pattern.actor_id not in beat.actor_ids:
                issue("invalid_behavior_reference", beat.id)
    return {"valid": not issues, "issues": issues, "spec": spec.model_dump(),
            "scope": "description_validation_only", "creative_approved": False}


def make_router(store=None) -> APIRouter:
    router = APIRouter()

    @router.post("/api/production/spec/validate")
    async def validate(payload: ProductionSpec) -> dict:
        result = validate_spec(payload)
        if store is not None:
            for actor in payload.characters:
                for asset_id in actor.reference_asset_ids:
                    if store.get_asset(asset_id) is None:
                        result["issues"].append({"code": "reference_asset_missing", "item_id": asset_id})
            if payload.visual_profile:
                for asset_id in payload.visual_profile.reference_asset_ids:
                    asset = store.get_asset(asset_id)
                    if not asset or asset.get("kind") != "image":
                        result["issues"].append({"code": "visual_reference_missing_or_not_image", "item_id": asset_id})
            result["valid"] = not result["issues"]
        result["spec_sha256"] = json_sha256(result["spec"])
        return result

    return router
