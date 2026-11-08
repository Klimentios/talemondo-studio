from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from contextlib import ExitStack
from pathlib import Path
import subprocess
import shutil

from .store import StaleTransition, StudioStore
from app.graph import effective_cache_mode
from app.lora_routing import select_adapter
from app.continuation import ROLE as CONTINUATION_ROLE
from . import take_timing
from app.contract import ABSENT_EMOTIONAL_DIRECTION, API_CONTRACT, DEFAULTS, gpu_contract_problem, reference_prompt


class PreEnqueueRejected(RuntimeError):
    """GPU API rejected Director planning before creating a remote job."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


DOWNLOAD_OUTAGE_LIMIT_S = 1800
# An ambiguous submit that cannot be confirmed by client ID within this time is closed as failed.
RECONCILE_EXPIRY_S = 7200
CAPABILITIES_TTL_S = 60


class SubmitNotSent(RuntimeError):
    """The connection failed before a request could reach the GPU API."""



COMPOSITION_ROLE = "Композиция"


def composition_sketch(path: Path) -> Path:
    """«Композиция» picture → a blurred grey layout sketch (PNG next to the asset, caller deletes it).

    H3 conditions on every reference picture as identity: a sharp photo of people put those people and their
    clothes into the video instead of the heroes. At 256 px with a strong blur and no colour, figures are grey
    shapes: placement, shot size and camera angle stay readable, faces, hair, costume and palette do not.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("Для роли «Композиция» нужен ffmpeg на Mac")
    out = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.composition.png")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-frames:v", "1", "-vf",
         "scale=256:256:force_original_aspect_ratio=decrease,gblur=sigma=8,format=gray,"
         "scale=iw*4:ih*4:flags=bicubic", str(out)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
    if result.returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        raise ValueError("Не удалось подготовить картинку «Композиция»: " + result.stderr.decode(errors="replace")[-300:])
    return out

def is_connection_error(exc: BaseException) -> bool:
    """True for tunnel/network failures, where the GPU-side result is still intact."""
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, httpx.TransportError)


# Poll timeouts: a hung tunnel must not hold the dispatch lock for half a minute per pass.
POLL_TIMEOUT_S = 20
POLL_CONNECT_TIMEOUT_S = 5
# Consecutive "unknown" answers from a GPU API that does not flag `lost` itself
# (older servers) before the job is declared lost.
UNKNOWN_POLLS_LIMIT = 3


class GpuBackoff:
    """Dispatcher pacing: 3 s while the GPU answers, doubling to 30 s while it does not.

    `ok()` (any successful GPU answer, also the UI's health probe) wakes a sleeping
    dispatcher at once, so work resumes as soon as the tunnel is back.
    """

    BASE_S = 3.0
    CAP_S = 30.0

    def __init__(self) -> None:
        self.failures = 0
        self._wake: asyncio.Event | None = None

    def _event(self) -> asyncio.Event:
        if self._wake is None:
            self._wake = asyncio.Event()
        return self._wake

    def failed(self) -> None:
        self.failures += 1

    def ok(self) -> None:
        if self.failures:
            self.failures = 0
            self._event().set()

    def delay(self) -> float:
        if not self.failures:
            return self.BASE_S
        return self.CAP_S if self.failures >= 4 else min(self.CAP_S, self.BASE_S * 2 ** self.failures)

    async def wait(self) -> None:
        event = self._event()
        try:
            await asyncio.wait_for(event.wait(), timeout=self.delay())
        except TimeoutError:
            pass
        finally:
            event.clear()


def gpu_director_submission_options(settings: dict) -> dict[str, str]:
    """Only new explicitly-directed jobs wake Qwen; old saved jobs remain unchanged."""
    if settings.get("agent_prepared_prompt") is True:
        return {"precompiled_prompt": "true", "emotional_direction": "false"}
    emotional_direction = settings.get("emotional_direction", ABSENT_EMOTIONAL_DIRECTION) is True
    return {
        "precompiled_prompt": str(not emotional_direction).lower(),
        "emotional_direction": str(emotional_direction).lower(),
    }


