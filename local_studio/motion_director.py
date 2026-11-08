"""Studio-side dependency planning for Motion Director Mixed timelines; no GPU calls."""
from __future__ import annotations

from typing import Literal
import json
import math
from pathlib import PurePosixPath
from pathlib import Path
import hashlib

from app.fingerprints import json_sha256
import asyncio
import tempfile
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


def media_suffix(asset: dict) -> str:
    # Library paths are content hashes without extensions; use the import metadata.
    supported = {"image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"},
                 "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}}
    suffix = Path(asset.get("original_name") or asset["path"]).suffix.lower()
    if suffix in supported.get(asset.get("kind"), set()):
        return suffix
    by_mime = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
               "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
               "video/x-matroska": ".mkv"}
    suffix = by_mime.get(asset.get("mime"))
    if suffix not in supported.get(asset.get("kind"), set()):
        raise ValueError("Не удалось определить поддерживаемый формат медиа из метаданных библиотеки")
    return suffix


async def upload_image(store, client, comfy_url: str, asset_id: str) -> dict:
    """Transfer one library image; no graph execution and no arbitrary local paths."""
    asset = store.get_asset(asset_id)
    if not asset or asset.get("kind") != "image":
        raise ValueError("В библиотеке нет такого изображения")
    path = Path(asset["path"])
    if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
        raise ValueError("Файл изображения недоступен в библиотеке")
    content = await asyncio.to_thread(path.read_bytes)
    digest = hashlib.sha256(content).hexdigest()
    if asset.get("sha256") and digest != asset["sha256"]:
        raise ValueError("Изображение изменилось после добавления в библиотеку")
    response = await client.post(f"{comfy_url}/upload/image", data={"subfolder": "studio-motion-director", "overwrite": "false"},
                                 files={"image": (digest + media_suffix(asset), content, asset.get("mime") or "application/octet-stream")})
    response.raise_for_status()
    result = response.json()
    if result.get("type") != "input" or not isinstance(result.get("name"), str) or not result["name"]:
        raise ValueError("ComfyUI не подтвердил загрузку изображения в input")
    binding = ImageBinding(asset_id=asset_id, image_file=str(PurePosixPath(result.get("subfolder") or "") / result["name"]))
    return {**binding.model_dump(), "sha256": digest, "uploaded": True, "submitted": False}


class ImageBinding(StrictInput):
    asset_id: str = Field(min_length=1)
    image_file: str = Field(min_length=1)

    @model_validator(mode="after")
    def safe_path(self):
        path = PurePosixPath(self.image_file)
        if not path.name or path.is_absolute() or ".." in path.parts or "\\" in self.image_file or ":" in self.image_file or any(ord(c) < 32 for c in self.image_file):
            raise ValueError("image_file должен быть относительным путём внутри ComfyUI/input")
        return self


async def upload_video(store, client, comfy_url: str, asset_id: str) -> dict:
    """Stream a verified library snapshot using the external node's chunk protocol."""
    asset = store.get_asset(asset_id)
    if not asset or asset.get("kind") != "video":
        raise ValueError("В библиотеке нет такого видео")
    path = Path(asset["path"])
    if not path.is_file() or not path.resolve().is_relative_to(store.assets_dir.resolve()):
        raise ValueError("Файл видео недоступен в библиотеке")
    # Snapshot first: never mix bytes from a library file edited during transfer.
    with tempfile.TemporaryFile() as snapshot:
        def copy_snapshot():
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as source:
                while block := source.read(512 * 1024):
                    snapshot.write(block)
                    digest.update(block)
                    size += len(block)
            snapshot.seek(0)
            return size, digest.hexdigest()
        size, digest = await asyncio.to_thread(copy_snapshot)
        if not size or (asset.get("sha256") and digest != asset["sha256"]):
            raise ValueError("Видео пустое или изменилось после добавления в библиотеку")
        chunk_size = 512 * 1024
        total = math.ceil(size / chunk_size)
        upload_id = uuid.uuid4().hex
        filename = digest + media_suffix(asset)
        for index in range(total):
            block = await asyncio.to_thread(snapshot.read, chunk_size)
            response = await client.post(f"{comfy_url}/minimax/motion-director/upload_chunk",
                data={"upload_id": upload_id, "filename": filename,
                      "chunk_index": str(index), "total_chunks": str(total)},
                files={"chunk": (filename, block, "application/octet-stream")})
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Motion Director вернул некорректное подтверждение загрузки")
            if index < total - 1 and (result.get("status") != "ok" or result.get("chunk_index") != index):
                raise ValueError("Motion Director не подтвердил часть видео")
        if result.get("type") != "input" or not isinstance(result.get("name"), str) or not result["name"]:
            raise ValueError("Motion Director не подтвердил загрузку видео в input")
        binding = ImageBinding(asset_id=asset_id,
            image_file=str(PurePosixPath(result.get("subfolder") or "") / result["name"]))
        return {"asset_id": asset_id, "video_file": binding.image_file, "sha256": digest,
                "bytes": size, "uploaded": True, "submitted": False}


