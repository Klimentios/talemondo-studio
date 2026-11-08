"""H3 jobs and the Mac queue: create, list, cancel, live preview, MP4 and poster, hide and clear,
Pause, and the side-job cards (upscale, keyframe, edit, sound) shown next to them."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path

from .. import review_gate
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from ..store import StaleTransition


class JobInput(BaseModel):
    client_request_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    prompt: str
    asset_ids: list[str] = Field(default_factory=list)
    reference_numbers: dict[str, int] | None = None
    reference_roles: dict[str, str] | None = None
    reference_owners: dict[str, str] | None = None
    # {video asset_id: {"pairs": [{"owner": hero, "performer": "the woman on the left"}], "keep_others": bool}}
    reference_performers: dict[str, dict] | None = None
    settings: dict = Field(default_factory=dict)
    production_spec: dict | None = None


class QueueInput(BaseModel):
    paused: bool


def _finished_clip(store, job_id: str) -> Path:
    job = store.get_job(job_id)
    if not job or job["state"] != "completed" or not job["output_path"]:
        raise HTTPException(404, "Результат не готов")
    path = Path(job["output_path"])
    if not path.is_file() or not path.resolve().is_relative_to(store.results_dir.resolve()):
        raise HTTPException(404, "MP4 не найден")
    return path


from ..minimax_cloud import API_SWAP_RECIPE, swap_recipe_problems


def validate_source_video_cast(payload: JobInput, get_asset, backend: str = "gpu") -> None:
    """A source-video replacement may only picture heroes assigned to source performers."""
    if payload.settings.get("mode") != "replace":
        return
    roles, owners = payload.reference_roles or {}, payload.reference_owners or {}
    source_ids = []
    pictured = set()
    has_environment = False
    has_prop_reference = False
    for asset_id in payload.asset_ids:
        asset = get_asset(asset_id)
        if not asset:
            continue
        role = roles.get(asset_id, asset.get("role"))
        if asset.get("kind") == "video" and role == "Исходное видео":
            source_ids.append(asset_id)
        if asset.get("kind") == "image" and role in {"Лицо героя", "Персонаж целиком", "Костюм"}:
            owner = owners.get(asset_id, "").strip()
            if owner:
                pictured.add(owner.casefold())
        if asset.get("kind") == "image" and role == "Окружение":
            has_environment = True
        if asset.get("kind") == "image" and role in {"Предмет", "Кадр сцены"}:
            has_prop_reference = True
    if not source_ids:
        return
    mapped = {str(pair.get("owner", "")).strip().casefold()
              for asset_id in source_ids
              for pair in (payload.reference_performers or {}).get(asset_id, {}).get("pairs", [])}
    if not mapped:
        raise ValueError("Для замены по исходному видео назначь каждому герою видимого исполнителя")
    extra = pictured - mapped
    if extra:
        raise ValueError("Герой на фото не назначен исходному исполнителю: " + ", ".join(sorted(extra))
                         + ". Добавление пассажира без исходного исполнителя — отдельная экспериментальная задача")
    if len(mapped) > 2:
        raise ValueError("Замена трёх и более исполнителей в одном исходном видео не является проверенным рецептом: "
                         "сначала раздели сцену на однозначные замены и проверь каждый MP4")
    if backend != "minimax_api" and has_environment:
        raise ValueError("GPU-замена по исходному видео не гарантирует смену всей комнаты по фото «Окружение». "
                         "Сначала подготовь кадр новой сцены и отдельный маршрут генерации")
    if re.search(r"\b(?:pinned photo|photograph|poster|portrait of|drawing of|cork board)\b", payload.prompt, re.I) \
            and not has_prop_reference:
        raise ValueError("На плакате или фотографии нужен отдельный подготовленный референс с нужным героем "
                         "(роль «Предмет» или «Кадр сцены»); текст не заменяет лицо внутри изображения")


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs", status_code=202)
    async def create_job(payload: JobInput) -> dict:
        try:
            settings = dict(payload.settings)
            cloud = getattr(ctx.gpu_transport, "cloud", None)
            if cloud is not None:
                # No explicit route (agents, MCP, old clients): follow the Studio switch.
                settings = cloud.resolve_settings(settings)
            elif settings.get("backend") == "minimax_api":
                raise ValueError("Ключ MiniMax API не настроен в Studio")
            validate_source_video_cast(payload, ctx.store.get_asset, settings.get("backend", "gpu"))
            if settings.get("backend") == "minimax_api" and not settings.get("api_swap_trial"):
                roles = payload.reference_roles or {}
                for asset_id in payload.asset_ids:
                    asset = ctx.store.get_asset(asset_id)
                    if asset and asset.get("kind") == "video" and roles.get(asset_id, asset.get("role")) == "Исходное видео":
                        if settings.get("swap_recipe") == API_SWAP_RECIPE:
                            owners = payload.reference_owners or {}
                            images = [{"role": roles.get(i), "owner": owners.get(i, "")} for i in payload.asset_ids
                                      if (a := ctx.store.get_asset(i)) and a.get("kind") == "image"]
                            problems = swap_recipe_problems(payload.prompt, images)
                            if problems:
                                raise ValueError("Замена по рецепту MiniMax API не собрана: " + "; ".join(problems)
                                                 + ". Построй план video_swap_plan с route=minimax_api")
                            continue
                        raise ValueError("MiniMax API не обеспечивает точную замену в исходном видео. Выбери «Свой GPU» с Character Swap; облачное задание не создано")
            # Always the GPU's real LoRA list (a replacement job needs character_swap even with
            # style_adapter "none"); None only when the GPU cannot be asked right now.
            style_adapters = None if settings.get("backend") == "minimax_api" else await ctx.style_adapters_or_none()
            # In a thread: create_job runs ffprobe on motion videos (up to 10 s), which froze every page.
            job = await asyncio.to_thread(
                ctx.store.create_job, prompt=payload.prompt, asset_ids=payload.asset_ids,
                settings=settings, reference_numbers=payload.reference_numbers,
                reference_roles=payload.reference_roles, reference_owners=payload.reference_owners,
                reference_performers=payload.reference_performers, style_adapters=style_adapters,
                production_spec=payload.production_spec,
                receipt_key=("scene-job:" + payload.client_request_id) if payload.client_request_id else None,
                receipt_hash=hashlib.sha256(json.dumps(payload.model_dump(exclude={"client_request_id"}),
                    sort_keys=True, ensure_ascii=False).encode()).hexdigest() if payload.client_request_id else None)
            ctx.gpu_backoff.ok()  # Wake dispatch immediately after local acceptance.
            return job
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/jobs")
    async def list_jobs() -> list[dict]:
        from ..production_review import load_reviews
        jobs = ctx.store.list_jobs()
        for job in jobs:
            if job.get("state") == "completed":
                reviews = load_reviews(ctx.store, job["job_id"])
                job["qc_verdict"] = reviews[-1]["verdict"] if reviews else None
        return jobs

    @router.get("/api/job-receipts/{request_id}")
    async def job_receipt(request_id: str) -> dict:
        with ctx.store.connect() as db:
            row = db.execute("SELECT job_id FROM submission_receipts WHERE receipt_key=?",
                             ("scene-job:" + request_id,)).fetchone()
        job = ctx.store.get_job(row["job_id"]) if row else None
        if not job:
            raise HTTPException(404, "Квитанция задания не найдена")
        return job

    @router.get("/api/side-jobs")
    async def side_jobs() -> list[dict]:
        from ..side_jobs import hidden_side_jobs, side_job_summary
        queues = ctx.side_queues()
        rows = side_job_summary(queues, hidden=hidden_side_jobs(ctx.store))
        return rows

    @router.delete("/api/side-jobs/{kind}/{item_id}")
    async def hide_side_job(kind: str, item_id: str) -> dict:
        from ..side_jobs import SIDE_KINDS, hide_side_jobs
        if kind not in SIDE_KINDS:
            raise HTTPException(404, "Неизвестный тип задания")
        try:
            return {"hidden": hide_side_jobs(ctx.store, ctx.side_queues(), only=(kind, item_id))}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/api/queue")
    async def queue_state() -> dict:
        hold = review_gate.review_hold(ctx.store)
        return {"paused": ctx.store.queue_paused() or bool(hold), **({"review_hold": hold} if hold else {})}

    @router.put("/api/queue")
    async def set_queue_state(payload: QueueInput) -> dict:
        # A persisted flag the worker re-reads before every submit; waiting for the
        # dispatch lock froze the Pause button for the length of an upload/download.
        if not payload.paused:
            review_gate.acknowledge(ctx.store)  # «Продолжить» = the finished takes were looked at
        return {"paused": ctx.store.set_queue_paused(payload.paused)}

    @router.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        job = ctx.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        return job

    @router.get("/api/jobs/{job_id}/comfyui")
    async def open_job_in_comfyui(job_id: str) -> RedirectResponse:
        """The job's exact graph in the ComfyUI editor (h3-studio-bridge reads ?h3_job)."""
        job = ctx.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        if not job.get("remote_job_id"):
            raise HTTPException(409, "Задание ещё не дошло до ComfyUI")
        if not ctx.comfy_url:
            raise HTTPException(503, "Адрес ComfyUI не настроен (H3_COMFY_URL)")
        return RedirectResponse(f"{ctx.comfy_url}/?h3_job={job['remote_job_id']}", status_code=307)

    @router.get("/api/comfyui/open/{prompt_id}")
    async def open_prompt_in_comfyui(prompt_id: str) -> RedirectResponse:
        """Any graph ComfyUI ran by its prompt id (Motion Director segments keep theirs in motion_jobs)."""
        try:
            prompt_id = str(uuid.UUID(prompt_id))
        except ValueError:
            raise HTTPException(422, "Нужен prompt id ComfyUI (UUID)")
        if not ctx.comfy_url:
            raise HTTPException(503, "Адрес ComfyUI не настроен (H3_COMFY_URL)")
        return RedirectResponse(f"{ctx.comfy_url}/?h3_job={prompt_id}", status_code=307)

    def remote_of(job_id: str) -> tuple[dict, str]:
        job = ctx.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        if not job.get("remote_job_id") or not ctx.comfy_url:
            raise HTTPException(409, "Задание не дошло до ComfyUI или адрес ComfyUI не настроен")
        return job, job["remote_job_id"]

    async def comfy_json(method: str, path: str, **kwargs):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(method, f"{ctx.comfy_url}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
        if response.status_code == 404:
            raise HTTPException(404, "ComfyUI этого не знает (нет моста h3-studio-bridge или графа)")
        if response.status_code >= 400:
            raise HTTPException(502, f"ComfyUI: HTTP {response.status_code} {response.text[:300]}")
        return response.json()

    @router.get("/api/jobs/{job_id}/graph")
    async def job_graph(job_id: str) -> dict:
        """The exact graph ComfyUI ran (or queued) for this job; from the Mac archive once the GPU is gone."""
        from ..job_graphs import JobGraphMissing
        try:
            found = await ctx.job_graphs.get(ctx.store, job_id)
        except JobGraphMissing as exc:
            raise HTTPException(404 if "не найдено" in str(exc) else 409, str(exc)) from exc
        return {"job_id": job_id, "remote_job_id": found["remote_job_id"], "graph": found["graph"],
                "source": found["source"], "saved_at": found.get("saved_at")}

    @router.get("/api/comfyui/nodes/{class_type}")
    async def comfy_node(class_type: str) -> dict:
        """One node's input schema (types, ranges, choices) from the connected ComfyUI."""
        if not ctx.comfy_url:
            raise HTTPException(409, "Адрес ComfyUI не настроен")
        info = await comfy_json("GET", f"/object_info/{class_type}")
        if class_type not in info:
            raise HTTPException(404, f"В ComfyUI нет узла {class_type}")
        return {"class_type": class_type, "input": info[class_type].get("input"),
                "output": info[class_type].get("output"), "output_name": info[class_type].get("output_name"),
                "description": info[class_type].get("description", "")}

    @router.post("/api/jobs/{job_id}/comfyui-edits", status_code=201)
    async def make_comfyui_edit(job_id: str, body: dict) -> dict:
        """An edit without the editor (the Studio agent), saved in ComfyUI like «⇪ Отправить в Studio». No render.
        set=[{node, input, value}] changes literal inputs; ops=[…] (app/graph_ops.py) also adds, removes and
        replaces nodes and rewires, checked against the full /object_info. Model files stay those of the job."""
        from app.graph_edit import apply_settings, edit_problems, graph_changes
        _, remote = remote_of(job_id)
        source = (await comfy_json("GET", f"/h3/studio/graph/{remote}"))["prompt"]
        settings, ops = body.get("set"), body.get("ops")
        if not isinstance(settings, list) and not isinstance(ops, list):
            raise HTTPException(422, "Нужен set: [{node, input, value}] или ops: [{op, …}]")
        if isinstance(ops, list):
            from app.graph_ops import apply_ops
            from ..comfy_schema import ComfyUnavailable
            try:
                result = apply_ops(source, ([{"op": "set", **item} for item in settings] if isinstance(settings, list)
                                            else []) + ops, await ctx.comfy_schema.get())
            except ComfyUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if result["new_problems"]:
                raise HTTPException(422, "; ".join(result["new_problems"][:8]))
            graph = result["graph"]
        else:
            classes = sorted({source[str(item.get("node"))]["class_type"] for item in settings
                              if isinstance(item, dict) and str(item.get("node")) in source})
            node_info = {}
            for name in classes:
                node_info.update(await comfy_json("GET", f"/object_info/{name}"))
            try:
                graph = apply_settings(source, settings, node_info)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        problems = edit_problems(source, graph)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        saved = await comfy_json("POST", "/h3/studio/edited", json={"source_prompt_id": remote, "prompt": graph})
        return {"edit_id": saved["edit_id"], "changes": graph_changes(source, graph), "problems": []}

    @router.get("/api/jobs/{job_id}/comfyui-edits")
    async def comfyui_edits(job_id: str) -> dict:
        """Graphs sent back by «⇪ Отправить в Studio», newest first, with their changes and problems."""
        import httpx
        from app.graph_edit import edit_problems, graph_changes
        _, remote = remote_of(job_id)
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                listed = await client.get(f"{ctx.comfy_url}/h3/studio/edited", params={"source": remote})
                if listed.status_code == 404:
                    raise HTTPException(409, "В ComfyUI нет моста Studio (h3-studio-bridge)")
                listed.raise_for_status()
                edits = listed.json()["edits"][:5]
                if not edits:
                    return {"edits": []}
                source = (await client.get(f"{ctx.comfy_url}/h3/studio/graph/{remote}")).raise_for_status().json()["prompt"]
                result = []
                for item in edits:
                    graph = (await client.get(f"{ctx.comfy_url}/h3/studio/edited/{item['edit_id']}")).raise_for_status().json()["prompt"]
                    result.append({**item, "changes": graph_changes(source, graph), "problems": edit_problems(source, graph)})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"ComfyUI недоступен: {exc}") from exc
        return {"edits": result}

    @router.post("/api/jobs/{job_id}/comfyui-edits/{edit_id}/render", status_code=202)
    async def render_comfyui_edit(job_id: str, edit_id: str) -> dict:
        """The GPU API reads and checks the edit itself; a Studio job row follows its acceptance.
        It bypasses the Mac FIFO, so it waits for an empty, unpaused queue instead."""
        import httpx
        source, remote = remote_of(job_id)
        if ctx.store.queue_paused():
            raise HTTPException(409, "Очередь на паузе")
        if ctx.store.active_job():
            raise HTTPException(409, "На GPU уже идёт задание: рендер правки — после него")
        new_id = str(uuid.uuid4())
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(f"{ctx.gpu_url}/api/graph-jobs", json={
                    "source_job_id": remote, "edit_id": edit_id, "client_job_id": new_id})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"GPU API недоступен: {exc}") from exc
        if response.status_code >= 400:
            detail = response.json().get("detail") if "json" in response.headers.get("content-type", "") else response.text
            raise HTTPException(response.status_code if response.status_code < 500 else 502, detail)
        accepted = response.json()
        edit = {**(accepted.get("director") or {}).get("comfyui_edit", {}), "edit_id": edit_id,
                "source_job_id": job_id, "source_remote_job_id": remote}
        return ctx.store.add_comfyui_edit_job(source, new_id, accepted["job_id"], edit,
                                              json.dumps(accepted.get("director"), ensure_ascii=False, sort_keys=True))

    @router.get("/api/jobs/{job_id}/reproducibility")
    async def get_job_reproducibility(job_id: str) -> dict:
        from ..reproducibility import job_reproducibility
        job = ctx.store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        return await asyncio.to_thread(job_reproducibility, ctx.store, job)

    async def gpu_answers() -> bool:
        """Two health probes 2 s apart, so a tunnel blip never closes a live GPU render."""
        import httpx
        if not ctx.gpu_url:
            return False
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=4) as client:
                    if (await client.get(f"{ctx.gpu_url}/api/health")).status_code < 500:
                        return True
            except httpx.HTTPError:
                pass
            if not attempt:
                await asyncio.sleep(2)
        return False

    @router.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str) -> dict:
        """Guarded state transitions only (expected_states), never the dispatch lock: a cancel used to
        wait up to 30 min behind an upload or download in progress."""
        store = ctx.store
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        if job["state"] == "queued_local":
            # cancel_queued is a guarded UPDATE ... WHERE state='queued_local' (ValueError if dispatch won).
            try:
                return store.cancel_queued(job_id)
            except ValueError:
                job = store.get_job(job_id)
        if job["state"] == "needs_reconciliation":
            # Never resubmitted anyway; cancelling only stops the background check by client ID.
            try:
                return store.transition(job_id, "cancelled", expected_states=("needs_reconciliation",),
                                        error="Отменено пользователем до сверки; если GPU успел принять задание, "
                                              "его результат не будет скачан")
            except StaleTransition:
                job = store.get_job(job_id)
        if job["state"] not in {"queued_gpu", "running"} or not job["remote_job_id"]:
            raise HTTPException(409, "Это задание уже нельзя прервать")
        try:
            cancelled = await ctx.gpu_transport.cancel(job["remote_job_id"])
        except Exception as exc:
            cloud = job["remote_job_id"].startswith("minimax:")
            if not cloud and not await gpu_answers():
                # The GPU server is gone (e.g. a dead Vast host): «Прервать» closes the job here so the
                # FIFO and the queue card stop waiting for it. A server that comes back keeps its own copy.
                try:
                    return store.transition(job_id, "interrupted", expected_states=("queued_gpu", "running"),
                                            error="GPU-сервер недоступен: задание закрыто по «Прервать» как "
                                                  "потерянное, автоповтор отключён")
                except StaleTransition:
                    raise HTTPException(409, "Задание уже перешло в другой статус") from None
            raise HTTPException(502, f"Не удалось связаться с {'MiniMax API' if cloud else 'GPU'} для прерывания") from exc
        if not cancelled:
            if job["remote_job_id"].startswith("minimax:"):
                raise HTTPException(409, "MiniMax API разрешает отменить только ожидающее задание; выполняющееся прервать нельзя")
            raise HTTPException(409, "GPU уже завершил это задание")
        try:
            return store.transition(job_id, "stopping", expected_states=("queued_gpu", "running"))
        except StaleTransition:
            raise HTTPException(409, "Задание уже завершилось на GPU") from None

    @router.get("/api/jobs/{job_id}/preview")
    async def job_preview(job_id: str) -> Response:
        job = ctx.store.get_job(job_id)
        if not job or job["state"] not in {"running", "stopping"} or not job["remote_job_id"]:
            raise HTTPException(404, "Предпросмотр недоступен")
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                remote = await client.get(f"{ctx.gpu_url}/api/jobs/{job['remote_job_id']}/preview")
        except httpx.HTTPError as exc:
            raise HTTPException(502, "GPU не отвечает") from exc
        if remote.status_code != 200:
            raise HTTPException(404, "Предпросмотр ещё не получен")
        return Response(remote.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/api/jobs/{job_id}/video")
    async def video(job_id: str, download: bool = Query(False)) -> FileResponse:
        path = _finished_clip(ctx.store, job_id)
        return FileResponse(path, media_type="video/mp4",
                            filename=f"h3-{job_id}.mp4" if download else None)

    @router.get("/api/jobs/{job_id}/poster")
    async def video_poster(job_id: str) -> FileResponse:
        """First frame of a finished clip as a small JPEG: Safari draws a black box for
        preload=metadata videos, so the results gallery and Story cards show this poster."""
        path = _finished_clip(ctx.store, job_id)
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(503, "ffmpeg недоступен")
        poster_dir = ctx.data_root / "posters"
        poster_dir.mkdir(mode=0o700, exist_ok=True)
        poster = poster_dir / f"{job_id}.jpg"
        if not poster.is_file():
            temporary = poster_dir / f".{job_id}.{uuid.uuid4().hex}.jpg"
            try:
                process = await asyncio.create_subprocess_exec(
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.1", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale='min(540,iw)':-2", "-q:v", "4", "-y", str(temporary),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(process.communicate(), timeout=15)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.communicate()
                    raise HTTPException(422, "Не удалось получить кадр ролика")
                if process.returncode != 0 or not temporary.is_file():
                    raise HTTPException(422, "Не удалось получить кадр ролика")
                os.replace(temporary, poster)
            finally:
                temporary.unlink(missing_ok=True)
        return FileResponse(poster, media_type="image/jpeg")

    @router.delete("/api/jobs/{job_id}")
    async def delete_result(job_id: str) -> dict:
        store = ctx.store
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(404, "Задание не найдено")
        if any(item["source_job_id"] == job_id and item["state"] in {"queued_local", "uploading", "needs_reconciliation"}
               for item in ctx.upscale_queue.list()):
            raise HTTPException(409, "Исходный MP4 нужен для ожидающего апскейла")
        try:
            if job["state"] in {"failed", "cancelled"}:
                store.hide_failed_job(job_id)
                return {"hidden": True, "record_preserved": True}
            store.hide_completed_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"hidden": True, "media_recoverable_in": str(store.results_dir / "trash")}

    @router.delete("/api/history")
    async def clear_history() -> dict:
        # clear_visible_jobs is one BEGIN IMMEDIATE transaction touching only queued_local/finished jobs;
        # the dispatcher's own transitions are guarded by expected_states, so no dispatch lock is needed.
        from ..side_jobs import hide_side_jobs
        try:
            result = ctx.store.clear_visible_jobs()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        # Finished upscale / keyframe / edit / sound cards go too; their files and rows stay.
        return {**result, "side_hidden": hide_side_jobs(ctx.store, ctx.side_queues())}

    return router
