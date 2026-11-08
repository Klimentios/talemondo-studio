"""«Резкий зум»: speed-ramp the zoom of a finished crash-zoom take down to a few frames, as an editor would.

H3 picks its own pace for a zoom (1.7–2.6 s whatever the prompt asks), so the snap is made after the render.
Ported from LoopForge's snap.py (MIT, skills/h3-camera-shots/upstream/snap.py): every frame is compared (SSIM at
32 px) with the first and the last frame; the stretch where the picture leaves the opening framing for the closing
one is played TARGET_FRAMES long, its sound sped up by the same factor with the pitch kept. The take stays as H3
made it; the ramped clip is a new library video.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException

TARGET_FRAMES = 3  # 0.125 s at 24 fps, under a crash zoom's 0.2 s


def _tools() -> tuple[str, str]:
    return shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg", shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"


def _similarity(video: Path, ref: Path) -> list[float]:
    """SSIM of every frame against one still. ssim's own shortest=1 ends with the video: the looped still alone kept
    the upstream -shortest running forever on a clip without sound."""
    ffmpeg, _ = _tools()
    out = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(video), "-loop", "1", "-i", str(ref), "-filter_complex",
                          "[0:v]scale=32:-2[a];[a][1:v]ssim=shortest=1:stats_file=-", "-shortest", "-f", "null", "-"],
                         capture_output=True, text=True, check=True, timeout=300).stdout
    return [float(x) for x in re.findall(r"All:([0-9.]+)", out)]


def zoom_window(video: Path) -> tuple[int, int] | None:
    """The zoom as [start, end) frames: from where the picture stops looking like the opening framing to where it
    looks like the closing one. Levels, not rates: a face drifting inside a held framing is not a zoom."""
    ffmpeg, _ = _tools()
    with tempfile.TemporaryDirectory() as tmp:
        first, last = Path(tmp) / "first.png", Path(tmp) / "last.png"
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(video), "-vf", "scale=32:-2", "-frames:v", "1", str(first)],
                       check=True, timeout=120)
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-sseof", "-0.1", "-i", str(video), "-vf", "scale=32:-2",
                        "-update", "1", str(last)], check=True, timeout=120)
        to_first, to_last = _similarity(video, first), _similarity(video, last)
    n = min(len(to_first), len(to_last))
    if n < 20 or max(to_first) - min(to_first) < 0.15:
        return None  # the framing never changes much: no zoom
    head, low = max(to_first[:8]), min(to_first)
    start = next((i for i in range(n) if to_first[i] < head - 0.15 * (head - low)), None)
    if start is None:
        return None
    start = max(0, start - 2)
    floor = min(to_last[:start + 1])
    end = next((i for i in range(start, n) if to_last[i] >= floor + 0.92 * (1 - floor)), n - 1)
    return (start, end + 1) if end > start else None


def _atempo(factor: float) -> str:
    stages = []
    while factor > 2.0:  # atempo takes 0.5–2.0 per stage
        stages.append("atempo=2.0")
        factor /= 2.0
    return ",".join([*stages, f"atempo={factor:.4f}"])


def snap(src: Path, dst: Path) -> dict:
    """Write `dst` with the zoom ramped to TARGET_FRAMES frames; no zoom found (or already short) → ramped False."""
    ffmpeg, ffprobe = _tools()
    rate = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                           "-of", "csv=p=0", str(src)], capture_output=True, text=True, check=True).stdout.strip()
    num, _, den = rate.partition("/")
    fps = float(num) / float(den or 1)
    window = zoom_window(src)
    if not window or window[1] - window[0] <= TARGET_FRAMES:
        return {"ramped": False, "window": window}
    a, b = window
    factor = (b - a) / TARGET_FRAMES
    t0, t1 = a / fps, b / fps
    has_audio = bool(subprocess.run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                                     "-of", "csv=p=0", str(src)], capture_output=True, text=True).stdout.strip())
    graph = (f"[0:v]trim=end={t0},setpts=PTS-STARTPTS[v0];"
             f"[0:v]trim=start={t0}:end={t1},setpts=(PTS-STARTPTS)/{factor:.4f}[v1];"
             f"[0:v]trim=start={t1},setpts=PTS-STARTPTS[v2];"
             f"[v0][v1][v2]concat=n=3:v=1:a=0,fps={fps:g}[v]")
    maps = ["-map", "[v]"]
    if has_audio:
        graph += (f";[0:a]atrim=end={t0},asetpts=PTS-STARTPTS[a0];"
                  f"[0:a]atrim=start={t0}:end={t1},asetpts=PTS-STARTPTS,{_atempo(factor)}[a1];"
                  f"[0:a]atrim=start={t1},asetpts=PTS-STARTPTS[a2];"
                  "[a0][a1][a2]concat=n=3:v=0:a=1[a]")
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "256k"]
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(src), "-filter_complex", graph, *maps, "-c:v", "libx264",
                    "-preset", "slow", "-crf", "14", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)],
                   check=True, capture_output=True, timeout=900)
    return {"ramped": True, "window": [a, b], "seconds": [round(t0, 2), round(t1, 2)], "factor": round(factor, 1)}


def snap_job(store, job_id: str) -> dict:
    job = store.get_job(job_id)
    if not job or job["state"] != "completed" or not job.get("output_path"):
        raise ValueError("Нужен готовый дубль")
    source = Path(job["output_path"])
    if not source.is_file() or not source.resolve().is_relative_to(store.results_dir.resolve()):
        raise ValueError("Готовый ролик отсутствует")
    target = store.assets_dir / f"snap-zoom-{uuid.uuid4()}.mp4"
    try:
        report = snap(source, target)
        if not report["ramped"]:
            target.unlink(missing_ok=True)
            return {**report, "message": "Зум не найден или уже короткий — ролик не изменён"}
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing := store.find_asset(sha256=digest, kind="video"):
            target.unlink(missing_ok=True)
            return {**report, "asset": existing}
        asset = store.add_asset(kind="video", role="Движение", original_name=f"h3-{job_id[:8]} · резкий зум.mp4",
                                mime="video/mp4", size=target.stat().st_size, sha256=digest, path=target)
        return {**report, "asset": asset}
    except Exception:
        target.unlink(missing_ok=True)
        raise


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.post("/api/jobs/{job_id}/snap-zoom")
    async def snap_zoom(job_id: str) -> dict:
        try:
            return await asyncio.to_thread(snap_job, ctx.store, job_id)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
