"""Persist explicit review evidence; a submitted review is not automated viewing."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .production_artifacts import ArtifactKind, result_record


class ReviewInput(BaseModel):
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verdict: Literal["technical_pass", "creative_accepted", "rejected"]
    reviewer: str = Field(min_length=1, max_length=120)
    evidence: str = Field(min_length=10, max_length=6000)
    viewed_ranges: list[tuple[float, float]] = Field(default_factory=list, max_length=200)
    audio_reviewed: bool = False


def output_digest(store, job_id: str, kind: ArtifactKind = "job") -> tuple[dict, Path, str]:
    job = result_record(store, job_id, kind)
    path = Path(job["output_path"])
    if not path.is_file():
        raise ValueError("MP4 результата отсутствует")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if job.get("output_sha256") and job["output_sha256"] != digest:
        raise ValueError("MP4 изменён после завершения задания; требуется сверка результата")
    return job, path, digest


def probe(path: Path) -> tuple[float, bool]:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=30, check=True)
    data = json.loads(result.stdout)
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Длительность MP4 не определена")
    return duration, any(item.get("codec_type") == "audio" for item in data.get("streams", []))


def review_file(store, job_id: str, kind: ArtifactKind = "job") -> Path:
    # Hash the external ID rather than using it as a filesystem path.
    root = store.root / "production_reviews"
    if kind != "job":
        if kind not in {"concat", "edl", "motion", "asset"}:
            raise ValueError("Неизвестный вид результата")
        root = root / "artifacts" / kind
    return root / (hashlib.sha256(job_id.encode()).hexdigest() + ".json")


def load_reviews(store, job_id: str, kind: ArtifactKind = "job") -> list[dict]:
    path = review_file(store, job_id, kind)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def require_creative_acceptance(store, job_id: str, kind: ArtifactKind = "job") -> dict:
    _, _, digest = output_digest(store, job_id, kind)
    matching = [row for row in load_reviews(store, job_id, kind) if row["output_sha256"] == digest]
    if not matching or matching[-1]["verdict"] != "creative_accepted":
        raise ValueError("Финальный MP4 не имеет действующей творческой приёмки")
    return matching[-1]


def covers_whole(ranges: list[tuple[float, float]], duration: float) -> bool:
    end = 0.0
    for start, stop in sorted(ranges):
        if not all(math.isfinite(v) for v in (start, stop)) or start < 0 or stop <= start or stop > duration + 0.05:
            return False
        if start > end + 0.05:
            return False
        end = max(end, stop)
    return end >= duration - 0.05


def record_review(store, job_id: str, payload: ReviewInput, kind: ArtifactKind = "job") -> dict:
    _, path, digest = output_digest(store, job_id, kind)
    if digest != payload.output_sha256:
        raise ValueError("Хеш просмотренного MP4 не совпадает с текущим результатом")
    rows = load_reviews(store, job_id, kind)
    duration, has_audio = probe(path)
    if payload.verdict == "creative_accepted":
        current = [row for row in rows if row["output_sha256"] == digest]
        since_rejection = []
        for row in current:
            if row["verdict"] == "rejected":
                since_rejection.clear()
            else:
                since_rejection.append(row)
        if not any(row["verdict"] == "technical_pass" for row in since_rejection):
            raise ValueError("Перед творческой приёмкой требуется technical_pass этого MP4")
        if not covers_whole(payload.viewed_ranges, duration):
            raise ValueError("Для приёмки требуется просмотр всего MP4 без пропусков")
        if has_audio and not payload.audio_reviewed:
            raise ValueError("Для приёмки требуется прослушивание звуковой дорожки")
    review = {"id": uuid.uuid4().hex, "job_id": job_id, "artifact_kind": kind, **payload.model_dump(),
              "duration": duration, "has_audio": has_audio, "source": "reviewer_reported",
              "created_at": datetime.now(timezone.utc).isoformat()}
    target = review_file(store, job_id, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps([*rows, review], ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)
    return review


def make_router(store) -> APIRouter:
    router = APIRouter()
    locks: dict[str, asyncio.Lock] = {}

    @router.get("/api/jobs/{job_id}/production-reviews")
    async def reviews(job_id: str) -> list[dict]:
        return load_reviews(store, job_id)

    @router.post("/api/jobs/{job_id}/production-reviews", status_code=201)
    async def review(job_id: str, payload: ReviewInput) -> dict:
        try:
            async with locks.setdefault(str(review_file(store, job_id)), asyncio.Lock()):
                return await asyncio.to_thread(record_review, store, job_id, payload)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/production/artifacts/{kind}/{artifact_id}/reviews")
    async def artifact_reviews(kind: ArtifactKind, artifact_id: str) -> dict:
        try:
            _, _, digest = await asyncio.to_thread(output_digest, store, artifact_id, kind)
            rows = await asyncio.to_thread(load_reviews, store, artifact_id, kind)
            return {"artifact_id": artifact_id, "kind": kind, "output_sha256": digest, "reviews": rows}
        except (ValueError, OSError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/api/production/artifacts/{kind}/{artifact_id}/reviews", status_code=201)
    async def artifact_review(kind: ArtifactKind, artifact_id: str, payload: ReviewInput) -> dict:
        try:
            key = str(review_file(store, artifact_id, kind))
            # Both the legacy job route and this route lock the same review file.
            async with locks.setdefault(key, asyncio.Lock()):
                return await asyncio.to_thread(record_review, store, artifact_id, payload, kind)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
