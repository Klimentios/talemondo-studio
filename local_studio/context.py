"""The Studio's shared services in one object. Routes read them from here at request time,
so a test (or a reconnect) that swaps ctx.store, ctx.gpu_url or ctx.gpu_transport takes effect."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .compute.manager import ComputeManager
from .context_ir import ContextQueue
from .audio_jobs import AudioQueue
from .motion_jobs import MotionQueue
from .user_workflows import WorkflowLibrary, WorkflowQueue
from .comfy_manager import ComfyManifest, Inventory
from .comfy_schema import ObjectInfoCache
from .job_graphs import JobGraphs
from .production import ProductionQueue
from .store import StudioStore
from .upscale import UpscaleQueue
from .worker import GpuBackoff, HttpGpuTransport
from .minimax_cloud import MiniMaxCloudTransport, RoutingTransport


class StudioContext:
    def __init__(self, *, root: Path, data_root: Path, gpu_url: str, comfy_url: str) -> None:
        self.root = root
        self.data_root = data_root
        self.gpu_url = gpu_url
        self.comfy_url = comfy_url
        self.store = StudioStore(data_root)
        self.dispatch_lock = asyncio.Lock()
        self.gpu_transport = RoutingTransport(HttpGpuTransport(gpu_url), MiniMaxCloudTransport(data_root), self.store)
        # 3 s between dispatcher passes while the GPU answers, up to 30 s while it is unreachable.
        self.gpu_backoff = GpuBackoff()
        self.upscale_queue = UpscaleQueue(self.store, gpu_url)
        self.audio_queue = AudioQueue(self.store, gpu_url)
        self.motion_queue = MotionQueue(self.store, comfy_url)
        self.workflow_library = WorkflowLibrary(data_root)
        self.workflow_queue = WorkflowQueue(self.store, comfy_url, self.workflow_library)
        self.comfy_manifest = ComfyManifest(data_root)
        # One /object_info cache for every lane that edits or checks a graph (app/graph_ops.py).
        self.comfy_schema = ObjectInfoCache(comfy_url)
        self.comfy_inventory = Inventory(comfy_url, self.comfy_schema)
        self.job_graphs = JobGraphs(data_root, comfy_url)
        self.context_queue = ContextQueue(self.store, gpu_url)
        self.production_queue = ProductionQueue(self.store, self.context_queue, lambda: self.style_adapters_or_none())
        self.compute_manager = ComputeManager(data_root, busy=self.compute_busy, repo_root=root, gpu_url=gpu_url)

    def compute_busy(self) -> bool:
        return bool(self.store.active_job() or self.store.oldest_pending() or
                    self.upscale_queue.first_active() or self.audio_queue.first_active() or
                    self.context_queue.db.active() or self.motion_queue.first_active() or
                    self.workflow_queue.first_active() or
                    self.production_queue.list(active=True))

    def side_queues(self) -> dict:
        return {"upscale": self.upscale_queue, "audio": self.audio_queue, "motion": self.motion_queue,
                "workflow": self.workflow_queue}

    async def style_adapters(self) -> set[str]:
        """Style LoRAs the GPU really has (its /api/capabilities). Unreachable GPU: none, only 'none' is allowed."""
        return await self.style_adapters_or_none() or set()

    async def style_adapters_or_none(self) -> set[str] | None:
        """Like style_adapters(), but None when the GPU is not configured or not answering."""
        if not self.gpu_url:
            return None
        try:
            caps = await self.gpu_transport.capabilities()
        except Exception:
            return None
        adapters = caps.get("style_adapters")
        return {item for item in adapters if isinstance(item, str)} if isinstance(adapters, list) else set()
