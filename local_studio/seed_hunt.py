"""«🎲 Подобрать seed»: N small previews of one job, then «Дорендерить этот» on the one the user liked.

Previews and the finish are ordinary Mac-queue jobs (one at a time on the GPU, same worker, receipts and
MP4 download) with `settings.seed_hunt`; the GPU API turns them into pass 1 kept in an LQ package and
pass 2 of that package (app/h3graph/seed_hunt.py). Preview 1 keeps the source job's seed, the others
get fresh ones. The package lives on the GPU server that rendered the preview: finish there.
"""
from __future__ import annotations

import json
import secrets
import uuid

from fastapi import APIRouter, HTTPException

from app.h3graph.seed_hunt import COUNT_RANGE


def hunt_of(job: dict) -> dict | None:
    return json.loads(job["settings_json"]).get("seed_hunt")


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    def job_or_404(job_id: str) -> dict:
        job = ctx.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        return job

    @router.post("/api/jobs/{job_id}/seed-hunt", status_code=201)
    async def start(job_id: str, body: dict | None = None) -> dict:
        source = job_or_404(job_id)
        if hunt_of(source):
            raise HTTPException(409, "Это уже задание подбора seed: подбирай от исходного задания")
        count = (body or {}).get("count", 3)
        if type(count) is not int or not COUNT_RANGE[0] <= count <= COUNT_RANGE[1]:
            raise HTTPException(422, f"Превью: {COUNT_RANGE[0]}–{COUNT_RANGE[1]}")
        hunt_id = str(uuid.uuid4())
        seeds = [json.loads(source["settings_json"])["seed"]] + [secrets.randbelow(2**53) for _ in range(count - 1)]
        jobs = ctx.store.add_seed_hunt_jobs(source, [
            {"seed": seed, "seed_hunt": {"role": "preview", "hunt_id": hunt_id, "index": index, "source_job_id": job_id}}
            for index, seed in enumerate(seeds, 1)])
        ctx.gpu_backoff.ok()  # wake dispatch, as POST /api/jobs does
        return {"hunt_id": hunt_id, "jobs": [job["job_id"] for job in jobs]}

    @router.post("/api/jobs/{job_id}/seed-hunt/finish", status_code=201)
    async def finish(job_id: str) -> dict:
        preview = job_or_404(job_id)
        hunt = hunt_of(preview)
        if not hunt or hunt.get("role") != "preview":
            raise HTTPException(409, "Дорендер — только у превью подбора seed")
        if preview["state"] != "completed":
            raise HTTPException(409, "Превью ещё не готово")
        seed = json.loads(preview["settings_json"])["seed"]
        [job] = ctx.store.add_seed_hunt_jobs(preview, [
            {"seed": seed, "seed_hunt": {**hunt, "role": "finish", "preview_job_id": job_id}}])
        ctx.gpu_backoff.ok()
        return {"hunt_id": hunt["hunt_id"], "job_id": job["job_id"]}

    @router.get("/api/seed-hunts/{hunt_id}")
    async def hunt(hunt_id: str) -> dict:
        jobs = [job for job in ctx.store.list_jobs() if (hunt_of(job) or {}).get("hunt_id") == hunt_id]
        if not jobs:
            raise HTTPException(404, "Подбор не найден")
        jobs.sort(key=lambda job: (hunt_of(job)["role"] != "preview", hunt_of(job)["index"]))
        return {"hunt_id": hunt_id, "jobs": [
            {"job_id": job["job_id"], "state": job["state"], "error": job.get("error"),
             "seed": json.loads(job["settings_json"])["seed"], **hunt_of(job)} for job in jobs]}

    return router