class SourceVideo(StrictInput):
    asset_id: str | None = None  # Required for execution; compile may describe an already-uploaded input.
    video_file: str = Field(min_length=1)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_range(self):
        ImageBinding(asset_id="source", image_file=self.video_file)
        if self.end <= self.start or self.end - self.start > 15:
            raise ValueError("Диапазон исходника должен быть положительным и не длиннее 15 секунд")
        return self


class ReplacementBinding(StrictInput):
    owner: str = Field(min_length=1, max_length=80, pattern=r"^[^<>\r\n]+$")
    performer: str = Field(min_length=3, max_length=300, pattern=r"^[^\r\n]+$")
    body_asset_id: str = Field(min_length=1)
    face_asset_id: str | None = None
    costume_asset_id: str | None = None


class CompileSegment(StrictInput):
    id: str = Field(min_length=1, max_length=100)
    mode: Literal["t2v", "i2v", "fl2v", "r2v", "source_video"]
    prompt: str = Field(min_length=1, max_length=20000)
    duration: float = Field(ge=1, le=15, allow_inf_nan=False)
    pictures: list[ImageBinding] = Field(default_factory=list, max_length=8)
    source_video: SourceVideo | None = None
    replacements: list[ReplacementBinding] = Field(default_factory=list, max_length=4)
    visual_continuity: bool = False
    audio_continuity: bool = False

    @model_validator(mode="after")
    def check_media(self):
        if (self.mode == "source_video") != (self.source_video is not None):
            raise ValueError("Исходное видео требуется только для режима source_video")
        if self.source_video and not math.isclose(self.duration, self.source_video.end - self.source_video.start, abs_tol=0.001):
            raise ValueError("Длительность сегмента должна совпадать с диапазоном исходника")
        if self.replacements and self.mode != "source_video":
            raise ValueError("Карта замены применяется только к source_video")
        if self.mode == "source_video" and self.pictures and not self.replacements:
            raise ValueError("Для замены по видео укажи replacements: исполнитель, герой и его полный образ")
        ids = {picture.asset_id for picture in self.pictures}
        seen_owners, seen_bodies, seen_performers = set(), set(), set()
        for binding in self.replacements:
            if binding.owner.strip().casefold() in seen_owners or binding.body_asset_id in seen_bodies or binding.performer.strip().casefold() in seen_performers:
                raise ValueError("Каждому герою нужны собственные исполнитель и полный образ")
            required = {binding.body_asset_id, binding.face_asset_id, binding.costume_asset_id} - {None}
            if required - ids:
                raise ValueError("Референсы карты замены должны присутствовать в pictures сегмента")
            seen_owners.add(binding.owner.strip().casefold())
            seen_bodies.add(binding.body_asset_id)
            seen_performers.add(binding.performer.strip().casefold())
        count = len(self.pictures)
        if (self.mode == "t2v" and count) or (self.mode == "i2v" and count != 1) or (self.mode == "fl2v" and count != 2) or (self.mode == "r2v" and not count):
            raise ValueError("Число изображений не соответствует режиму сегмента")
        return self


