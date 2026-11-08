"""Prepare an H3 motion reference from a library clip with the pinned local ReShot CLI.

This changes only a library asset. The user still chooses its role and reviews the resulting
video before any H3 job; it never bypasses Studio's queue, passport or prompt checks.
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import ROOT
from .reference_excerpts import media_source
from .reference_segments import file_hash
from .store import _media_duration_seconds

RESHOT_COMMIT = "770d7220fe79fdf0fdaa84bdce047b90363fc1b5"
RESHOT_BIN = ROOT / ".venv" / "bin" / "reshot"


class MotionReferenceRequest(BaseModel):
    control: Literal["depth", "pose", "canny"] = "depth"


def prepare_motion_reference(store, uid: str, request: MotionReferenceRequest, *, binary: Path = RESHOT_BIN) -> dict:
    asset, source, seconds = media_source(store, uid)
    if asset["kind"] != "video":
        raise ValueError("Для карты движения нужен видеофайл")
    if seconds > 15.05:
        raise ValueError("ReShot/H3 принимает не более 15 с: сначала выбери участок видео")
    if file_hash(source) != asset["sha256"]:
        raise ValueError("Исходное видео изменилось: добавь его заново")
    if not binary.is_file():
        raise ValueError("ReShot не установлен: запусти scripts/install_reshot.sh")
    with (store.assets_dir / ".reshot-reference.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        key = hashlib.sha256(f"{asset['sha256']}:{request.control}:{RESHOT_COMMIT}:h3:320".encode()).hexdigest()
        target = store.assets_dir / f"reshot-{key}.mp4"
        with store.connect() as db:
            previous = db.execute("SELECT * FROM assets WHERE path=?", (str(target),)).fetchone()
        if previous and target.is_file():
            if file_hash(target) != previous["sha256"]:
                raise ValueError("Сохранённая карта движения изменилась; повторно не используем")
            return {**dict(previous), "motion_reference": {"source_asset_id": uid, "control": request.control,
                                                               "upstream_commit": RESHOT_COMMIT, "cached": True}}
        with tempfile.TemporaryDirectory(dir=store.assets_dir, prefix=".reshot-") as temporary:
            input_file = Path(temporary) / "source.mp4"
            input_file.symlink_to(source)
            output = Path(temporary) / "control.mp4"
            command = [str(binary), str(input_file), "-o", str(output), "--target", "h3", "--control", request.control,
                       "--max-res", "320", "--model", "small"]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("ReShot не закончил подготовку за 15 минут") from exc
            if result.returncode or not output.is_file() or output.stat().st_size == 0:
                raise ValueError("ReShot не создал карту движения: " + (result.stderr or result.stdout)[-600:])
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=width,height,avg_frame_rate", "-of", "json", str(output)],
                                   capture_output=True, text=True, timeout=30, check=False)
            streams = json.loads(probe.stdout).get("streams", []) if probe.returncode == 0 else []
            video = streams[0] if streams else {}
            if (not video or video.get("width", 0) % 32 or video.get("height", 0) % 32
                    or video.get("avg_frame_rate") != "24/1"
                    or abs((_media_duration_seconds(output) or 0) - seconds) > 0.2):
                raise ValueError("ReShot создал файл вне контракта H3: проверь длительность, 24 fps и размер кратный 32")
            if previous and file_hash(output) != previous["sha256"]:
                raise ValueError("Повторная подготовка дала другой файл; проверь источник и версию ReShot")
            os.replace(output, target)
        role = "Движение" if request.control == "pose" else "Движение и камера"
        saved = dict(previous) if previous else store.add_asset(
            kind="video", role=role, original_name=f"{asset['original_name']} · ReShot {request.control}.mp4",
            mime="video/mp4", size=target.stat().st_size, sha256=file_hash(target), path=target)
        return {**saved, "motion_reference": {"source_asset_id": uid, "control": request.control,
                                                "upstream_commit": RESHOT_COMMIT, "cached": False}}


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.post("/api/assets/{uid}/motion-reference")
    async def create(uid: str, request: MotionReferenceRequest) -> dict:
        try:
            return await asyncio.to_thread(prepare_motion_reference, store, uid, request)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
