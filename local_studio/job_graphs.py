"""The exact ComfyUI graph of an H3 job, kept on this Mac.

The graph lives in the GPU's ComfyUI (h3-studio-bridge /h3/studio/graph/<prompt id>) and dies with the rented
server. Studio copies it into <Studio data>/job-graphs/<job id>.json when a job completes and whenever it is read,
so a job or an accepted recipe can be opened, forked into «Свои воркфлоу» and edited after the server is gone.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
JOB_ID = re.compile(r"[0-9A-Za-z_-]{1,80}")


class JobGraphMissing(LookupError):
    pass


class JobGraphs:
    def __init__(self, data_root: Path, comfy_url: str) -> None:
        self.folder = data_root / "job-graphs"
        self.comfy_url = comfy_url

    def _path(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id or ""):
            raise JobGraphMissing("Некорректный id задания")
        return self.folder / f"{job_id}.json"

    def cached(self, job_id: str) -> dict | None:
        path = self._path(job_id)
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None

    def _save(self, job_id: str, remote: str, graph: dict) -> dict:
        from datetime import datetime, timezone
        record = {"job_id": job_id, "remote_job_id": remote, "graph": graph,
                  "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(job_id)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return record

    async def _fetch(self, remote: str) -> dict | None:
        if not self.comfy_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{self.comfy_url}/h3/studio/graph/{remote}")
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        graph = (response.json() or {}).get("prompt")
        return graph if isinstance(graph, dict) and graph else None

    async def get(self, store, job_id: str) -> dict:
        """{job_id, remote_job_id, graph, source: comfyui|archive}; JobGraphMissing when neither has it."""
        job = store.get_job(job_id)
        if not job:
            raise JobGraphMissing("Задание не найдено")
        remote = job.get("remote_job_id")
        if remote:
            graph = await self._fetch(remote)
            if graph:
                return {**self._save(job_id, remote, graph), "source": "comfyui"}
        archived = self.cached(job_id)
        if archived:
            return {**archived, "source": "archive"}
        if not remote:
            raise JobGraphMissing("Задание ещё не дошло до ComfyUI: графа нет")
        raise JobGraphMissing("Граф этого задания хранился на GPU-сервере, где шёл рендер, и в архив Studio не попал "
                              "(задание старше архива или сервер уже снят)")

    async def archive(self, store, job_id: str) -> None:
        """After a job completes: keep its graph. Best effort, never fails the job."""
        try:
            if not self.cached(job_id):
                await self.get(store, job_id)
        except Exception as exc:  # the MP4 is what matters; a missing graph copy is only logged
            log.info("job graph %s not archived: %s", job_id, exc)