class CompileTimeline(StrictInput):
    segments: list[CompileSegment] = Field(min_length=1, max_length=60)
    width: int = Field(ge=32, le=8192, multiple_of=32)
    height: int = Field(ge=32, le=8192, multiple_of=32)

    def compile(self) -> dict:
        # Validate shared identity/order rules without importing the GPL runtime.
        RerunPlan(segments=[Segment(**s.model_dump(exclude={"pictures", "prompt", "duration", "source_video", "replacements"})) for s in self.segments],
                  changed_ids=[self.segments[0].id])
        segments = []
        for segment in self.segments:
            images = [{"assetId": ref.asset_id, "imageFile": ref.image_file, "index": i} for i, ref in enumerate(segment.pictures)]
            inputs = {"resultRefs": []}
            if segment.mode == "r2v": inputs["pictures"] = images
            elif segment.mode == "i2v": inputs["startFrame"] = images[0]
            elif segment.mode == "fl2v": inputs.update(firstFrame=images[0], lastFrame=images[1])
            frames = 5 + 17 * math.ceil(max(0, round(segment.duration * 24) - 5) / 17)
            if segment.source_video:
                source = segment.source_video
                inputs.update(sourceVideo={"videoFile": source.video_file, "type": "input",
                                           "range": {"startSec": source.start, "endSec": source.end}},
                              identityPictures=images)
                frames = max(4, round(segment.duration * 24))
            prompt = segment.prompt
            if segment.replacements:
                from .character_identity import replacement_identity, validate_replacement_prompt
                validate_replacement_prompt(prompt)
                claims = {}
                for binding in segment.replacements:
                    for asset, role in ((binding.body_asset_id, "Персонаж целиком"),
                                        (binding.face_asset_id, "Лицо героя"), (binding.costume_asset_id, "Костюм")):
                        if not asset:
                            continue
                        if asset in claims and claims[asset][0] != binding.owner:
                            raise ValueError("Один референс не может определять двух героев")
                        if asset not in claims or claims[asset][1] != "Персонаж целиком":
                            claims[asset] = (binding.owner, role)
                images = tuple({"job_owner": claims.get(picture.asset_id, ("", ""))[0],
                                "job_role": claims.get(picture.asset_id, ("", ""))[1]}
                               for picture in segment.pictures)
                for binding in segment.replacements:
                    prompt += (f"\nReplace {binding.performer} with {binding.owner}. "
                               + replacement_identity(binding.owner, "<Video 1>", images))
            segments.append({"id": segment.id, "mode": segment.mode, "prompt": prompt,
                             "duration": segment.duration, "frameCount": frames, "inputs": inputs,
                             "continuity": {"visual": segment.visual_continuity, "audio": segment.audio_continuity}})
        timeline = {"version": 1, "timelineMode": "mixed", "frameRate": 24, "width": self.width, "height": self.height,
                    "output": {"mode": "fixed", "width": self.width, "height": self.height, "exportMode": "all", "audioMode": "generate"},
                    "runSelectEnabled": False, "runSelection": [], "segments": segments}
        return {"timeline_data": json.dumps(timeline, ensure_ascii=False), "total_frames": sum(s["frameCount"] for s in segments),
                "submitted": False, "media_verified": False,
                "note": "Файлы должны быть загружены в ComfyUI/input; компиляция не подтверждает их наличие или личность героев."}


