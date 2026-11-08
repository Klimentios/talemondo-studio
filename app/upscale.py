"""SeedVR2 jobs on the existing ComfyUI GPU, with immutable client receipts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from .job_registry import recover_accepted
from .qwen_lifecycle import async_render_submission
from .upscale_graph import MODES, SEEDVR2_MODELS, build_upscale_graph, required_nodes, required_weights


MAX_VIDEO = 500 * 1024 * 1024


def _probe(data: bytes) -> float:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as source:
        source.write(data)
        source.flush()
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                 "stream=codec_type,r_frame_rate,width,height:format=duration",
                                 "-of", "json", source.name], capture_output=True, text=True,
                                timeout=30, check=False)
    if result.returncode:
        raise ValueError("Исходный MP4 не читается")
    info = json.loads(result.stdout)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video or not video.get("width") or not video.get("height"):
        raise ValueError("В исходном MP4 нет видеопотока")
    numerator, denominator = (int(v) for v in video["r_frame_rate"].split("/"))
    fps = numerator / denominator
    if not 0 < fps <= 120 or float(info.get("format", {}).get("duration", 0)) > 60:
        raise ValueError("Поддерживаются ролики до 60 секунд и 120 кадров/с")
    return fps


def make_router(comfy, registry_factory) -> APIRouter:
    router = APIRouter(prefix="/api/upscale", tags=["upscale"])

    @router.get("/capabilities")
    async def capabilities(mode: str = "upscale", model: str = "3b") -> dict:
        if mode not in MODES:
            raise HTTPException(422, "Неизвестный режим улучшения ролика")
        if model not in SEEDVR2_MODELS:
            raise HTTPException(422, "Неизвестная модель SeedVR2")
        try:
            nodes = await comfy.object_info()
            root = Path(os.environ.get("H3_COMFY_ROOT", "/workspace/h3/ComfyUI"))
            missing = sorted(required_nodes(mode) - set(nodes))
            missing.extend(Path(path).name for path, _ in required_weights(mode, model) if not (root / path).is_file())
            return {"available": not missing, "missing": missing, "model": MODES[mode], "mode": mode,
                    "modes": {name: name for name in MODES}}
        except Exception:
            return {"available": False, "missing": ["GPU недоступен"], "model": MODES[mode], "mode": mode}

    @router.post("/jobs", status_code=202)
    async def submit(client_job_id: str = Form(...), video: UploadFile = File(...),
                     mode: str = Form("upscale"),
                     # A/B only (scripts/h3_ab.py upscale): "7b" = SeedVR2 7B fp16 (H3_SEEDVR2_7B=1).
                     ab_seedvr2_model: str = Form("")) -> dict:
        raise HTTPException(410, "Апскейл отключён; доступ к старым результатам сохранён")
        try:
            uuid.UUID(client_job_id)
        except ValueError as exc:
            raise HTTPException(422, "Некорректный client_job_id") from exc
        if video.content_type != "video/mp4":
            raise HTTPException(422, "Нужен MP4")
        data = await video.read(MAX_VIDEO + 1)
        if not data or len(data) > MAX_VIDEO:
            raise HTTPException(413, "MP4 пуст или превышает 500 МБ")
        try:
            fps = await asyncio.to_thread(_probe, data)
        except (ValueError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, str(exc)) from exc
        if mode not in MODES:
            raise HTTPException(422, "Неизвестный режим улучшения ролика")
        model = ab_seedvr2_model.strip() or "3b"
        if model not in SEEDVR2_MODELS or (model != "3b" and mode == "smooth"):
            raise HTTPException(422, "ab_seedvr2_model: 3b или 7b, только с SeedVR2")
        capability = await capabilities(mode, model)
        if not capability["available"]:
            raise HTTPException(503, f"{MODES[mode]} недоступен: {', '.join(capability['missing'])}")
        registry = registry_factory()
        digest = hashlib.sha256(data).hexdigest()
        # The SeedVR2-only hash is unchanged so receipts of earlier upscales still match.
        request_hash = f"upscale-3b-fp16:{digest}" if mode == "upscale" else f"upscale-{mode}:{digest}"
        if model != "3b":
            request_hash = f"upscale-{model}-fp16-{mode}:{digest}"
        existing = registry.get(client_job_id)
        if existing:
            if existing["request_hash"] == request_hash:
                existing = await recover_accepted(registry, comfy, existing)
            if existing["request_hash"] != request_hash:
                raise HTTPException(409, "client_job_id уже использован с другим заданием")
            if existing["remote_job_id"]:
                return {"job_id": existing["remote_job_id"], "status": "queued"}
            raise HTTPException(409, "Приём задания требует сверки; автоповтор отключён")
        try:
            async with async_render_submission():
                await comfy.release_models_if_idle()
                planned = str(uuid.uuid4())
                fresh, record = registry.accept(client_job_id, request_hash, planned_remote_id=planned)
                if not fresh:
                    if record["remote_job_id"]:
                        return {"job_id": record["remote_job_id"], "status": "queued"}
                    raise HTTPException(409, "Приём задания требует сверки; автоповтор отключён")
                source_name = await comfy.upload_file(f"upscale-{client_job_id}.mp4", data, "video/mp4")
                graph = build_upscale_graph(source_name, client_job_id, fps, mode=mode, model=model)
                remote_id = await comfy.queue(graph, prompt_id=planned)
            registry.queued(client_job_id, remote_id)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Отправка апскейла требует сверки: {exc}") from exc
        return {"job_id": remote_id, "status": "queued"}

    @router.get("/by-client/{client_job_id}")
    async def by_client(client_job_id: str) -> dict:
        registry = registry_factory()
        record = registry.get(client_job_id)
        if record and record["request_hash"].startswith("upscale-"):
            record = await recover_accepted(registry, comfy, record)
        if not record or not record["request_hash"].startswith("upscale-"):
            raise HTTPException(404, "Задание апскейла не найдено")
        return {"job_id": record["remote_job_id"], "status": record["state"]}

    @router.get("/jobs/{job_id}")
    async def status(job_id: str) -> dict:
        registry = registry_factory()
        if not registry.owns_remote(job_id):
            raise HTTPException(404, "Задание не найдено")
        result = await comfy.status(job_id)
        if result.get("status") == "unknown":
            result = {**result, "lost": True, "error": "ComfyUI перезапускался: апскейл потерян на GPU"}
        result.pop("video", None)
        return result

    @router.get("/jobs/{job_id}/video")
    async def output(job_id: str) -> StreamingResponse:
        if not registry_factory().owns_remote(job_id):
            raise HTTPException(404, "Задание не найдено")
        result = await comfy.status(job_id)
        if result["status"] != "success":
            raise HTTPException(409, "Апскейл ещё не готов")
        return StreamingResponse(comfy.stream_video(result["video"]), media_type="video/mp4")

    return router
