"""MiniMax V2 video API transport for Studio's existing durable video queue.

The API key lives in the Studio data directory, never in a job or repository.
Cloud receipts are written before returning from submit; an ambiguous POST is
never automatically repeated because MiniMax V2 has no client-id lookup.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx

from .worker import PreEnqueueRejected, SubmitNotSent, sha256_file

BASE_URL = "https://api.minimax.io"
MODELS_768 = {"MiniMax-H3", "MiniMax-H3-Highspeed", "MiniMax-H3-Lite", "MiniMax-H3-Max"}
PREFIX = "minimax:"
# Studio-wide video route chosen by the user's switch: the rented GPU (ComfyUI
# graph, LoRA, steps, cache) or the MiniMax cloud API (768P 9:16, no LoRA).
VIDEO_BACKENDS = ("gpu", "minimax_api")
# The cloud swap recipe proven 02.11.2026 (removed video_swap.api_plan; route disabled 04.11): the API follows the source action only
# with a [Shot N] action map, body + face per hero, a franchise set picture and the SOURCE LEAK GUARD block.
API_SWAP_RECIPE = "minimax-api-action-map-v1"
LEAK_GUARD_MARKER = "SOURCE LEAK GUARD"


def swap_recipe_problems(prompt: str, images: list[dict]) -> list[str]:
    """Why a source-video swap job is not the cloud recipe; [] when it is. images: [{role, owner}]."""
    problems = []
    if "[Shot " not in prompt:
        problems.append("в промпте нет покадровой карты действий [Shot N]")
    if LEAK_GUARD_MARKER not in prompt:
        problems.append(f"в промпте нет блока {LEAK_GUARD_MARKER} из плана замены")
    owners = {i.get("owner") for i in images if i.get("owner") and i.get("role") == "Персонаж целиком"}
    for owner in sorted(owners):
        if not any(i.get("owner") == owner and i.get("role") == "Лицо героя" for i in images):
            problems.append(f"для {owner} нужно и «Персонаж целиком», и «Лицо героя»")
    if not owners:
        problems.append("нет фото героев «Персонаж целиком»")
    if not any(i.get("role") == "Окружение" for i in images):
        problems.append("нет референса «Окружение» франшизы")
    return problems


class MiniMaxCloudTransport:
    def __init__(self, data_root: Path):
        self.key_path = data_root / "minimax-api-key"
        self.backend_path = data_root / "video-backend"
        self.receipts_dir = data_root / "minimax-receipts"
        self.receipts_dir.mkdir(mode=0o700, exist_ok=True)

    def configured(self) -> bool:
        return self.key_path.is_file() and bool(self.key_path.read_text().strip())

    def default_backend(self) -> str:
        """The switch value; before the first switch: API when its key exists (prior behaviour)."""
        try:
            value = self.backend_path.read_text().strip()
        except OSError:
            value = ""
        if value in VIDEO_BACKENDS:
            return value
        return "minimax_api" if self.configured() else "gpu"

    def set_default_backend(self, value: str) -> str:
        if value not in VIDEO_BACKENDS:
            raise ValueError("Маршрут видео: gpu или minimax_api")
        if value == "minimax_api" and not self.configured():
            raise ValueError("Ключ MiniMax API не настроен в Studio")
        temp = self.backend_path.with_suffix(".tmp")
        temp.write_text(value)
        temp.replace(self.backend_path)
        return value

    def resolve_settings(self, settings: dict) -> dict:
        """Fill the route from the switch and normalise per-route fields; returns a new dict."""
        settings = dict(settings)
        backend = settings.get("backend") or self.default_backend()
        if backend not in VIDEO_BACKENDS:
            raise ValueError("Маршрут видео: gpu или minimax_api")
        if backend == "gpu":
            # GPU jobs have always been written without these keys; the GPU API never saw them.
            for key in ("backend", "cloud_model", "cloud_resolution"):
                settings.pop(key, None)
            return settings
        if not self.configured():
            raise ValueError("Ключ MiniMax API не настроен в Studio")
        if settings.get("cloud_resolution", "768P") != "768P":
            raise ValueError("MiniMax API в Studio сейчас принимает только 768P")
        # The cloud output is 768P regardless of local slider values.
        settings.update(backend="minimax_api", cloud_resolution="768P", cloud_model="MiniMax-H3",
                        quality="custom", megapixels=0.98)
        if settings.get("style_adapter") == "auto":
            settings["style_adapter"] = "none"
        return settings

    def _headers(self) -> dict[str, str]:
        if not self.key_path.is_file():
            raise ValueError("Ключ MiniMax API не настроен в Studio")
        key = self.key_path.read_text().strip()
        if not key:
            raise ValueError("Ключ MiniMax API пуст")
        return {"Authorization": f"Bearer {key}"}

    def _receipt(self, job_id: str) -> Path:
        if len(job_id) != 36 or any(c not in "0123456789abcdef-" for c in job_id):
            raise ValueError("Неверный локальный ID задания")
        return self.receipts_dir / f"{job_id}.json"

    async def lookup(self, job_id: str) -> dict | None:
        path = self._receipt(job_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text())
        return {"job_id": PREFIX + str(data["task_id"])}

    @staticmethod
    def validate(job: dict) -> tuple[dict, dict]:
        settings = json.loads(job["settings_json"])
        manifest = json.loads(job["manifest_json"])
        if settings.get("cloud_model", "MiniMax-H3") not in MODELS_768:
            raise ValueError("Неизвестная видеомодель MiniMax API")
        if settings.get("cloud_resolution", "768P") != "768P":
            raise ValueError("В Studio сейчас выбрана генерация MiniMax API 768P")
        duration = settings.get("duration")
        minimum = 5 if settings.get("cloud_model") == "MiniMax-H3-Max" else 4
        if type(duration) is not int or not minimum <= duration <= 15:
            raise ValueError(f"MiniMax API: длительность {minimum}–15 секунд")
        if settings.get("aspect_ratio") != "9:16":
            raise ValueError("MiniMax API Studio: только вертикальный формат 9:16")
        if settings.get("style_adapter", "none") != "none":
            raise ValueError("MiniMax API не принимает локальную LoRA; выбери «без LoRA» или локальный GPU")
        for key in ("character_refinement", "guide_frames", "render_options", "ab_turbo", "seed_hunt",
                    "color_match", "color_grade", "film_look", "camera_effect", "continuation_trim_frames"):
            if settings.get(key):
                raise ValueError(f"MiniMax API не поддерживает параметр {key}; задание не отправлено")
        if len(manifest.get("images", [])) > 9 or len(manifest.get("videos", [])) > 3 or len(manifest.get("audios", [])) > 3:
            raise ValueError("Превышен лимит референсов MiniMax API (9 фото, 3 видео, 3 аудио)")
        if sum(len(manifest.get(kind, [])) for kind in ("images", "videos", "audios")) > 12:
            raise ValueError("MiniMax H3 принимает не более 12 референсов всего")
        if len(job["compiled_prompt"]) > 7000:
            raise ValueError("MiniMax H3 принимает промпт до 7000 символов")
        # api_swap_trial: the user explicitly asked for an API swap probe; the result still needs full QC.
        swap = any(video.get("job_role", video.get("role")) == "Исходное видео" for video in manifest.get("videos", []))
        if swap and settings.get("swap_recipe") == API_SWAP_RECIPE:
            problems = swap_recipe_problems(job["compiled_prompt"], manifest.get("images", []))
            if problems:
                raise ValueError("Замена по рецепту MiniMax API не собрана: " + "; ".join(problems))
        elif swap and not settings.get("api_swap_trial"):
            raise ValueError("MiniMax API не обеспечивает точную замену в исходном видео. Выбери «Свой GPU» с Character Swap; облачное задание не отправлено")
        if manifest.get("images") and any(x.get("role") in {"Первый кадр", "Последний кадр"} for x in manifest["images"]) and (
            len(manifest["images"]) > 2 or manifest.get("videos") or manifest.get("audios")):
            raise ValueError("Первый/последний кадр нельзя смешивать с другими референсами MiniMax API")
        return settings, manifest

    async def submit(self, job: dict, store) -> dict:
        prior = await self.lookup(job["job_id"])
        if prior:
            return prior
        settings, manifest = self.validate(job)
        headers = self._headers()
        content = [{"type": "text", "text": job["compiled_prompt"]}]
        roles = {"images": ("image_url", "reference_image"), "videos": ("video_url", "reference_video"),
                 "audios": ("audio_url", "reference_audio")}
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20), follow_redirects=True) as client:
            for kind, (content_type, role) in roles.items():
                for entry in manifest.get(kind, []):
                    asset = store.get_asset(entry["asset_id"])
                    if not asset or asset["sha256"] != entry["sha256"]:
                        raise ValueError(f"Референс {entry['asset_id']} изменён или удалён")
                    path = Path(asset["path"])
                    if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
                        raise ValueError(f"Референс {entry['asset_id']} отсутствует")
                    if await asyncio.to_thread(sha256_file, path) != entry["sha256"]:
                        raise ValueError(f"SHA-256 референса {entry['asset_id']} изменился")
                    with path.open("rb") as stream:
                        response = await client.post(BASE_URL + "/v1/files/upload", headers=headers,
                            data={"purpose": "video_generation_input"},
                            files={"file": (asset["original_name"], stream, asset["mime"])})
                    if 400 <= response.status_code < 500:
                        raise ValueError(f"MiniMax отклонил референс {entry['asset_id']}: HTTP {response.status_code}")
                    if response.status_code >= 500:
                        raise SubmitNotSent(f"MiniMax загрузка референса: HTTP {response.status_code}")
                    uploaded = response.json()
                    file_id = (uploaded.get("file") or {}).get("file_id")
                    if not file_id:
                        raise RuntimeError("MiniMax не вернул file_id референса")
                    content.append({"type": content_type, content_type: {"url": f"mm_file://{file_id}"}, "role": role})
            body = {"model": settings.get("cloud_model", "MiniMax-H3"), "content": content,
                    "resolution": "768P", "duration": settings["duration"], "ratio": "9:16"}
            try:
                response = await client.post(BASE_URL + "/v2/video_generation", headers=headers, json=body)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise SubmitNotSent(f"MiniMax API недоступен до отправки: {exc}") from exc
            if 400 <= response.status_code < 500:
                raise PreEnqueueRejected(f"MiniMax отклонил генерацию: HTTP {response.status_code}: {response.text[:400]}")
            response.raise_for_status()
            accepted = response.json()
            task_id = accepted.get("task_id")
            if not task_id:
                raise RuntimeError("Ответ MiniMax без task_id: отправка неоднозначна, автоповтор запрещён")
            receipt = self._receipt(job["job_id"])
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump({"task_id": str(task_id), "model": body["model"], "resolution": "768P"}, output)
                output.flush()
                os.fsync(output.fileno())
            return {"job_id": PREFIX + str(task_id)}

    async def status(self, remote_id: str) -> dict:
        response = await self._query(remote_id)
        task = response.get("task") or {}
        state = task.get("status")
        if (response.get("base_resp") or {}).get("status_code") not in (None, 0):
            return {"status": "error", "error": (response.get("base_resp") or {}).get("status_msg")}
        return {"status": {"succeeded": "success", "failed": "error", "cancelled": "error",
                           "running": "running", "queued": "queued"}.get(state, "unknown"),
                "error": task.get("error_msg") or response.get("base_resp", {}).get("status_msg")}

    async def _query(self, remote_id: str) -> dict:
        if not remote_id.startswith(PREFIX):
            raise ValueError("Не MiniMax API task ID")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(BASE_URL + "/v2/query/video_generation/" + remote_id[len(PREFIX):],
                                        headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def download(self, remote_id: str, destination: Path) -> None:
        task = (await self._query(remote_id)).get("task") or {}
        if task.get("status") != "succeeded":
            raise RuntimeError("MiniMax API: видео ещё не готово")
        url = (task.get("content") or {}).get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RuntimeError("MiniMax API не вернул HTTPS ссылку на MP4")
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20), follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)

    async def cancel(self, remote_id: str) -> bool:
        # The V2 DELETE endpoint cancels only queued tasks. On succeeded/failed
        # tasks it deletes the remote record, so query first and never call it then.
        task = (await self._query(remote_id)).get("task") or {}
        if task.get("status") != "queued":
            return False
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.delete(BASE_URL + "/v2/video_generation/" + remote_id[len(PREFIX):],
                                           headers=self._headers())
            if response.status_code in {400, 409}:
                return False  # It started running between query and cancel.
            response.raise_for_status()
            return response.json().get("action") == "cancelled"


class RoutingTransport:
    def __init__(self, gpu, cloud: MiniMaxCloudTransport, store):
        self.gpu = gpu
        self.cloud = cloud
        self.store = store

    async def capabilities(self, *args, **kwargs):
        return await self.gpu.capabilities(*args, **kwargs)

    def cached_capabilities(self):
        return self.gpu.cached_capabilities()

    async def lookup(self, job_id: str):
        job = self.store.get_job(job_id)
        if job and json.loads(job["settings_json"]).get("backend") == "minimax_api":
            return await self.cloud.lookup(job_id)
        return await self.gpu.lookup(job_id)

    async def submit(self, job: dict, store):
        settings = json.loads(job["settings_json"])
        if settings.get("backend") == "minimax_api":
            return await self.cloud.submit(job, store)
        return await self.gpu.submit(job, store)

    async def status(self, remote_id: str):
        return await (self.cloud if remote_id.startswith(PREFIX) else self.gpu).status(remote_id)

    async def download(self, remote_id: str, destination: Path):
        return await (self.cloud if remote_id.startswith(PREFIX) else self.gpu).download(remote_id, destination)

    async def cancel(self, remote_id: str):
        return await (self.cloud if remote_id.startswith(PREFIX) else self.gpu).cancel(remote_id)
