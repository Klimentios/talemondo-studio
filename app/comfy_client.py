from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import json
import time
import uuid

# Long enough to share one read across a job's graph build, short enough that a new LoRA file shows up.
OBJECT_INFO_TTL_S = 30


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:18188") -> None:
        self.base_url = base_url.rstrip("/")
        self.sampling_progress: dict[str, dict[str, int]] = {}
        self.preview_frames: dict[str, bytes] = {}
        self._progress_tasks: set[asyncio.Task] = set()
        self._watchers: dict[str, asyncio.Task] = {}
        self.node_timings: dict[str, dict[str, float]] = {}
        self._node_started: dict[str, tuple[str, float]] = {}
        self.finished_timings: dict[str, dict] = {}
        self.execution_observations: dict[str, dict] = {}
        # A/B jobs only (track_vram): peak GPU memory seen in /system_stats while the job runs.
        self.vram_peaks: dict[str, dict] = {}
        self._object_info_body: tuple[float, bytes] | None = None
        self._object_info_lock = asyncio.Lock()

    def _stop_progress(self, prompt_id: str) -> None:
        task = self._watchers.pop(prompt_id, None)
        if task:
            task.cancel()
        self.sampling_progress.pop(prompt_id, None)
        self.preview_frames.pop(prompt_id, None)
        self.node_timings.pop(prompt_id, None)
        self._node_started.pop(prompt_id, None)

    def _record_executing(self, prompt_id: str | None, node: str | None) -> None:
        if not prompt_id:
            return
        now = time.monotonic()
        previous = self._node_started.pop(prompt_id, None)
        if previous:
            timings = self.node_timings.setdefault(prompt_id, {})
            timings[previous[0]] = timings.get(previous[0], 0.0) + now - previous[1]
        if node is not None:
            self._node_started[prompt_id] = (str(node), now)

    def _finish_timing(self, prompt_id: str, entry: dict) -> dict:
        if prompt_id in self.finished_timings:
            return self.finished_timings[prompt_id]
        stamps = {m[0]: m[1].get("timestamp") for m in entry.get("status", {}).get("messages", [])
                  if isinstance(m, list) and len(m) == 2 and isinstance(m[1], dict)}
        total = None
        if isinstance(stamps.get("execution_start"), (int, float)) and \
                isinstance(stamps.get("execution_success"), (int, float)):
            total = round((stamps["execution_success"] - stamps["execution_start"]) / 1000, 1)
        phases: dict[str, float] = {}
        for node, seconds in self.node_timings.get(prompt_id, {}).items():
            phase = _node_phase(node)
            phases[phase] = round(phases.get(phase, 0.0) + seconds, 1)
        timing = {"total_seconds": total, "phases": phases}
        self.finished_timings[prompt_id] = timing
        while len(self.finished_timings) > 200:
            self.finished_timings.pop(next(iter(self.finished_timings)))
        print(f"H3 timing prompt={prompt_id} total={total}s phases={json.dumps(phases, sort_keys=True)}", flush=True)
        return timing

    async def _watch_progress(self, client_id: str, ready: asyncio.Event,
                              prompt_id_hint: str | None = None) -> None:
        import websockets

        url = self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        try:
            async with websockets.connect(f"{url}/ws?clientId={client_id}", max_size=2**20) as socket:
                ready.set()
                current_prompt_id = prompt_id_hint
                while True:
                    message = await socket.recv()
                    if not isinstance(message, str):
                        if (current_prompt_id and len(message) > 8
                                and int.from_bytes(message[:4], "big") == 1
                                and int.from_bytes(message[4:8], "big") == 1
                                and message[8:10] == b"\xff\xd8"
                                and len(message) <= 2**20):
                            self.preview_frames[current_prompt_id] = message[8:]
                        continue
                    event = json.loads(message)
                    if event.get("type") == "executing":
                        current_prompt_id = event.get("data", {}).get("prompt_id") or current_prompt_id
                        self._record_executing(current_prompt_id, event.get("data", {}).get("node"))
                    data = event.get("data", {})
                    event_type = event.get("type")
                    prompt_id = data.get("prompt_id") or current_prompt_id
                    if event_type == "progress_state":
                        nodes = data.get("nodes", {})
                        sampler = next((nodes.get(node) for node in ("125", "50", "34")
                                        if nodes.get(node)), {})
                        value, maximum = sampler.get("value"), sampler.get("max")
                    elif event_type == "progress" and str(data.get("node", "125")) in {"125", "50", "34"}:
                        value, maximum = data.get("value"), data.get("max")
                    else:
                        continue
                    current_prompt_id = prompt_id or current_prompt_id
                    if prompt_id and isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
                        self.sampling_progress[prompt_id] = {
                            "value": min(int(value), int(maximum)), "max": int(maximum),
                        }
        except (OSError, ValueError, json.JSONDecodeError, websockets.exceptions.WebSocketException):
            pass  # A lost progress stream must not interrupt the render.
        finally:
            ready.set()

    async def health(self) -> dict:
        import httpx

        # Model downloads and graph discovery can make ComfyUI's stats endpoint
        # respond slowly even though the engine is healthy.
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(f"{self.base_url}/system_stats")
            response.raise_for_status()
            return response.json()

    async def object_info(self) -> dict:
        """ComfyUI /object_info (~2.5 MB, model folders rescanned on every GET). One job reads it
        up to five times, so the body is kept OBJECT_INFO_TTL_S and parsed fresh per call:
        callers may mutate their copy. A refused /prompt drops the cache (see queue)."""
        import httpx

        async with self._object_info_lock:
            cached = self._object_info_body
            if cached is None or time.monotonic() - cached[0] >= OBJECT_INFO_TTL_S:
                async with httpx.AsyncClient(timeout=60) as client:
                    response = await client.get(f"{self.base_url}/object_info")
                    response.raise_for_status()
                cached = self._object_info_body = (time.monotonic(), response.content)
        payload = json.loads(cached[1])
        if not isinstance(payload, dict):
            self._object_info_body = None
            raise RuntimeError("ComfyUI /object_info вернул неверный формат")
        return payload

    async def bridge(self, path: str) -> dict | None:
        """h3-studio-bridge GET (/h3/studio/...); None when that graph or edit is unknown."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/h3/studio/{path}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def embeddings(self) -> list[str]:
        """Names in models/embeddings without extension (ComfyUI GET /embeddings)."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/embeddings")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise RuntimeError("ComfyUI /embeddings вернул неверный формат")
            return [str(item) for item in payload]

    async def release_models_if_idle(self) -> None:
        """Free completed H3 weights before the loopback Qwen Director wakes."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            queue_response = await client.get(f"{self.base_url}/queue")
            queue_response.raise_for_status()
            queue = queue_response.json()
            if queue.get("queue_running") or queue.get("queue_pending"):
                raise RuntimeError("ComfyUI занят другим заданием; Qwen Director не запущена")
            response = await client.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
            )
            response.raise_for_status()
            # ComfyUI may finish dropping the last VAE/cache objects after the
            # first response. A second pass makes the Qwen wake deterministic.
            await asyncio.sleep(2)
            response = await client.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
            )
            response.raise_for_status()
        await asyncio.sleep(1)

    async def upload_file(self, filename: str, data: bytes, content_type: str) -> str:
        import httpx

        safe_name = f"studio-{uuid.uuid4().hex}-{filename.rsplit('/', 1)[-1]}"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url}/upload/image",
                files={"image": (safe_name, data, content_type)},
                data={"type": "input", "overwrite": "false"},
            )
            response.raise_for_status()
            payload = response.json()
            subfolder = payload.get("subfolder", "")
            return f"{subfolder}/{payload['name']}".lstrip("/")

    async def upload_image(self, filename: str, data: bytes, content_type: str) -> str:
        return await self.upload_file(filename, data, content_type)

    async def queue(self, graph: dict, prompt_id: str | None = None) -> str:
        """Queue a graph; `prompt_id` (canonical UUID) is minted by the caller so a crash
        between this POST and the registry write can be reconciled from ComfyUI later.
        ComfyUI builds without client prompt ids ignore it and return their own id."""
        import httpx

        client_id = uuid.uuid4().hex
        ready = asyncio.Event()
        task = asyncio.create_task(self._watch_progress(client_id, ready))
        self._progress_tasks.add(task)
        task.add_done_callback(self._progress_tasks.discard)
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
        except TimeoutError:
            pass
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/prompt",
                    json={"prompt": graph, "client_id": client_id,
                          **({"prompt_id": prompt_id} if prompt_id else {}),
                          "extra_data": {"preview_method": "latent2rgb"}},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("node_errors"):
                    raise RuntimeError(f"ComfyUI rejected the graph: {payload['node_errors']}")
                prompt_id = payload["prompt_id"]
                self._watchers[prompt_id] = task
                return prompt_id
        except Exception:
            task.cancel()
            # Graph built from a stale node list (e.g. ComfyUI restarted with other nodes): reread it.
            self._object_info_body = None
            raise

    async def cache_metrics(self, prompt_id: str):
        import httpx
        from .cache_metrics import validated_metrics
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self.base_url}/h3/cache-metrics/{prompt_id}")
                response.raise_for_status()
                return validated_metrics(response.json(), prompt_id)
        except (httpx.HTTPError, ValueError):
            return None

    def _history_status(self, prompt_id: str, history: dict) -> dict | None:
        """Terminal status from a /history entry, or None while ComfyUI has none."""
        if prompt_id not in history:
            return None
        entry = history[prompt_id]
        status = entry.get("status") or {}
        if status.get("status_str") == "error":
            self._stop_progress(prompt_id)
            messages = status.get("messages", [])
            error = next(
                (_execution_error_text(m[1]) for m in messages
                 if isinstance(m, list) and len(m) == 2 and m[0] == "execution_error"
                 and isinstance(m[1], dict)),
                None,
            )
            if error is None and any(isinstance(m, list) and m and m[0] == "execution_interrupted"
                                     for m in messages):
                error = "Генерация прервана на GPU"
            return {"status": "error", "error": error or "Ошибка генерации"}
        video = _find_video(entry.get("outputs", {}))
        if video:
            timing = self._finish_timing(prompt_id, entry)
            self._stop_progress(prompt_id)
            result = {"status": "success", "video": video, "timing": timing}
            text = entry.get("outputs", {}).get("502", {}).get("text", [])
            if text:
                try:
                    report = json.loads(text[0])
                    if isinstance(report.get("missing_frames"), dict):
                        result["refinement_report"] = report
                except (ValueError, TypeError, AttributeError):
                    pass
            return result
        # ComfyUI writes history only when the prompt is done: finished without an MP4.
        self._stop_progress(prompt_id)
        return {"status": "error", "error": "ComfyUI завершил задание без MP4 на выходе"}

    def track_vram(self, prompt_id: str) -> None:
        """Sample GPU memory on every status poll of this job (A/B harness; Studio jobs skip it)."""
        self.vram_peaks.setdefault(prompt_id, {"peak_used_gb": None, "total_gb": None, "samples": 0,
                                               "source": "comfyui /system_stats while polled"})
        while len(self.vram_peaks) > 200:
            self.vram_peaks.pop(next(iter(self.vram_peaks)))

    async def _sample_vram(self, client, prompt_id: str) -> None:
        peak = self.vram_peaks.get(prompt_id)
        if peak is None:
            return
        try:
            response = await client.get(f"{self.base_url}/system_stats", timeout=3)
            response.raise_for_status()
            device = next(item for item in response.json().get("devices") or [] if item.get("vram_total"))
            total, free = float(device["vram_total"]), float(device["vram_free"])
        except Exception:  # a slow or odd stats endpoint never affects the job status
            return
        used = round((total - free) / 2**30, 2)
        peak["samples"] += 1
        peak["total_gb"] = round(total / 2**30, 2)
        if peak["peak_used_gb"] is None or used > peak["peak_used_gb"]:
            peak["peak_used_gb"] = used

    async def status(self, prompt_id: str) -> dict:
        import httpx

        result = await self._status(prompt_id)
        if prompt_id in self.vram_peaks:
            if result.get("status") == "running":
                async with httpx.AsyncClient(timeout=5) as client:
                    await self._sample_vram(client, prompt_id)
            result = {**result, "vram": dict(self.vram_peaks[prompt_id])}
        return result

    async def _status(self, prompt_id: str) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            async def history() -> dict | None:
                response = await client.get(f"{self.base_url}/history/{prompt_id}")
                response.raise_for_status()
                history_data = response.json()
                result = self._history_status(prompt_id, history_data)
                if result:
                    from .execution_evidence import history_evidence
                    evidence = self.execution_observations.get(prompt_id)
                    entry = history_data.get(prompt_id) or {}
                    if evidence is None and history_evidence(prompt_id, entry) is not None:
                        stats = None
                        try:
                            observed = await client.get(f"{self.base_url}/system_stats", timeout=3)
                            observed.raise_for_status()
                            stats = observed.json()
                        except (httpx.HTTPError, ValueError):
                            pass  # Missing runtime metadata must not lose a completed result.
                        evidence = history_evidence(prompt_id, entry, stats if isinstance(stats, dict) else None)
                        try:
                            context_response = await client.get(f"{self.base_url}/h3/studio/execution/{prompt_id}", timeout=3)
                            context_response.raise_for_status()
                            context = context_response.json()
                            if isinstance(context, dict):
                                from .execution_evidence import merge_prompt_context
                                evidence = merge_prompt_context(evidence, context)
                        except (httpx.HTTPError, ValueError):
                            pass
                        self.execution_observations[prompt_id] = evidence
                        while len(self.execution_observations) > 200:
                            self.execution_observations.pop(next(iter(self.execution_observations)))
                    if evidence is not None:
                        result["execution_observation"] = evidence
                return result

            done = await history()
            if done:
                return done
            queue_response = await client.get(f"{self.base_url}/queue")
            queue_response.raise_for_status()
            queue = queue_response.json()
            running = next((item for item in queue.get("queue_running", []) if item[1] == prompt_id), None)
            if running:
                if (prompt_id not in self._watchers or self._watchers[prompt_id].done()) and len(running) > 3:
                    client_id = running[3].get("client_id") if isinstance(running[3], dict) else None
                    if client_id:
                        task = asyncio.create_task(self._watch_progress(client_id, asyncio.Event(), prompt_id))
                        self._progress_tasks.add(task)
                        task.add_done_callback(self._progress_tasks.discard)
                        self._watchers[prompt_id] = task
                return {"status": "running", "sampling_progress": self.sampling_progress.get(prompt_id)}
            if any(item[1] == prompt_id for item in queue.get("queue_pending", [])):
                return {"status": "queued"}
            # The prompt may have finished between the /history and /queue reads
            # (ComfyUI moves it from running to history atomically, but we read twice).
            done = await history()
            if done:
                return done
            self._stop_progress(prompt_id)
            return {"status": "unknown"}

    async def cancel(self, prompt_id: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/api/jobs/{prompt_id}/cancel")
            if response.status_code == 404:
                return False
            response.raise_for_status()
            return response.json().get("cancelled") is True

    async def image_result(self, prompt_id: str) -> dict | None:
        """The PNG a finished image graph (SaveImage) wrote, or None while there is none."""
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            entry = response.json().get(prompt_id) or {}
        if (entry.get("status") or {}).get("status_str") == "error":
            return None
        for output in (entry.get("outputs") or {}).values():
            for item in output.get("images", []):
                if item.get("filename", "").lower().endswith(".png") and item.get("type") == "output":
                    return item
        return None

    async def stream_video(self, video: dict) -> AsyncIterator[bytes]:
        import httpx

        params = {
            "filename": video["filename"],
            "subfolder": video.get("subfolder", ""),
            "type": video.get("type", "output"),
        }
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", f"{self.base_url}/view", params=params) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk


def _execution_error_text(error: dict) -> str | None:
    """ComfyUI's exception_message, or "<type> в узле <node>" when it is blank: an EOFError from an
    empty checkpoint has the message "\\n", and Studio showed the job as failed with no reason."""
    message = str(error.get("exception_message") or "").strip()
    if message:
        return error.get("exception_message")
    kind = str(error.get("exception_type") or "").strip()
    node = str(error.get("node_type") or "").strip()
    if not kind and not node:
        return None
    return f"{kind or 'Ошибка'} в узле {node}" if node else kind


def _find_video(outputs: dict) -> dict | None:
    for output in outputs.values():
        for key in ("videos", "images"):
            for item in output.get(key, []):
                if item.get("filename", "").lower().endswith(".mp4") and item.get("type") == "output":
                    return item
    return None


_PHASES = {
    "119": "load", "120": "load", "127": "load", "128": "load",
    "136": "reference_and_text", "330": "camera_guide",
    "310": "model_patch", "311": "model_patch",
    "123": "sampling", "124": "sampling", "125": "sampling", "126": "sampling", "129": "sampling",
    "121": "decode", "122": "decode", "130": "save", "92": "save",
}


def _node_phase(node: str) -> str:
    if node in _PHASES:
        return _PHASES[node]
    if node.isdigit() and 320 <= int(node) < 330:
        return "scene_guides"
    if node.isdigit() and 137 <= int(node) < 300:
        return "inputs"
    return "other"
