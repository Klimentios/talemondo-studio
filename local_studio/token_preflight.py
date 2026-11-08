"""Pre-GPU check of the final H3 text as the model reads it (docs/reference/H3_ENGINE_INTERNALS.md).

ComfyUI splices "<Picture i>: ", "<Video k>: " and "<Audio j>: " labels in front of the prompt, one per
connected input of that type; a <Picture N> in our text is plain text that only points at media when its
number exists. <d>, </d> and <|cutoff|> are real tokenizer tokens; <cutoff> and <scenetrans> are not.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from app.h3_budget import job_budget

_TAG = re.compile(r"<(Picture|Video|Audio)\s+(\d+)>")


def label_problems(prompt: str, pictures: int, videos: int, audios: int) -> list[str]:
    """Labels in the text that name no connected input of their type."""
    limit = {"Picture": pictures, "Video": videos, "Audio": audios}
    wrong = sorted({f"<{kind} {n}>" for kind, n in _TAG.findall(prompt) if not 1 <= int(n) <= limit[kind]})
    return [f"{tag}: такого входа нет (картинок {pictures}, видео {videos}, аудио {audios})" for tag in wrong]


def dialogue_problems(prompt: str) -> list[str]:
    problems = []
    if prompt.count("<d>") != prompt.count("</d>"):
        problems.append(f"реплики не закрыты: <d> {prompt.count('<d>')}, </d> {prompt.count('</d>')}")
    if re.search(r"<d>(?!\s*\[[A-Za-z ]+\])", prompt):
        problems.append("реплика без языка: нужно <d>[English] …</d>")
    if re.search(r"<d>[^<]*<d>", prompt):
        problems.append("<d> открыт внутри другой реплики")
    return problems


def preflight(prompt: str, pictures: int, videos: int, audios: int) -> None:
    problems = label_problems(prompt, pictures, videos, audios) + dialogue_problems(prompt)
    if problems:
        raise ValueError("Текст для H3 не сходится с файлами задания: " + "; ".join(problems))


def _probe(path: str | Path) -> tuple[int, int, float] | None:
    ffprobe = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    try:
        result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                                 "stream=width,height:format=duration", "-of", "json", str(path)],
                                capture_output=True, text=True, timeout=10, check=False)
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        width, height = int(stream["width"]), int(stream["height"])
        return (width, height, float(data.get("format", {}).get("duration") or 0)) if width > 0 and height > 0 else None
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired):
        return None


def budget_for(prompt: str, width: int, height: int, seconds: float, images, videos, ref_image_size: str) -> dict | None:
    """Packed-sequence estimate for the job card; None when a file cannot be measured."""
    pictures, clips = [], []
    for image in images:
        size = _probe(image["path"])
        if not size:
            return None
        pictures.append(size[:2])
    for video in videos:
        size = _probe(video["path"])
        if not size:
            return None
        clips.append(size)
    if any(clip[2] <= 0 for clip in clips):
        return None
    return job_budget(width, height, seconds, pictures, clips, ref_image_size or "max", len(prompt))
