"""Mac Studio app (uvicorn local_studio.main:app). Composition only: settings come from config.py,
shared services from one StudioContext (ctx), the dispatcher from dispatcher.py, and every HTTP
route from a make_router(...) module. Tests swap services on ctx (ctx.store, ctx.gpu_url, ...)."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .context import StudioContext
from .static_files import StudioStaticFiles
from .seed_hunt import make_router as make_seed_hunt_router
from .subscription_bridge import make_router as make_subscription_bridge_router
from .chat_images import make_router as make_chat_images_router
from .dispatcher import (RateLimitedErrors as _RateLimitedErrors, dispatch_loop, next_dispatch,  # noqa: F401  (tests import these here)
                         remove_stale_partials as _remove_stale_partials)
from .analytics import make_router as make_analytics_router
from .franchises import make_router as make_franchises_router
from .production import make_router as make_production_router
from .acting import make_router as make_acting_router
from .upscale import make_router as make_upscale_router
from .audio_project import make_router as make_audio_router
from .audio_jobs import make_router as make_audio_jobs_router
from .scenarist import make_router as make_scenarist_router
from .agent_brain import make_router as make_brain_router
from .agent_accounts import make_router as make_agent_accounts_router
from .agent_chat import make_router as make_agent_chat_router
from .recipes import make_router as make_recipes_router
from .production_review import make_router as make_production_review_router
from .production_spec import make_router as make_production_spec_router
from .visual_profiles import make_router as make_visual_profiles_router
from .orchestrator import make_router as make_orchestrator_router
from .decisions import make_router as make_decisions_router
from .ui_bridge import make_router as make_ui_bridge_router
from .shot_review import make_router as make_shot_review_router
from .edl import make_router as make_edl_router
from .montage import make_router as make_montage_router
from .franchise_profile import make_router as make_franchise_profile_router
from .idea_pack import make_router as make_idea_pack_router
from .staging import make_router as make_staging_router
from .scene_kit import make_router as make_scene_kit_router
from .previs import make_router as make_previs_router
from .codex_frames import make_router as make_codex_frames_router
from .previs_scene import make_router as make_previs_scene_router
from .context_ir import make_router as make_context_ir_router
from .context_dispatch import context_dispatch_loop
from .scene_planner import make_router as make_scene_planner_router
from .scene_qc import make_router as make_scene_qc_router
from .scenes import make_router as make_scenes_router
from .storyboard import make_router as make_storyboard_router
from .scene_runs import SceneRuns, make_router as make_scene_runs_router
from .studio_readiness import make_router as make_studio_readiness_router
from .video_swap import make_router as make_video_swap_router
from .video_swap_auto import make_router as make_video_swap_auto_router
from .world_swap_auto import make_router as make_world_swap_auto_router
from .snap_zoom import make_router as make_snap_zoom_router
from .motion_jobs import make_router as make_motion_jobs_router
from .user_workflows import make_router as make_workflows_router
from .comfy_manager import make_router as make_comfy_router
from .comfy_schema import make_router as make_comfy_schema_router
from .setup_status import make_router as make_setup_router
from .reference_segments import make_router as make_reference_segments_router
from .reference_excerpts import make_router as make_reference_excerpts_router
from .reshot_reference import make_router as make_reshot_reference_router
from .compute.router import make_router as make_compute_router
from .routes import assets as assets_routes, jobs as job_routes, system as system_routes

ctx = StudioContext(root=config.ROOT, data_root=config.DATA_ROOT, gpu_url=config.GPU_URL, comfy_url=config.COMFY_URL)
scene_runs = SceneRuns(lambda: ctx.data_root / "scene-runs")  # «Собрать сцену» goes on here without the tab
for _log in ("service.log", "service-error.log"):
    config.trim_log(config.DATA_ROOT / _log)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(dispatch_loop(ctx)) if ctx.gpu_url else None
    context_task = asyncio.create_task(context_dispatch_loop(ctx.context_queue))
    runs_task = asyncio.create_task(scene_runs.loop(app))
    try:
        yield
    finally:
        for pending in (task, context_task, runs_task):
            if pending is None:
                continue
            pending.cancel()
            try:
                await pending
            except asyncio.CancelledError:
                pass


app = FastAPI(title="H3 Mac Studio", version="0.1.0", lifespan=lifespan)
# Feature routers receive their services when the app is built (as before the split).
store, gpu_url = ctx.store, ctx.gpu_url
app.include_router(make_franchises_router(store))
app.include_router(make_recipes_router(store))
app.include_router(make_production_review_router(store))
app.include_router(make_production_spec_router(store))
app.include_router(make_visual_profiles_router())
app.include_router(make_franchise_profile_router(store))
app.include_router(make_idea_pack_router(store))  # after the profile router: it creates the profiles table
app.include_router(make_staging_router())
app.include_router(make_scene_kit_router(store))  # «Комплект сцены»: the only pictures, heroes and limits a scene may use
app.include_router(make_previs_router(store))  # «Превиз»: Blockout staging → «Композиция» frames
app.include_router(make_codex_frames_router(store))  # a still drawn by Codex (canvas fantasy world)
app.include_router(make_previs_scene_router(store))  # «Проверить в Blockout»: the scene as one grey reel
app.include_router(make_analytics_router(store))
app.include_router(make_acting_router(store, gpu_url, ctx.dispatch_lock))
app.include_router(make_upscale_router(ctx.upscale_queue))
app.include_router(make_audio_router(store, gpu_url))
app.include_router(make_audio_jobs_router(ctx.audio_queue))
app.include_router(make_scenarist_router(store, gpu_url))
app.include_router(make_brain_router())
app.include_router(make_agent_accounts_router())
agent_chats = make_agent_chat_router(store)
app.include_router(agent_chats)
app.include_router(make_subscription_bridge_router(store, agent_chats.hub))
app.include_router(make_orchestrator_router(store, agent_chats.hub))
app.include_router(make_decisions_router(store))
app.include_router(make_ui_bridge_router())
app.include_router(make_shot_review_router())
app.include_router(make_context_ir_router(ctx.context_queue, lambda: ctx.style_adapters_or_none()))
app.include_router(make_production_router(ctx.production_queue, ctx.dispatch_lock))
app.include_router(make_reference_excerpts_router(store))
app.include_router(make_reshot_reference_router(store))
app.include_router(make_scene_planner_router(gpu_url, store))
app.include_router(make_scene_qc_router(store))
app.include_router(make_scenes_router(store))
app.include_router(make_storyboard_router(store, scene_runs))
app.include_router(make_scene_runs_router(scene_runs))
app.include_router(make_studio_readiness_router(ctx, scene_runs))
app.include_router(make_motion_jobs_router(ctx))
app.include_router(make_workflows_router(ctx))  # «Свои воркфлоу»: any ComfyUI API graph as a button/API/agent tool
app.include_router(make_comfy_schema_router(ctx))  # node catalog + graph check/apply against /object_info (app/graph_ops.py)
app.include_router(make_comfy_router(ctx))  # «Узлы и модели»: GPU inventory + the user's ComfyUI manifest
app.include_router(make_setup_router(ctx))  # «Первый запуск»: checks, addresses, agent hookup
app.include_router(make_reference_segments_router(store))
app.include_router(make_edl_router(store))
app.include_router(make_montage_router(store))
app.include_router(make_compute_router(ctx.compute_manager))
del store, gpu_url  # read ctx.store / ctx.gpu_url: a module-level alias would go stale when a test swaps them
# Core routes read ctx at request time.
app.include_router(system_routes.make_router(ctx))
app.include_router(assets_routes.make_router(ctx))
app.include_router(make_chat_images_router(ctx))
app.include_router(make_video_swap_router(ctx))  # «Замена по видео»: the whole source as parts
app.include_router(make_video_swap_auto_router(ctx))  # …and its shot maps, prompts and queue without an agent's hands
app.include_router(make_world_swap_auto_router(ctx))  # «Смена мира по видео» built by Studio, one part per call
app.include_router(make_snap_zoom_router(ctx))  # «Резкий зум»: H3 paces a zoom itself, the snap is made after
app.include_router(job_routes.make_router(ctx))
app.include_router(make_seed_hunt_router(ctx))  # «🎲 Подобрать seed»: previews → finish


@app.middleware("http")
async def local_only(request: Request, call_next):
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    origin = request.headers.get("origin", "")
    if host not in {"127.0.0.1", "localhost"}:
        return JSONResponse({"detail": "Studio доступна только через localhost"}, status_code=403)
    parsed_origin = urlparse(origin) if origin else None
    if parsed_origin and (parsed_origin.scheme != "http" or parsed_origin.netloc.lower() != request.headers.get("host", "").lower()):
        return JSONResponse({"detail": "Недопустимый Origin"}, status_code=403)
    return await call_next(request)


app.mount("/", StudioStaticFiles(directory=config.ROOT / "preview/h3-director", html=True), name="studio")
