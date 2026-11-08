"""«Раскадровка»: finished shot MP4s glued in order into one long clip.

Stream copy (concat demuxer) when every shot has identical video/audio parameters — the
usual case, all shots come from one H3 recipe. Otherwise one re-encode through the concat
filter (H.264 CRF 14 + AAC 256k; a shot without sound gets silence). The result is checked
with ffprobe before it is offered for playback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .production_review import output_digest
from .assembly_receipt import assembly_snapshot, verify_sources, file_sha256
from .take_acceptance import take_is_accepted
from .retention import prune
from .store import StudioStore

MAX_SHOTS = 60
VIDEO_KEYS = ("codec_name", "profile", "width", "height", "pix_fmt", "r_frame_rate", "time_base", "sample_aspect_ratio")
AUDIO_KEYS = ("codec_name", "sample_rate", "channels", "channel_layout", "time_base")


class ShotsInput(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    # Frames cut off each shot's head: the tail part of a «Замена по видео» plan reaches back over the part before it
    # (video_swap.split_parts glue_trim_frames). Without it a continuation's own trim is used.
    trim_frames: list[int] | None = None
    tail_frames: list[int] | None = None  # frames cut off each shot's end (a plan part's run-on past its cut)


def _tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    return ffmpeg, ffprobe


def probe(path: Path, ffprobe: str) -> dict:
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise ValueError(f"ffprobe не прочитал {path.name}: {result.stderr[:200]}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError(f"В {path.name} нет видеодорожки")
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    return {"video": {k: video.get(k) for k in VIDEO_KEYS},
            "audio": {k: audio.get(k) for k in AUDIO_KEYS} if audio else None,
            "duration": duration}


def shot_paths(store: StudioStore, job_ids: list[str]) -> list[Path]:
    if not job_ids:
        raise ValueError("Нет шотов для склейки")
    if len(job_ids) > MAX_SHOTS:
        raise ValueError(f"Не больше {MAX_SHOTS} шотов за одну склейку")
    paths = []
    for index, job_id in enumerate(job_ids, 1):
        job = store.get_job(job_id) if isinstance(job_id, str) else None
        if not job:
            raise ValueError(f"Шот {index}: задание не найдено")
        if job["state"] != "completed" or not job.get("output_path"):
            raise ValueError(f"Шот {index} ещё не готов — склеить можно только готовые ролики")
        path = Path(job["output_path"]).resolve()
        if not path.is_file() or not path.is_relative_to(store.results_dir.resolve()):
            raise ValueError(f"Шот {index}: MP4 не найден")
        _, verified_path, _ = output_digest(store, job_id)
        if verified_path.resolve() != path:
            raise ValueError(f"Шот {index}: путь результата изменился во время проверки")
        paths.append(path)
    return paths


def concat(paths: list[Path], output: Path, ffmpeg: str, ffprobe: str, trim_frames: list[int] | None = None,
           tail_frames: list[int] | None = None) -> dict:
    infos = [probe(path, ffprobe) for path in paths]
    trims = trim_frames if trim_frames is not None else [0] * len(paths)
    tails = tail_frames if tail_frames is not None else [0] * len(paths)
    if (len(trims) != len(paths) or len(tails) != len(paths)
            or any(type(n) is not int or n < 0 for n in [*trims, *tails])):
        raise ValueError("Неверное перекрытие шотов")
    offsets, ends = [], []
    for count, tail, info in zip(trims, tails, infos):
        numerator, denominator = map(int, (info['video']['r_frame_rate'] or '24/1').split('/'))
        if numerator <= 0 or denominator <= 0:
            raise ValueError("Неверная частота кадров шота")
        offset = count * denominator / numerator
        end = tail * denominator / numerator
        if offset + end >= info['duration']:
            raise ValueError("Перекрытие удаляет весь шот")
        offsets.append(offset)
        ends.append(end)
    same = all(info["video"] == infos[0]["video"] and info["audio"] == infos[0]["audio"] for info in infos)
    any_audio = any(info["audio"] for info in infos)
    tmp = output.with_name(f".{output.stem}.tmp.mp4")
    if same and not any(trims) and not any(tails):
        listing = output.with_suffix(".txt")
        listing.write_text("".join(f"file '{str(p).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for p in paths))
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-map", "0:v:0", *(["-map", "0:a:0"] if any_audio else []), "-c", "copy", "-movflags", "+faststart", str(tmp)]
        mode = "copy"
    else:
        listing = None
        width, height = infos[0]["video"]["width"], infos[0]["video"]["height"]
        rate = infos[0]["video"]["r_frame_rate"] or "24/1"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        for path in paths:
            cmd += ["-i", str(path)]
        filters, labels = [], []
        for index, info in enumerate(infos):
            duration = info['duration'] - offsets[index] - ends[index]
            trim = (f"trim=start_frame={trims[index]}" + (f":duration={duration:.4f}" if tails[index] else '')
                    + ",setpts=PTS-STARTPTS,") if trims[index] or tails[index] else ''
            filters.append(f"[{index}:v:0]{trim}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                           f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={rate},format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
            labels.append(f"[v{index}]")
            if any_audio:
                if info["audio"]:
                    audio_trim = f"atrim=start={offsets[index]:.8f},asetpts=PTS-STARTPTS," if trims[index] else ''
                    filters.append(f"[{index}:a:0]{audio_trim}aresample=48000,aformat=channel_layouts=stereo,"
                                   f"apad,atrim=duration={duration:.4f},asetpts=PTS-STARTPTS[a{index}]")
                else:
                    filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration:.4f}[a{index}]")
                labels.append(f"[a{index}]")
        filters.append("".join(labels) + f"concat=n={len(paths)}:v=1:a={1 if any_audio else 0}[vout]" + ("[aout]" if any_audio else ""))
        cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", *(["-map", "[aout]"] if any_audio else []),
                "-c:v", "libx264", "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p",
                *(["-c:a", "aac", "-b:a", "256k"] if any_audio else []), "-movflags", "+faststart", str(tmp)]
        mode = "reencode"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, check=False)
        if result.returncode != 0 or not tmp.is_file():
            raise ValueError(f"FFmpeg не склеил шоты: {result.stderr[-400:]}")
        made = probe(tmp, ffprobe)
        expected = sum(info["duration"] for info in infos) - sum(offsets) - sum(ends)
        if any_audio and not made["audio"]:
            raise ValueError("Склейка потеряла звук")
        if abs(made["duration"] - expected) > max(0.5, 0.05 * len(paths)):
            raise ValueError(f"Длительность склейки {made['duration']:.2f} с вместо {expected:.2f} с")
        tmp.replace(output)
    finally:
        tmp.unlink(missing_ok=True)
        if listing:
            listing.unlink(missing_ok=True)
    return {"mode": mode, "duration": round(made["duration"], 3), "has_audio": bool(made["audio"]),
            "shots": [round(info["duration"], 3) for info in infos],
            **({"trim_frames": trims} if any(trims) else {})}


def make_router(store: StudioStore, scene_runs=None) -> APIRouter:
    router = APIRouter()
    directory = store.root / "storyboards"
    directory.mkdir(parents=True, exist_ok=True)

    def path_for(concat_id: str) -> Path:
        if not concat_id or len(concat_id) != 32 or not all(c in "0123456789abcdef" for c in concat_id):
            raise HTTPException(404, "Склейка не найдена")
        path = directory / f"{concat_id}.mp4"
        if not path.is_file():
            raise HTTPException(404, "Склейка не найдена")
        return path

    @router.post("/api/storyboard/concat", status_code=201)
    async def make_concat(payload: ShotsInput) -> dict:
        if scene_runs is not None:
            requested = set(payload.job_ids)
            for run in scene_runs.all():
                rejected = {item.get("job_id") for item in run.get("flagged", [])}
                if requested & rejected:
                    raise HTTPException(422, "Отклонённый дубль сцены нельзя склеить")
                for cut in run.get("cuts", []):
                    job_id = cut.get("shotJobId")
                    historical = {take.get("jobId") for take in cut.get("takes", [])}
                    if requested & (historical - {job_id}):
                        raise HTTPException(422, f"Шот {cut.get('id')}: старый дубль не принят после QC")
                    if job_id not in requested:
                        continue
                    if not take_is_accepted(cut):
                        raise HTTPException(422, f"Шот {cut.get('id')}: дубль не принят после QC")
        ffmpeg, ffprobe = _tools()
        try:
            paths = await asyncio.to_thread(shot_paths, store, payload.job_ids)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        concat_id = uuid.uuid4().hex
        output = directory / f"{concat_id}.mp4"
        try:
            trims = payload.trim_frames if payload.trim_frames is not None else [
                int(json.loads(store.get_job(uid)['settings_json']).get('continuation_trim_frames',0)) for uid in payload.job_ids]
            sources = {f"{index}:{uid}": path for index, (uid, path) in enumerate(zip(payload.job_ids, paths))}
            snapshot = await asyncio.to_thread(assembly_snapshot, sources,
                {"job_ids": payload.job_ids, "trim_frames": trims, "tail_frames": payload.tail_frames})
            info = await asyncio.to_thread(concat, paths, output, ffmpeg, ffprobe,
                                           **({"trim_frames":trims} if any(trims) else {}),
                                           **({"tail_frames":payload.tail_frames} if payload.tail_frames else {}))
            await asyncio.to_thread(verify_sources, sources, snapshot)
            output_hash = await asyncio.to_thread(file_sha256, output)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            output.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
        (directory / f"{concat_id}.json").write_text(json.dumps({"job_ids": payload.job_ids, **info,
            "snapshot": snapshot, "output_sha256": output_hash}))
        try:
            await asyncio.to_thread(prune, directory, store)
        except Exception:
            logging.getLogger("h3.studio").exception("Не удалось удалить старые склейки")
        return {"concat_id": concat_id, "job_ids": payload.job_ids, **info,
                "url": f"/api/storyboard/concat/{concat_id}/file"}

    @router.get("/api/storyboard/concat/{concat_id}/file")
    async def concat_file(concat_id: str, download: bool = Query(False)) -> FileResponse:
        return FileResponse(path_for(concat_id), media_type="video/mp4",
                            filename=f"scene-{concat_id[:8]}.mp4" if download else None)

    @router.post("/api/storyboard/probe")
    async def probe_shots(payload: ShotsInput) -> list[dict]:
        """Durations and sound of finished shots, for «В монтаж»."""
        _, ffprobe = _tools()
        try:
            paths = await asyncio.to_thread(shot_paths, store, payload.job_ids)
            infos = [await asyncio.to_thread(probe, path, ffprobe) for path in paths]
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return [{"job_id": job_id, "duration": round(info["duration"], 3), "has_audio": bool(info["audio"]),
                 "prompt": (store.get_job(job_id) or {}).get("original_prompt", "")[:500]}
                for job_id, info in zip(payload.job_ids, infos)]

    return router
