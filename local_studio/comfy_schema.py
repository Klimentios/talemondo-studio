"""The connected ComfyUI's node schema (/object_info) for every Studio lane that edits or checks a graph.

One cache (60 s) shared by «Узлы и модели», «Свои воркфлоу» and job graph edits; the engine is app/graph_ops.py.
Nothing here is specific to a node pack: a pack installed later (H3 Continuity Studio, …) is found, described,
added to graphs and checked the same way. Without a reachable ComfyUI an edit is refused, never applied blind.
"""
from __future__ import annotations

import asyncio
import time

import httpx


class ComfyUnavailable(RuntimeError):
    pass


class ObjectInfoCache:
    TTL_S = 60

    def __init__(self, comfy_url: str) -> None:
        self.comfy_url = comfy_url
        self._cache: tuple[float, dict] | None = None
        self._lock = asyncio.Lock()

    async def get(self, fresh: bool = False) -> dict:
        if not self.comfy_url:
            raise ComfyUnavailable("ComfyUI не настроен (H3_COMFY_URL): без /object_info граф не проверить")
        async with self._lock:
            if not fresh and self._cache and time.monotonic() - self._cache[0] < self.TTL_S:
                return self._cache[1]
            try:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.get(f"{self.comfy_url}/object_info")
                    response.raise_for_status()
                    info = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise ComfyUnavailable(f"ComfyUI недоступен ({type(exc).__name__}): без /object_info граф не проверить") from exc
            if not isinstance(info, dict) or not info:
                raise ComfyUnavailable("ComfyUI вернул пустой /object_info")
            self._cache = (time.monotonic(), info)
            return info


def make_router(ctx):
    from fastapi import APIRouter, HTTPException

    from app.graph_ops import GraphEditError, apply_ops, catalog, describe, graph_problems

    router = APIRouter()

    async def info(fresh: bool = False) -> dict:
        try:
            return await ctx.comfy_schema.get(fresh)
        except ComfyUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get("/api/comfy/classes")
    async def classes(q: str = "", limit: int = 50, fresh: bool = False):
        """Node classes of the live ComfyUI matching a search: inputs, outputs, pack."""
        data = await info(fresh)
        return {"total": len(data), "classes": catalog(data, q, limit)}

    @router.get("/api/comfy/classes/{class_type}")
    async def node_class(class_type: str):
        try:
            return describe(await info(), class_type)
        except GraphEditError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/api/comfy/graph/check")
    async def check(body: dict):
        """Any API graph against the live /object_info: every problem ComfyUI would report, no run."""
        from .user_workflows import check_api_graph
        try:
            graph = check_api_graph(body.get("graph"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        problems = graph_problems(graph, await info())
        return {"ok": not problems, "problems": problems, "nodes": len(graph)}

    @router.post("/api/comfy/graph/apply")
    async def apply(body: dict):
        """Dry run of graph operations on any graph: the result, the diff and the problems. Saves nothing."""
        from .user_workflows import check_api_graph
        try:
            graph = check_api_graph(body.get("graph"))
            return apply_ops(graph, body.get("ops"), await info())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
