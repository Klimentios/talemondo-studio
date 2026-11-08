"""«Кадр сцены» must sit on the source's own cuts.

Guide frames are the only input H3 aligns to the target timeline (model.py: clean rows at cursor + frame_idx); the
reference video is not time-aligned. Guides taken from another part's shot plan (1.9/3.8/4.6/5.33 s against measured
cuts 4.58/5.33/7.75/8.92 s) make the shots drift. This gate measures the source part's cuts and
refuses source shots of MIN_SHOT_S or longer left without their own guide and guides just before a cut.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

FPS = 24
TOLERANCE_S = 0.15  # a guide within ±3–4 frames of the measured cut
BLEED_S = 0.5  # a guide this close before a cut shows the next shot's content in this one
MIN_SHOT_S = 1.0
SCENE_THRESHOLD = 0.3
MOTION_ROLES = frozenset({"Движение и камера", "Исходное видео", "Движение"})


_CUTS: dict[tuple[str, float, float], list[float]] = {}


def measured_cuts(video: Path, duration: float) -> list[float]:
    key = (str(video), video.stat().st_mtime, duration)
    if key not in _CUTS:
        _CUTS[key] = _measure(video, duration)
    return _CUTS[key]


def _measure(video: Path, duration: float) -> list[float]:
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(video), "-map", "0:v:0",
                             "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=90, check=False)
    times = sorted({round(float(t), 3) for t in re.findall(r"pts_time:([\d.]+)", result.stderr)})
    return [t for t in times if 0.2 < t < duration - 0.2]


def guide_cut_problems(guide_frames: list, cuts: list[float], duration: float) -> list[str]:
    """Pure check against the measured cuts.

    - every source shot of MIN_SHOT_S or longer has its own «Кадр сцены» (from TOLERANCE_S before its cut to its end);
    - no guide sits just before a cut (BLEED_S..TOLERANCE_S before it): it would carry the next shot into this one.
    A guide anywhere else inside a shot is a legitimate anchor (a key beat mid-shot).
    """
    problems = []
    times = sorted(0.0 if idx == 0 else idx / FPS for _, idx in guide_frames if idx != -1)
    for t in times:
        before = [c for c in cuts if TOLERANCE_S < c - t < BLEED_S]
        if before:
            problems.append(f"«Кадр сцены» на {t:.2f} с за {before[0] - t:.2f} с до склейки {before[0]:.2f} с: "
                            "поставьте его на склейку или внутрь шота")
    edges = [0.0, *cuts, duration]
    for start, end in zip(edges, edges[1:]):
        if end - start >= MIN_SHOT_S and not any(start - TOLERANCE_S <= t < end for t in times):
            problems.append(f"шот исходника {start:.2f}–{end:.2f} с без своего «Кадра сцены»")
    return problems


def check(store, *, settings: dict, manifest: dict) -> None:
    """Raise before the GPU when a guided job's «Кадры сцены» are off the source part's measured cuts."""
    guides = settings.get("guide_frames") or []
    videos = [v for v in manifest.get("videos") or [] if v.get("role") in MOTION_ROLES]
    world_swap = (settings.get("mode") == "animation3d" and any(
        v.get("role") == "Движение и камера" and v.get("performers") for v in videos))
    if world_swap and not guides:
        raise ValueError("Смена героев и мира по видео требует «Кадр сцены» на каждый шот и guide_frames; "
                         "без них H3 возвращает исходных героев и комнату")
    if world_swap and not any(isinstance(guide, (list, tuple)) and len(guide) == 2
                              and guide[1] == 0 for guide in guides):
        raise ValueError("Смена мира требует первый «Кадр сцены» на кадре 0; иначе начало берётся из исходника")
    if not guides or len(videos) != 1:
        return
    asset = store.get_asset(videos[0]["asset_id"])
    path = Path(asset["path"]) if asset and asset.get("path") else None
    if not path or not path.is_file():
        return
    duration = float(settings.get("duration") or 0) or 0.0
    problems = guide_cut_problems(guides, measured_cuts(path, duration or 600.0), duration)
    if problems:
        raise ValueError("Кадры сцены не совпадают со склейками исходника (замер ffmpeg): " + "; ".join(problems)
                         + ". План шотов и guide_frames брать из замеренных склеек этой части.")