class GraphPlan(CompileTimeline):
    seed: int = Field(ge=0, le=2**53 - 1)
    steps: int = Field(default=25, ge=20, le=25)  # user decisions 16.10/04.11: never more than 25

    def graph(self, director_id: str = "5", run_selection: list[int] | None = None) -> dict:
        """director_id names the node, and with it the node's segment cache folder on the GPU."""
        from app.h3graph.core import VIDEO_VAE, AUDIO_VAE
        from app.render_profiles import PROFILES
        compiled = self.compile()
        if run_selection:
            timeline = json.loads(compiled["timeline_data"])
            timeline.update(runSelectEnabled=True, runSelection=sorted(run_selection))
            compiled["timeline_data"] = json.dumps(timeline, ensure_ascii=False)
        def node(kind, **inputs):
            return {"class_type": kind, "inputs": inputs}
        graph = {
            "1": node("UNETLoader", unet_name=PROFILES["bf16"]["unet"], weight_dtype="default"),
            "2": node("CLIPLoader", clip_name=PROFILES["bf16"]["clip"], type="minimax", device="default"),
            "3": node("VAELoader", vae_name=VIDEO_VAE),
            "4": node("VAELoader", vae_name=AUDIO_VAE),
            director_id: node("MiniMaxH3MotionDirector", model=["1", 0], clip=["2", 0], video_vae=["3", 0], audio_vae=["4", 0],
                      task_type="mixed — 混合模式(Mixed)", global_prompt="", bd_grp_sample="采样设置", cfg=1.0, seed=self.seed,
                      frame_rate=24.0, width=self.width, height=self.height, ref_max_size=max(self.width, self.height),
                      total_frames=compiled["total_frames"], timeline_data=compiled["timeline_data"],
                      motion_context_enabled=True, audio_context_enabled=True, context_length=22, source_overlap_frames=0,
                      steps=self.steps, sampler_name="res_multistep", scheduler="simple", shift_video=12.0, shift_audio=3.0,
                      clear_vram_between_segments=True, postprocess_config=""),
            "6": node("CreateVideo", images=[director_id, 0], audio=[director_id, 1], fps=[director_id, 2],
                      bit_depth=8, color_space="sRGB", codec="none"),
            "7": node("SaveVideo", video=["6", 0], filename_prefix="video/Studio_Motion_Director", format="auto",
                      codec="auto", **{"format.codec": "auto"}),
        }
        return {**compiled, "graph": graph, "required_nodes": sorted({n["class_type"] for n in graph.values()}),
                "graph_verified": False, "steps": self.steps, "seed": self.seed}

    def run_selection(self, previous: "GraphPlan | None") -> list[int] | None:
        """Indices to regenerate against the scene's last completed plan; None means the whole timeline.

        Fingerprints chain through continuity, so a changed segment also selects what continues it.
        A segment that moved on the timeline is selected too: the node keys its cache by position."""
        if previous is None:
            return None
        def placed(plan):
            start, spans = 0, {}
            for index, segment in enumerate(json.loads(plan.compile()["timeline_data"])["segments"]):
                spans[segment["id"]] = (index, start, segment["frameCount"])
                start += segment["frameCount"]
            return spans
        old, new = previous.fingerprints(), self.fingerprints()
        old_span, new_span = placed(previous), placed(self)
        changed = [index for index, segment in enumerate(self.segments)
                   if old.get(segment.id) != new[segment.id] or old_span.get(segment.id) != new_span[segment.id]]
        return changed if changed and len(changed) < len(self.segments) else None

    def fingerprints(self) -> dict[str, str]:
        """Execution inputs including incoming dependencies; never a claim of cached output."""
        self.compile()  # Validate unique IDs and boundary order first.
        versions = {}
        previous = None
        settings = self.model_dump(exclude={"segments"})
        for segment in self.segments:
            inputs = {"settings": settings, "segment": segment.model_dump(),
                      "previous": previous if segment.visual_continuity or segment.audio_continuity else None}
            digest = json_sha256(inputs)
            versions[segment.id] = digest
            previous = digest
        return versions


class SavedPlanInput(StrictInput):
    plan: GraphPlan
    expected_revision: int = Field(ge=0)  # 0 creates; edits compare against the last GET.


class Segment(StrictInput):
    id: str = Field(min_length=1, max_length=100)
    mode: Literal["t2v", "i2v", "fl2v", "r2v", "source_video"]
    visual_continuity: bool = False
    audio_continuity: bool = False
    result_sources: list[str] = Field(default_factory=list)


class RerunPlan(StrictInput):
    segments: list[Segment] = Field(min_length=1, max_length=60)
    changed_ids: list[str] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def check_links(self):
        seen = set()
        for segment in self.segments:
            if segment.id in seen:
                raise ValueError(f"Повтор ID сегмента: {segment.id}")
            if set(segment.result_sources) - seen:
                raise ValueError(f"{segment.id}: ссылка должна вести на существующий предыдущий сегмент")
            if not seen and (segment.visual_continuity or segment.audio_continuity):
                raise ValueError("Первый сегмент не может продолжать предыдущий")
            seen.add(segment.id)
        if set(self.changed_ids) - seen:
            raise ValueError("Изменённый сегмент отсутствует в сцене")
        return self

    def plan(self) -> dict:
        dependencies = {}
        invalidated = set(self.changed_ids)
        reasons = {}
        for index, segment in enumerate(self.segments):
            sources = set(segment.result_sources)
            if index and (segment.visual_continuity or segment.audio_continuity):
                sources.add(self.segments[index - 1].id)
            dependencies[segment.id] = sorted(sources)
            stale = sources & invalidated
            if stale:
                invalidated.add(segment.id)
                reasons[segment.id] = sorted(stale)
        ordered = [segment.id for segment in self.segments]
        return {"changed_ids": [key for key in ordered if key in self.changed_ids],
                "rerun_ids": [key for key in ordered if key in invalidated],
                "reusable_ids": [key for key in ordered if key not in invalidated],
                "dependencies": dependencies, "invalidation_reasons": reasons,
                "submitted": False,
                "note": "Незатронутые сегменты можно использовать только при совпадении сохранённых версий входов и кэша."}