def gpu_form_fields(job: dict) -> dict[str, str]:
    """Form fields of POST /api/jobs for a saved Studio job (also used by scripts/h3_ab.py)."""
    settings = json.loads(job["settings_json"])
    manifest = json.loads(job["manifest_json"])
    return {
        "client_job_id": job["job_id"], "manifest_json": job["manifest_json"],
        "contract_version": str(API_CONTRACT),
        "prompt": reference_prompt(
            # Studio contracts retain per-character identity, wardrobe and
            # reference roles. Qwen planning must receive them too; falling
            # back to the original story silently discarded these bindings.
            job["compiled_prompt"],
            tuple(range(1, len(manifest["videos"]) + 1)) if settings.get("use_video_audio") else (),
        ), "duration": str(settings["duration"]),
        # New emotion-directed Studio jobs use the GPU-local Qwen Director
        # before H3. Legacy saved jobs retain their precompiled route.
        **gpu_director_submission_options(settings),
        "aspect_ratio": settings["aspect_ratio"], "quality": settings["quality"],
        "megapixels": str(settings.get("megapixels") or ""),
        "profile": settings.get("profile", "bf16"), "steps": str(settings["steps"]),
        "seed": str(settings["seed"]), "ref_image_size": settings["ref_image_size"],
        "use_video_audio": str(bool(settings.get("use_video_audio", False))).lower(),
        # Legacy field still read by GPU APIs deployed before the ControlNet removal.
        "control_type": "none",
        "mode": settings.get("mode", DEFAULTS["mode"]),
        "camera_state_json": json.dumps(settings["camera_state"], ensure_ascii=False)
            if settings.get("camera_state") else "",
        "camera_optics_json": json.dumps(settings["camera_optics"], ensure_ascii=False)
            if settings.get("camera_optics") else "",
        "director_config_json": json.dumps(settings["director_config"], ensure_ascii=False)
            if settings.get("director_config") else "",
        "color_grade_json": json.dumps(settings["color_grade"]) if settings.get("color_grade") else "",
        **({"color_match_json": json.dumps(settings["color_match"])} if settings.get("color_match") else {}),
        "film_look_json": json.dumps(settings["film_look"]) if settings.get("film_look") else "",
        **({"camera_effect": settings["camera_effect"]} if settings.get("camera_effect") else {}),
        # Studio's 20–25 step control always means full sampling.
        # Never inherit a GPU host's speed default.
        "speed": settings.get("speed") or "quality",
        "character_refinement_json": json.dumps(settings["character_refinement"]) if settings.get("character_refinement") else "",
        **({"swap_mask_json": json.dumps(settings["swap_mask"], ensure_ascii=False)} if settings.get("swap_mask") else {}),
        **({"reskin_json": json.dumps(settings["reskin"])} if settings.get("reskin") else {}),
        "guide_frames_json": json.dumps(settings.get("guide_frames", [])),
        "style_adapter": settings.get("style_adapter", "none"),
        "cache_mode": settings.get("cache_mode", DEFAULTS["cache_mode"]),
        **({"ab_variant": f"studio-{settings['ab_turbo']}", "ab_turbo": settings["ab_turbo"]}
           if settings.get("ab_turbo") else {}),
        **({"render_options_json": json.dumps(settings["render_options"])} if settings.get("render_options") else {}),
        **({"seed_hunt_json": json.dumps(settings["seed_hunt"])} if settings.get("seed_hunt") else {}),
        **({"cache_config_json": json.dumps(settings["cache_config"])} if settings.get("cache_config") is not None else {}),
        **({"sampling_profile": settings["sampling_profile"]} if settings.get("sampling_profile") else {}),
        # Agent text without Studio/GPU generic blocks (store.create_job clean_prompt); absent = "studio".
        **({"prompt_blocks": "none"} if settings.get("prompt_blocks") == "none" else {}),
        # Contract 5: the user confirmed «Короткое видео движения — всё равно запустить».
        **({"allow_short_motion_reference": "true"} if settings.get("allow_short_motion_reference") is True else {}),
    }


