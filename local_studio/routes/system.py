"""Health and what the connected GPU can do: /api/health, studio capabilities, render options,
character-refinement availability, live ComfyUI activity, the Director schema."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import FileResponse
from ..motion_director import RerunPlan, CompileTimeline, GraphPlan, upload_image, upload_video


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.post("/api/studio/motion-director/preflight")
    async def motion_director_preflight(payload: GraphPlan) -> dict:
        import httpx
        from scripts.h3_graph_validate import validate
        from fastapi import HTTPException
        try:
            prepared = payload.graph()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not ctx.comfy_url:
            return {**prepared, "problems": ["ComfyUI не настроен"], "ready_to_submit": False}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{ctx.comfy_url}/object_info")
                response.raise_for_status()
                info = response.json()
                if not isinstance(info, dict):
                    raise ValueError("Некорректная схема узлов ComfyUI")
                problems = validate(prepared["graph"], info)
            return {**prepared, "graph_verified": not problems, "problems": problems,
                    "ready_to_submit": False,
                    "note": "Проверена схема графа и доступность моделей; наличие загруженных медиа, лицензия и приёмка постановки проверяются отдельно."}
        except (httpx.HTTPError, ValueError):
            return {**prepared, "problems": ["Не удалось получить схему узлов ComfyUI"], "ready_to_submit": False}

    @router.post("/api/studio/motion-director/graph")
    async def motion_director_graph(payload: GraphPlan) -> dict:
        from fastapi import HTTPException
        try:
            return payload.graph()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/studio/motion-director/videos/{asset_id}")
    async def motion_director_upload_video(asset_id: str) -> dict:
        return await transfer_media(asset_id, upload_video)

    @router.post("/api/studio/motion-director/assets/{asset_id}")
    async def motion_director_upload(asset_id: str) -> dict:
        return await transfer_media(asset_id, upload_image)

    async def transfer_media(asset_id, uploader) -> dict:
        import httpx
        from fastapi import HTTPException
        if not ctx.comfy_url:
            raise HTTPException(503, "ComfyUI не настроен")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                return await uploader(ctx.store, client, ctx.comfy_url, asset_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "ComfyUI не подтвердил загрузку; генерация не запускалась") from exc

    @router.post("/api/studio/motion-director/compile")
    async def motion_director_compile(payload: CompileTimeline) -> dict:
        from fastapi import HTTPException
        try:
            return payload.compile()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/api/studio/motion-director/rerun-plan")
    async def motion_director_rerun_plan(payload: RerunPlan) -> dict:
        return payload.plan()

    @router.get("/api/health")
    async def health() -> dict:
        reachable = False
        if ctx.gpu_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=4) as client:
                    response = await client.get(f"{ctx.gpu_url}/api/health")
                    reachable = response.status_code == 200 and response.json().get("engine") == "ready"
            except (httpx.HTTPError, ValueError, AttributeError):  # AttributeError: a non-object body
                pass
            if reachable:
                ctx.gpu_backoff.ok()  # tunnel is back: wake a backed-off dispatcher now
        cloud = getattr(ctx.gpu_transport, "cloud", None)
        return {"status": "ok", "gpu_configured": bool(ctx.gpu_url),
                "minimax_api_configured": bool(cloud and cloud.configured()),
                "video_backend": cloud.default_backend() if cloud else "gpu",
                "gpu_reachable": reachable, "queue_owner": "mac", "franchises_v1": True, "analytics_v1": True,
                "style_adapters": sorted(await ctx.style_adapters()) if reachable else []}

    @router.get("/api/studio/video-backend")
    async def video_backend() -> dict:
        cloud = getattr(ctx.gpu_transport, "cloud", None)
        return {"video_backend": cloud.default_backend() if cloud else "gpu",
                "minimax_api_configured": bool(cloud and cloud.configured()), "choices": ["gpu", "minimax_api"]}

    @router.put("/api/studio/video-backend")
    async def set_video_backend(payload: dict) -> dict:
        """The «Свой GPU / MiniMax API» switch. Applies to new jobs; queued jobs keep their route."""
        from fastapi import HTTPException
        cloud = getattr(ctx.gpu_transport, "cloud", None)
        if cloud is None:
            raise HTTPException(503, "Маршрут MiniMax API не подключён в этой Studio")
        try:
            value = cloud.set_default_backend(str(payload.get("video_backend", "")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"video_backend": value, "minimax_api_configured": cloud.configured()}

    @router.get("/api/studio/capabilities")
    async def studio_capabilities() -> dict:
        """Controls shown in Studio must reflect the connected GPU, not local code alone."""
        from app.contract import ASPECTS
        # Offline the formats come from the shared contract (app/ is the same on Mac and GPU); the worker checks the
        # live GPU list again before sending.
        offline = {"gpu_reachable": False, "camera_effects_v1": {"available": []},
                   "continuation_v1": False, "scene_guides": False, "aspect_ratios": list(ASPECTS)}
        if not ctx.gpu_url:
            return offline
        try:
            caps = await ctx.gpu_transport.capabilities()
        except Exception:
            return offline
        effects = caps.get("camera_effects_v1") or {}
        available = effects.get("available", []) if isinstance(effects, dict) else []
        return {"gpu_reachable": True,
                "camera_effects_v1": {"available": [x for x in available if isinstance(x, str)]},
                "continuation_v1": caps.get("continuation_v1") is True,
                "scene_guides": caps.get("scene_guides") is True,
                "aspect_ratios": [x for x in caps.get("aspect_ratios") or ["16:9", "9:16"] if isinstance(x, str)]}

    @router.get("/api/studio/render-options")
    async def studio_render_options() -> dict:
        """Local catalog plus live availability; no speculative enablement while GPU is offline."""
        from app.render_profiles import render_capability
        catalog = render_capability({})
        reachable = False
        try:
            caps = await ctx.gpu_transport.capabilities() if ctx.gpu_url else {}
            remote = caps.get("render_profiles_v1")
            if isinstance(remote, dict) and remote.get("version") == 1:
                catalog = remote
                reachable = True
        except Exception:
            pass
        return {**catalog, "gpu_reachable": reachable}

    @router.get("/api/studio/motion-director")
    async def motion_director_capability() -> dict:
        """Expose live ComfyUI node contract to Studio and MCP without starting a render."""
        from scripts.h3_motion_director_probe import probe
        if not ctx.comfy_url:
            return {"installed": False, "api_compatible": False, "reason": "ComfyUI не настроен"}
        import httpx
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(f"{ctx.comfy_url}/object_info")
                response.raise_for_status()
                return probe(response.json())
        except (httpx.HTTPError, ValueError, TypeError):
            return {"installed": False, "api_compatible": False, "reason": "Не удалось прочитать /object_info ComfyUI"}

    @router.get("/api/character-refinement/capabilities")
    async def character_refinement_capabilities() -> dict:
        import httpx
        if not ctx.gpu_url:
            return {"available": False}
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{ctx.gpu_url}/api/capabilities")
                response.raise_for_status()
                return {"available": response.json().get("character_refinement_v1") is True}
        except (httpx.HTTPError, ValueError):
            return {"available": False}

    @router.get("/api/gpu/activity")
    async def gpu_activity() -> dict:
        """Read ComfyUI activity, including renders submitted outside Mac Studio."""
        offline = {"reachable": False, "running": [], "pending_count": 0}
        gpu_url, comfy_url = ctx.gpu_url, ctx.comfy_url
        if not gpu_url or not comfy_url:
            return offline
        import httpx

        async def progress_of(client, prompt_id: str) -> dict:
            detail = {"prompt_id": prompt_id, "status": "running", "sampling_progress": None}
            try:
                status = await client.get(f"{gpu_url}/api/jobs/{prompt_id}")
                progress = status.json().get("sampling_progress") if status.is_success else None
                if isinstance(progress, dict) and isinstance(progress.get("value"), int) and isinstance(progress.get("max"), int):
                    detail["sampling_progress"] = {"value": progress["value"], "max": progress["max"]}
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            return detail

        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{comfy_url}/queue")
                response.raise_for_status()
                queue = response.json()
                if not isinstance(queue, dict):
                    return offline
                ids = [item[1] for item in queue.get("queue_running", [])[:4]
                       if isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str)]
                running = await asyncio.gather(*(progress_of(client, prompt_id) for prompt_id in ids))
                return {"reachable": True, "running": list(running), "pending_count": len(queue.get("queue_pending", []))}
        except (httpx.HTTPError, ValueError, TypeError):
            return offline

    @router.get("/director-schema.json")
    async def director_schema() -> FileResponse:
        # Single source of Director fields, shared with the GPU API (app/director_config.py).
        return FileResponse(ctx.root / "app/director_schema.json", media_type="application/json",
                            headers={"Cache-Control": "no-cache"})

    return router