class HttpGpuTransport:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.queue_url = os.environ.get("H3_SERVER_QUEUE_URL") or (
            "http://127.0.0.1:18191" if self.base_url == "http://127.0.0.1:18189" else "")
        self._capabilities: tuple[float, dict] | None = None

    async def capabilities(self, client: object | None = None) -> dict:
        """GPU /api/capabilities, cached for CAPABILITIES_TTL_S per process.

        5xx (GPU API restarting) and no connection -> SubmitNotSent: retry later.
        404 (API without capabilities) or any other refusal -> ValueError: update the server.
        """
        cached = self._capabilities
        if cached and time.monotonic() - cached[0] < CAPABILITIES_TTL_S:
            return cached[1]
        import httpx
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=20) as own:
                    response = await own.get(f"{self.base_url}/api/capabilities")
            else:
                response = await client.get(f"{self.base_url}/api/capabilities", timeout=20)
        except httpx.TransportError as exc:
            # A GET of capabilities never creates a job: any timeout/transport error is retryable.
            raise SubmitNotSent(f"GPU недоступен: {exc}") from exc
        if response.status_code >= 500:
            # GPU API restarting (ComfyUI not up yet): retry later, not "update the server".
            raise SubmitNotSent(f"GPU capabilities HTTP {response.status_code}")
        if response.status_code == 404:
            raise ValueError("GPU API старой версии: нет /api/capabilities. Обнови сервер: scripts/h3_new_server.sh")
        if response.status_code != 200:
            raise ValueError(f"GPU API capabilities HTTP {response.status_code}. Обнови сервер: scripts/h3_new_server.sh")
        try:
            caps = response.json()
        except ValueError:
            caps = None
        if not isinstance(caps, dict):
            raise ValueError("GPU API вернул неверный /api/capabilities. Обнови сервер: scripts/h3_new_server.sh")
        if self.queue_url:
            try:
                async with httpx.AsyncClient(timeout=3) as queue_client:
                    queue_caps = await queue_client.get(f"{self.queue_url or self.base_url}/api/server-queue/capabilities")
                if queue_caps.status_code == 200 and queue_caps.json().get("server_queue_v1") is True:
                    caps["server_queue_v1"] = True
            except (httpx.TransportError, ValueError):
                pass  # Older servers keep their existing one-at-a-time path.
        self._capabilities = (time.monotonic(), caps)
        return caps

    def cached_capabilities(self) -> dict | None:
        cached = self._capabilities
        return cached[1] if cached and time.monotonic() - cached[0] < CAPABILITIES_TTL_S else None

    async def _server_queue_get(self, client, path: str) -> dict | None:
        """GET from the server queue; None when the job is not staged there.

        A dedicated queue tunnel (queue_url) is asked first, but if it is down the
        main GPU API stays authoritative instead of stalling every lookup/status.
        """
        import httpx
        if self.queue_url:
            try:
                staged = await client.get(f"{self.queue_url}{path}")
            except httpx.TransportError:
                staged = None
            if staged is not None and staged.status_code != 404:
                staged.raise_for_status()
                return staged.json()
            if staged is not None:
                return None
        if (await self.capabilities()).get("server_queue_v1") is True:
            staged = await client.get(f"{self.base_url}{path}")
            if staged.status_code != 404:
                staged.raise_for_status()
                return staged.json()
        return None

    async def lookup(self, job_id: str) -> dict | None:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT_S, connect=POLL_CONNECT_TIMEOUT_S)) as client:
            staged = await self._server_queue_get(client, f"/api/server-queue/jobs/{job_id}")
            if staged is not None:
                return {**staged, "job_id": staged.get("rendered_job_id") or job_id}
            response = await client.get(f"{self.base_url}/api/jobs/by-client/{job_id}")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def submit(self, job: dict, store: StudioStore) -> dict:
        import httpx
        settings = json.loads(job["settings_json"])
        manifest = json.loads(job["manifest_json"])
        data = gpu_form_fields(job)
        with ExitStack() as stack:
            files = []
            for kind, form_name in (("images", "images"), ("videos", "videos"), ("audios", "audios")):
                for entry in manifest[kind]:
                    asset = store.get_asset(entry["asset_id"])
                    if not asset or asset["sha256"] != entry["sha256"]:
                        raise ValueError(f"Изменён или удалён референс {entry['asset_id']}")
                    path = Path(asset["path"])
                    if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
                        raise ValueError(f"Референс {entry['asset_id']} отсутствует")
                    if await asyncio.to_thread(sha256_file, path) != entry["sha256"]:
                        raise ValueError(f"SHA-256 референса {entry['asset_id']} изменился")
                    if kind == "images" and entry.get("job_role", entry.get("role")) == COMPOSITION_ROLE:
                        # A full photo made H3 copy its people and clothes over the heroes (2026-10-08).
                        sketch = await asyncio.to_thread(composition_sketch, path)
                        stack.callback(sketch.unlink, missing_ok=True)
                        # The GPU validates the uploaded bytes against this request manifest.
                        # The sketch replaces the source photo, so its checksum must replace
                        # the source checksum without changing the saved asset record.
                        entry["sha256"] = await asyncio.to_thread(sha256_file, sketch)
                        file_handle = stack.enter_context(sketch.open("rb"))
                        files.append((form_name, ("composition-layout.png", file_handle, "image/png")))
                        continue
                    file_handle = stack.enter_context(path.open("rb"))
                    files.append((form_name, (asset["original_name"], file_handle, asset["mime"])))
            data["manifest_json"] = json.dumps(manifest, ensure_ascii=False)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20)) as client:
                    caps = await self.capabilities(client)
                    problem = gpu_contract_problem(caps)
                    if problem:
                        raise ValueError(problem)
                    # Legacy APIs omit this capability. When it is advertised, its
                    # availability check also applies to the baseline BF16 profile.
                    if ("render_profiles_v1" in caps or settings.get("profile", "bf16") != "bf16"
                            or settings.get("render_options")):
                        from app.render_profiles import check_render_capability
                        check_render_capability(caps.get("render_profiles_v1"), settings.get("profile", "bf16"), settings.get("render_options"))
                    if settings.get("ab_turbo") and settings["ab_turbo"] not in (caps.get("ab_v1") or {}).get("turbo", []):
                        raise ValueError(f"Turbo {settings['ab_turbo']} недоступен на GPU; задание не отправлено")
                    # An older GPU API ignores seed_hunt_json and would render a full clip as a «preview».
                    if settings.get("seed_hunt") and not (caps.get("ab_v1") or {}).get("seed_hunt"):
                        raise ValueError("Подбор seed недоступен на GPU (обнови app/ на сервере); задание не отправлено")
                    if settings.get("character_refinement") and caps.get("character_refinement_v1") is not True:
                        raise ValueError("Уточнение героев не установлено на GPU; нужны узлы H3CharacterTrack/Crop/Composite и веса SAM2.1")
                    if settings.get("swap_mask") and caps.get("swap_mask_v1") is not True:
                        raise ValueError("Маска замены не установлена на GPU: нужны узлы h3-swap-mask и h3-character-refine (SAM2.1); задание не отправлено")
                    if settings.get("reskin") and caps.get("reskin_v1") is not True:
                        raise ValueError("Рескин не поддержан GPU API: обнови app/ на сервере (нужны h3-swap-mask и SolidMask); задание не отправлено")
                    if settings.get("sampling_profile", "standard") not in (caps.get("sampling_profiles") or ["standard"]):
                        raise ValueError("Экспериментальный сэмплинг недоступен на этом GPU: проверь KSamplerSelect и BasicScheduler")
                    # A format the connected GPU API does not list (older ones: 16:9 / 9:16 only) is refused here.
                    aspects = caps.get("aspect_ratios") or ["16:9", "9:16"]
                    if settings.get("aspect_ratio", "9:16") not in aspects:
                        raise ValueError(f"GPU-сервер не принимает формат {settings.get('aspect_ratio')}: доступны {', '.join(aspects)}. Обнови app/ на сервере (scripts/h3_new_server.sh)")
                    # 4 s (official H3 minimum) needs a GPU API that accepts it; older ones report [5, 15].
                    seconds_range = caps.get("duration_seconds") or [5, 15]
                    if int(settings.get("duration", 5)) < int(seconds_range[0]):
                        raise ValueError(f"GPU-сервер принимает ролики от {seconds_range[0]} с; для 4 с обнови app/ на сервере (scripts/h3_new_server.sh)")
                    if settings.get("color_match") and caps.get("color_match_v1") is not True:
                        raise ValueError("На GPU нет узла H3ColorGrade: «цвет как в предыдущем кадре» недоступен; обнови сервер")
                    if any(entry.get("role") == CONTINUATION_ROLE for entry in manifest.get("videos", [])) \
                            and caps.get("continuation_v1") is not True:
                        raise ValueError("На GPU нет «Продолжения» дубля (MiniMaxH3AddGuide, ImageFromBatch, "
                                         "TrimAudioDuration); обнови сервер: scripts/h3_new_server.sh")
                    if settings.get("camera_effect") and settings["camera_effect"] not in (
                            (caps.get("camera_effects_v1") or {}).get("available") or []):
                        raise ValueError(f"Эффект камеры {settings['camera_effect']} не установлен на GPU "
                                         "(установщик с H3_CAMERA_EFFECTS=1); обнови сервер")
                    if settings.get("color_grade") and caps.get("color_grade_v1") is not True:
                        raise ValueError("GPU API не поддерживает цветокор по фото (H3ColorGrade); обнови сервер: scripts/h3_new_server.sh")
                    if settings.get("film_look") and caps.get("film_look_v1") is not True:
                        raise ValueError("GPU API не поддерживает плёночный характер камеры (H3FilmLook); обнови сервер: scripts/h3_new_server.sh")
                    cache_mode = settings.get("cache_mode", DEFAULTS["cache_mode"])
                    cache_modes = caps.get("cache_modes")
                    if cache_mode == "custom" and (not isinstance(cache_modes, list) or "custom" not in cache_modes):
                        raise ValueError("CUSTOM кэш требует обновления GPU API; задание не отправлено")
                    if isinstance(cache_modes, list) and cache_mode not in cache_modes:
                        raise ValueError(f"Режим кэша «{cache_mode}» недоступен на этом GPU (нет узла "
                                         "ApplyMiniMaxH3FirstBlockCache): выбери «off» или обнови сервер: "
                                         "scripts/h3_new_server.sh")
                    style_adapter = settings.get("style_adapter", "none")
                    if style_adapter == "auto":
                        # Queued while the GPU was offline: choose now from the LoRAs it really has.
                        style_adapter, _reason = select_adapter(
                            job.get("original_prompt") or job["compiled_prompt"],
                            settings.get("mode", DEFAULTS["mode"]),
                            {item for item in caps.get("style_adapters") or [] if isinstance(item, str)},
                            people=any(entry.get("job_role", entry.get("role")) in {"Лицо героя", "Персонаж целиком"}
                                       for entry in manifest.get("images", [])))
                        data["style_adapter"] = style_adapter
                    if style_adapter != "none" and style_adapter not in (caps.get("style_adapters") or []):
                        raise ValueError(f"Стилевая LoRA {style_adapter} недоступна на этом GPU-сервере")
                    endpoint = f"{self.queue_url or self.base_url}/api/server-queue/jobs" if caps.get("server_queue_v1") is True else f"{self.base_url}/api/jobs"
                    response = await client.post(endpoint, data=data, files=files)
                    if response.status_code >= 400:
                        try:
                            detail = response.json().get("detail", "")
                        except (ValueError, AttributeError):
                            detail = ""
                        message = f"GPU API HTTP {response.status_code}: {str(detail)[:500]}"
                        # The pinned GPU API emits this 503 only from its Director
                        # preparation block, before uploads, registry acceptance,
                        # and ComfyUI enqueue. Keep this typed so a failed plan
                        # cannot block every later job in the local FIFO.
                        if (response.status_code == 503 and
                                str(detail).startswith("Автоматическая режиссура MiniMax H3 не завершена:")):
                            raise PreEnqueueRejected(message)
                        # A 4xx is a validation refusal (bad video, guide frame, unknown field). The
                        # worker confirms by client ID that nothing was accepted and fails just this job
                        # instead of parking the whole FIFO in needs_reconciliation for 2 hours.
                        if 400 <= response.status_code < 500 and response.status_code != 409:
                            raise PreEnqueueRejected(message)
                        raise RuntimeError(message)
                    response.raise_for_status()
                    try:
                        accepted = response.json()
                    except ValueError as exc:
                        # The POST succeeded, so the GPU may be rendering: never "failed" here.
                        # RuntimeError makes the worker look the job up by its client ID.
                        raise RuntimeError(f"GPU принял запрос, но ответ не разобран: {exc}") from None
                    if not isinstance(accepted, dict):
                        raise RuntimeError("GPU принял запрос, но ответ не разобран")
                    return accepted
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # No TCP connection, so nothing reached the GPU: safe to keep the job queued.
                raise SubmitNotSent(f"GPU недоступен: {exc}") from exc

    async def status(self, remote_job_id: str) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(POLL_TIMEOUT_S, connect=POLL_CONNECT_TIMEOUT_S)) as client:
            staged = await self._server_queue_get(client, f"/api/server-queue/jobs/{remote_job_id}")
            if staged is not None:
                return staged
            response = await client.get(f"{self.base_url}/api/jobs/{remote_job_id}")
            response.raise_for_status()
            return response.json()

    async def cancel(self, remote_job_id: str) -> bool:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            if (await self.capabilities()).get("server_queue_v1") is True:
                staged = await client.post(f"{self.queue_url or self.base_url}/api/server-queue/jobs/{remote_job_id}/cancel")
                if staged.status_code != 404:
                    staged.raise_for_status()
                    return staged.json().get("cancelled") is True
            response = await client.post(f"{self.base_url}/api/jobs/{remote_job_id}/cancel")
            if response.status_code in {404, 409}:
                return False
            response.raise_for_status()
            return response.json().get("cancelled") is True

    async def download(self, remote_job_id: str, destination: Path) -> None:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=20)) as client:
            async with client.stream("GET", f"{self.base_url}/api/jobs/{remote_job_id}/video") as response:
                response.raise_for_status()
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


SOURCE_AUDIO_ROLES = ("Движение и камера", "Исходное видео", "Движение")


def keep_source_audio_path(job: dict, store: StudioStore) -> Path | None:
    """settings.keep_source_audio («Правка видео» → «Звук исходника без изменений»): the source clip whose sound
    goes onto the result one to one. H3 only imitates a reference soundtrack; the user wanted the original."""
    if json.loads(job.get("settings_json") or "{}").get("keep_source_audio") is not True:
        return None
    for entry in json.loads(job.get("manifest_json") or "{}").get("videos", []):
        if entry.get("job_role", entry.get("role")) in SOURCE_AUDIO_ROLES:
            asset = store.get_asset(entry["asset_id"])
            path = Path(asset["path"]) if asset else None
            if path and path.is_file() and path.resolve().is_relative_to(store.assets_dir.resolve()):
                return path
    raise ValueError("Исходный ролик для звука не найден")


def replace_audio(video: Path, source: Path) -> None:
    """Put the source clip's first audio track under the rendered picture (from 0 s, cut to the picture)."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    probe = subprocess.run([shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=index", "-of", "csv=p=0", str(source)],
                           capture_output=True, text=True, timeout=30, check=False)
    if probe.returncode != 0 or not probe.stdout.strip():
        return  # a silent source: keep H3's own soundtrack
    mixed = video.with_name(video.name + ".audio.mp4")
    result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(source),
                             "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                             "-shortest", "-movflags", "+faststart", str(mixed)],
                            capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0 or not mixed.is_file():
        mixed.unlink(missing_ok=True)
        raise ValueError(f"Не удалось поставить звук исходника: {result.stderr[-300:]}")
    os.replace(mixed, video)


def validate_mp4(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < 1024:
        raise ValueError("Пустой или слишком маленький MP4")
    probe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    result = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"MP4 не читается: {result.stderr[:300]}")
    streams = json.loads(result.stdout).get("streams", [])
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise ValueError("В MP4 нет видеопотока")


def director_summary_json(director: dict | None, job: dict) -> str | None:
    """GPU Director summary as stored for the job feed, with the cache mode the graph really used.

    The GPU summary repeats the requested cache mode; a video-reference job renders Fast as Safe
    (app.graph.effective_cache_mode). The graph is unchanged, only the report is corrected.
    """
    if not director:
        return None
    director = dict(director)
    requested = director.get("cache_mode")
    try:
        has_videos = bool(json.loads(job.get("manifest_json") or "{}").get("videos"))
    except (TypeError, ValueError):
        has_videos = False
    if isinstance(requested, str) and not director.get("character_refinement"):
        actual = effective_cache_mode(requested, has_videos)
        if actual != requested:
            director["cache_mode"] = actual
            director["cache_mode_requested"] = requested
    return json.dumps(director, ensure_ascii=False)


class QueueWorker:
    def __init__(self, store: StudioStore, transport: object, backoff: GpuBackoff | None = None,
                 on_completed=None) -> None:
        self.store = store
        self.transport = transport
        # async (store, job_id) after a job completes: job_graphs.JobGraphs.archive keeps the graph on the Mac
        self.on_completed = on_completed
        self.backoff = backoff or GpuBackoff()
        self._run_lock = asyncio.Lock()
        self._unknown_polls: dict[str, int] = {}
        self._retry_after: dict[str, float] = {}
        self._download_failures: dict[str, int] = {}
        self._download_outage_since: dict[str, float] = {}

    async def _gpu(self, call):
        """Await one GPU API call and feed the dispatcher backoff with its outcome."""
        try:
            result = await call
        except Exception:
            self.backoff.failed()
            raise
        self.backoff.ok()
        return result

    async def reconcile_once(self) -> bool:
        """Resolve jobs parked in needs_reconciliation by their immutable client ID. They are never
        resubmitted from here; after RECONCILE_EXPIRY_S they fail (also while the GPU is offline),
        so an ambiguous submit can no longer hold the Mac FIFO."""
        changed = False
        reachable = True
        for job in self.store.reconciling_jobs():
            job_id = job["job_id"]
            if reachable:
                try:
                    existing = await self._gpu(self.transport.lookup(job_id))
                except Exception:
                    reachable = False
                else:
                    if existing and existing.get("job_id"):
                        try:
                            self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                                  director_json=director_summary_json(existing.get("director"), job),
                                                  expected_states=("needs_reconciliation",))
                            changed = True
                        except StaleTransition:
                            pass
                        continue
            try:
                age = datetime.now(timezone.utc) - datetime.fromisoformat(job["updated_at"].replace(" ", "T")).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, AttributeError):
                continue
            if age.total_seconds() >= RECONCILE_EXPIRY_S:
                try:
                    self.store.transition(job_id, "failed", expected_states=("needs_reconciliation",), error=(
                        "Удалённая запись не найдена; Автоповтор отключён" if reachable else
                        f"GPU был недоступен {RECONCILE_EXPIRY_S // 3600} ч — не удалось подтвердить приём задания; "
                        "автоповтор отключён"))
                    changed = True
                except StaleTransition:
                    pass
        return changed

    async def stage_once(self) -> bool:
        if self.store.queue_paused() or not self.store.oldest_pending():
            return False
        capabilities = getattr(self.transport, "capabilities", None)
        if capabilities is None:
            return False
        try:
            caps = await self._gpu(capabilities())
        except Exception:
            return False
        if caps.get("server_queue_v1") is not True:
            return False
        async with self._run_lock:
            pending = self.store.oldest_pending()
            if not pending or self.store.queue_paused():
                return False
            return await self._run_job(pending)

    async def stage_cloud_once(self) -> bool:
        """Submit a MiniMax API job even while an older GPU job holds the FIFO (e.g. a dead GPU).

        The cloud keeps its own queue and receipts; status and download already bypass the
        FIFO in sync_ready_videos_once, so only the submission used to wait behind the GPU.
        """
        if self.store.queue_paused() or self._run_lock.locked():
            return False
        async with self._run_lock:
            pending = self.store.oldest_pending_cloud()
            if not pending or self.store.queue_paused():
                return False
            return await self._run_job(pending)

    async def run_once(self) -> bool:
        if self._run_lock.locked():
            # stage_once is uploading; let the dispatcher serve side queues meanwhile.
            return False
        async with self._run_lock:
            return await self._run_job()

    async def sync_ready_videos_once(self) -> bool:
        """Fetch one finished server MP4 even if an earlier GPU job still runs.

        Uses the normal receipt, validation and atomic download path; never creates
        a new render or imports an unowned server file into the wrong franchise.
        """
        if self._run_lock.locked():
            return False
        async with self._run_lock:
            changed = False
            for job in self.store.active_remote_jobs():
                if job['state'] == 'downloading':
                    return await self._run_job(job)
                try:
                    remote = await self._gpu(self.transport.status(job['remote_job_id']))
                except Exception:
                    # A disconnected older GPU job must not block completed
                    # cloud receipts later in the same durable queue.
                    continue
                if (job['remote_job_id'].startswith('minimax:') and
                        remote.get('status') == 'running' and job['state'] == 'queued_gpu'):
                    try:
                        self.store.transition(job['job_id'], 'running', expected_states=('queued_gpu',))
                        changed = True
                    except StaleTransition:
                        pass
                if job['remote_job_id'].startswith('minimax:') and remote.get('status') == 'error':
                    try:
                        self.store.transition(job['job_id'], 'failed',
                            error=remote.get('error') or 'MiniMax API не выполнил задание',
                            expected_states=('queued_gpu', 'running'))
                        changed = True
                    except StaleTransition:
                        pass
                    continue
                if remote.get('status') != 'success':
                    continue
                if await self._run_job(job):
                    refreshed = self.store.get_job(job['job_id'])
                    if refreshed and refreshed['state'] == 'downloading':
                        await self._run_job(refreshed)
                    return True
        return changed

    async def _run_job(self, staged_job=None) -> bool:
        job = staged_job or self.store.active_job()
        if not job:
            pending = self.store.oldest_pending()
            if pending and self.store.queue_paused():
                # A job staged independently on the GPU can finish while the
                # Mac is off. Reconcile by its immutable client ID, but never
                # dispatch a new upload while the local queue is paused.
                try:
                    existing = await self._gpu(self.transport.lookup(pending["job_id"]))
                except Exception:
                    return False
                if existing and existing.get("job_id"):
                    try:
                        self.store.transition(pending["job_id"], "queued_gpu",
                                              remote_job_id=existing["job_id"],
                                              director_json=director_summary_json(existing.get("director"), pending),
                                              expected_states=(pending["state"],))
                    except StaleTransition:
                        pass
                    return True
                return False
            job = pending
        if not job:
            return await self.reconcile_once()
        job_id = job["job_id"]
        state = job["state"]
        if state == "queued_local" and self.store.queue_paused():
            # Server-staged work may finish while this Mac is offline. Look it
            # up by immutable client ID, but never submit a new upload on pause.
            try:
                existing = await self._gpu(self.transport.lookup(job_id))
            except Exception:
                return False
            if existing and existing.get("job_id"):
                self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                      director_json=director_summary_json(existing.get("director"), job))
                return True
            return False
        if state == "uploading" and self.store.queue_paused():
            try:
                existing = await self._gpu(self.transport.lookup(job_id))
            except Exception:
                return False
            if existing and existing.get("job_id"):
                self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                      director_json=director_summary_json(existing.get("director"), job))
            else:
                self.store.transition(job_id, "needs_reconciliation",
                                      error="Не найдено принятое сервером задание; автоповтор отключён")
            return True
        if state == "cancelled" and job.get("remote_job_id"):
            # A canceled Studio job may still leave a terminal server receipt.
            # Query the remote status but retain the user's canceled state.
            try:
                await self._gpu(self.transport.status(job["remote_job_id"]))
            except Exception:
                return False
            return False
        if state == "downloading":
            temporary = self.store.results_dir / f".{job_id}.part"
            destination = self.store.results_dir / f"{job_id}.mp4"
            try:
                await self._gpu(self.transport.download(job["remote_job_id"], temporary))
                await asyncio.to_thread(validate_mp4, temporary)
                source_audio = keep_source_audio_path(job, self.store)
                if source_audio:
                    await asyncio.to_thread(replace_audio, temporary, source_audio)
                os.replace(temporary, destination)
                self.store.transition(job_id, "completed", output_path=str(destination))
                # plan-vs-result timing for the production gate (local_studio/take_timing.py), off the loop
                asyncio.get_running_loop().run_in_executor(None, take_timing.record, self.store, job_id)
                if self.on_completed:
                    asyncio.get_running_loop().create_task(self.on_completed(self.store, job_id))
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                if is_connection_error(exc):
                    # The finished MP4 stays on the GPU; wait for the tunnel instead of
                    # spending the five validation attempts in ~15 s of an outage.
                    since = self._download_outage_since.setdefault(job_id, time.monotonic())
                    if time.monotonic() - since >= DOWNLOAD_OUTAGE_LIMIT_S:
                        self.store.transition(job_id, "failed", error=f"GPU недоступен {DOWNLOAD_OUTAGE_LIMIT_S // 60} мин при скачивании: {exc}")
                    return False
                self._download_outage_since.pop(job_id, None)
                failures = self._download_failures.get(job_id, 0) + 1
                self._download_failures[job_id] = failures
                if failures >= 5:
                    self.store.transition(job_id, "failed", error=f"Скачивание или проверка MP4: {exc}")
            return True
        if state in {"queued_local", "uploading"}:
            if state == "queued_local" and time.monotonic() < self._retry_after.get(job_id, 0):
                return False
            try:
                existing = await self._gpu(self.transport.lookup(job_id))
            except Exception:
                # No submission was attempted for a queued_local job. Leave it in
                # the local FIFO until the GPU is reachable again.
                return False
            if existing and existing.get("job_id"):
                self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                      director_json=director_summary_json(existing.get("director"), job))
                return True
            if state == "uploading":
                self.store.transition(job_id, "needs_reconciliation",
                                      error="Нельзя доказать, что прежняя отправка не дошла до GPU")
                return True
            # Cloud V2 has no GPU capability endpoint; validate its request in submit.
            cloud_job = json.loads(job["settings_json"]).get("backend") == "minimax_api"
            prefetch = None if cloud_job else getattr(self.transport, "capabilities", None)
            if prefetch is not None:
                # Before "uploading": a timeout here proves nothing was sent, so the job stays queued.
                try:
                    await self._gpu(prefetch())
                except SubmitNotSent:
                    self._retry_after[job_id] = time.monotonic() + 30
                    return False
                except ValueError as exc:
                    self.store.transition(job_id, "failed", error=str(exc), expected_states=("queued_local",))
                    return True
                except Exception:
                    self._retry_after[job_id] = time.monotonic() + 30
                    return False
            self.store.transition(job_id, "uploading", expected_states=("queued_local",))
            try:
                response = await self.transport.submit(job, self.store)
                if response.get("job_id"):
                    self.store.transition(job_id, "queued_gpu", remote_job_id=response["job_id"],
                                          director_json=director_summary_json(response.get("director"), job))
                else:
                    self.store.transition(job_id, "needs_reconciliation", error="GPU не вернул remote_job_id")
                return True
            except ValueError as exc:
                self.store.transition(job_id, "failed", error=str(exc))
                return True
            except SubmitNotSent:
                self.backoff.failed()
                self.store.transition(job_id, "queued_local", expected_states=("uploading",))
                self._retry_after[job_id] = time.monotonic() + 30
                return True
            except PreEnqueueRejected as exc:
                if exc.retry_after is not None:
                    self.store.transition(job_id, "queued_local", expected_states=("uploading",))
                    self._retry_after[job_id] = time.monotonic() + max(1, exc.retry_after)
                    return True
                # Verify the immutable ID after the explicit pre-enqueue 503.
                # If the lookup itself is unavailable, retain the safe
                # reconciliation stop rather than risking a duplicate.
                lookup_succeeded = False
                try:
                    existing = await self._gpu(self.transport.lookup(job_id))
                    lookup_succeeded = True
                except Exception:
                    existing = None
                if existing and existing.get("job_id"):
                    self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                          director_json=director_summary_json(existing.get("director"), job))
                elif lookup_succeeded and existing is None:
                    self.store.transition(job_id, "failed", error=str(exc))
                else:
                    self.store.transition(job_id, "needs_reconciliation",
                                          error=f"Отправка неоднозначна: {exc}")
                return True
            except Exception as exc:
                # A timeout can happen *after* remote acceptance. Never blind-retry.
                try:
                    existing = await self._gpu(self.transport.lookup(job_id))
                except Exception:
                    existing = None
                if existing and existing.get("job_id"):
                    self.store.transition(job_id, "queued_gpu", remote_job_id=existing["job_id"],
                                          director_json=director_summary_json(existing.get("director"), job))
                else:
                    self.store.transition(job_id, "needs_reconciliation", error=f"Отправка неоднозначна: {exc}")
                return True
        if state in {"queued_gpu", "running", "stopping"}:
            try:
                remote = await self._gpu(self.transport.status(job["remote_job_id"]))
            except Exception:
                return False  # Keep known remote ID; connection can return.
            if remote.get("rendered_job_id") and remote["rendered_job_id"] != job["remote_job_id"]:
                job = self.store.transition(job_id, state, remote_job_id=remote["rendered_job_id"],
                                            director_json=director_summary_json(remote.get("director"), job),
                                            expected_states=(state,))
            try:
                if remote["status"] != "unknown":
                    self._unknown_polls.pop(job_id, None)
                if remote["status"] == "success":
                    if state == "stopping":
                        self.store.transition(job_id, "cancelled", error="Генерация завершилась после запроса отмены", expected_states=(state,))
                    else:
                        if remote.get("refinement_report") or remote.get("cache_runtime") or remote.get("execution_observation"):
                            director = json.loads(job.get("director_json") or "{}")
                            if remote.get("execution_observation"):
                                director["execution_observation"] = remote["execution_observation"]
                            if remote.get("refinement_report"):
                                director["refinement_report"] = remote["refinement_report"]
                            if remote.get("cache_runtime"):
                                from app.cache_metrics import validated_metrics
                                metrics = validated_metrics(remote['cache_runtime'], job['remote_job_id'])
                                if metrics is not None:
                                    director.setdefault('cache_execution', {})['runtime_counters'] = metrics
                            self.store.transition(job_id, "downloading", director_json=json.dumps(director, ensure_ascii=False), expected_states=(state,))
                        else:
                            self.store.transition(job_id, "downloading", expected_states=(state,))
                elif remote["status"] == "running":
                    if state not in {"running", "stopping"}:
                        self.store.transition(job_id, "running", expected_states=(state,))
                    progress = remote.get("sampling_progress") or {}
                    if isinstance(progress.get("value"), int) and isinstance(progress.get("max"), int):
                        self.store.set_sampling_progress(job_id, progress["value"], progress["max"])
                elif remote["status"] == "error":
                    if state == "stopping":
                        self.store.transition(job_id, "cancelled", error="Генерация прервана пользователем", expected_states=(state,))
                    else:
                        self.store.transition(job_id, "failed", error=remote.get("error", "Ошибка GPU"), expected_states=(state,))
                elif remote["status"] == "unknown":
                    if state == "stopping":
                        self.store.transition(job_id, "cancelled", error="Генерация прервана пользователем", expected_states=(state,))
                        return True
                    # A current GPU API answers `lost` only after re-reading ComfyUI history for
                    # a job its registry queued (ComfyUI restarted). Older APIs just say
                    # `unknown`: wait for a few consecutive answers before giving up.
                    polls = self._unknown_polls.get(job_id, 0) + 1
                    self._unknown_polls[job_id] = polls
                    if remote.get("lost") is True or polls >= UNKNOWN_POLLS_LIMIT:
                        self._unknown_polls.pop(job_id, None)
                        self.store.transition(job_id, "interrupted", error=str(
                            remote.get("error") or "ComfyUI на GPU не знает это задание (перезапуск GPU-сервера?); "
                                                   "результат потерян, автоповтор отключён"), expected_states=(state,))
            except StaleTransition:
                # The user cancelled (or another pass moved the job) while we awaited the GPU;
                # the next pass handles the new state instead of overwriting it.
                return True
            return True
        return False
