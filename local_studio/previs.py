"""«Превиз»: Blockout (github.com/wassermanproductions/blockout, Apache-2.0, ~/Applications/Blockout.app) as the
Studio's staging stage. The Studio agent blocks a shot there — heroes at real scale (a cat beside a man), marks,
lens, camera move — and takes the grey frame back into the library as «Композиция»: where the figures stand, the
shot size and the angle, without anyone's face (REFERENCE_ROLE_PROMPTS); a moving camera comes back as a grey video
with the role «Камера» (camera framing, movement and depth only — the mannequins' gait is not the heroes'). Blockout runs on this Mac and announces a
localhost port and token in ~/.config/blockout/control.json; nothing leaves the machine."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

CONTROL = Path(os.environ.get("BLOCKOUT_CONFIG_DIR") or Path.home() / ".config" / "blockout") / "control.json"
APP = Path.home() / "Applications" / "Blockout.app"
# Blockout's own MCP tools (Resources/mcp/blockout-mcp.mjs, v5.0.0). set_reference reads any local path: left out.
ACTIONS = {"get_state", "list_assets", "add_entity", "move_entity", "delete_entity", "add_actor_mark",
           "add_camera_mark", "clear_camera_marks", "set_shot", "new_shot", "apply_framing", "list_action_presets",
           "apply_action_preset", "list_sequence_styles", "spawn_sequence", "list_camera_moves", "apply_camera_move",
           "set_track_subject", "snap_to_ground", "set_time", "play", "stop", "screenshot", "list_presets",
           "save_preset", "apply_preset"}
# Only «Композиция»: a grey-box frame as «Кадр сцены» would make H3 render grey dummies.
FRAME_ROLES = {"Композиция"}
NOT_RUNNING = "Blockout не запущен: открой ~/Applications/Blockout.app (превиз), затем повтори"
NO_PROJECT = "В Blockout не открыт проект: нажми в его окне New project (или открой свой) — один раз после запуска"
H3_MAX_SECONDS = 15.08  # 362 frames at 24 fps


class Call(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)


class Motion(BaseModel):
    name: str = Field(default="previs", max_length=80)
    fps: int = Field(default=6, ge=2, le=12)


class Snapshot(BaseModel):
    name: str = Field(default="previs", max_length=80)
    role: str = "Композиция"
    time: float | None = Field(default=None, ge=0, le=60)


def crop(data: bytes, aspect: str, grey: bool = False, blur: float = 4) -> bytes:
    """Blockout renders every still 960×540: cut the shot's own aspect from the centre (what the agent looks at and
    what is saved are the same frame). `grey`: the «Композиция» contract — a blurred grey layout sketch (positions,
    size, angle; labels and colours do not survive)."""
    from PIL import Image, ImageFilter
    image = Image.open(io.BytesIO(data))
    try:
        w, h = (float(x) for x in aspect.split(":"))
    except ValueError:
        w, h = image.width, image.height
    width = min(image.width, round(image.height * w / h))
    height = min(image.height, round(width * h / w))
    left, top = (image.width - width) // 2, (image.height - height) // 2
    image = image.crop((left, top, left + width, top + height))
    if grey:
        image = image.convert("L")
        if blur:
            image = image.filter(ImageFilter.GaussianBlur(blur))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def control() -> dict | None:
    try:
        config = json.loads(CONTROL.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if config.get("app", "blockout") != "blockout" or config.get("protocolVersion", 1) != 1:
        return None
    return config


async def call(action: str, params: dict | None = None) -> dict:
    if action not in ACTIONS:
        raise HTTPException(422, f"Действие превиза «{action}» не поддерживается")
    params = dict(params or {})
    if action == "set_time" and "time" in params:  # the app reads `t`; its MCP schema says `time`
        params["t"] = params.pop("time")
    config = control()
    if not config:
        raise HTTPException(503, NOT_RUNNING)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"http://127.0.0.1:{int(config['port'])}/rpc",
                                         headers={"Authorization": f"Bearer {config['token']}"},
                                         json={"action": action, "params": params})
        answer = response.json()
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(503, NOT_RUNNING) from None
    if not answer.get("ok", False):
        error = str(answer.get("error") or answer)
        raise HTTPException(409 if "No project open" in error else 422,
                            NO_PROJECT if "No project open" in error else f"Blockout: {error}")
    return answer.get("data") or {}


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/previs")

    @router.get("/status")
    async def status() -> dict:
        config = control()
        if not config:
            return {"running": False, "installed": APP.is_dir(), "message": NOT_RUNNING}
        try:
            state = await call("get_state")
        except HTTPException as exc:
            return {"running": False, "installed": APP.is_dir(), "message": exc.detail}
        return {"running": True, "installed": True, "version": config.get("appVersion", ""),
                "scene": state.get("scene", {}).get("name", ""), "shot": state.get("shot", {}).get("name", "")}

    @router.post("/call")
    async def proxy(payload: Call) -> dict:
        data = await call(payload.action, payload.params)
        if payload.action == "screenshot":  # pictures go into the library with /snapshot, not through JSON
            return {"screenshot": True, "bytes": len(data.get("imageBase64", "")) * 3 // 4}
        return data

    @router.get("/frame")
    async def frame() -> Response:
        """The current staged frame in the shot's aspect, to look at (the agent's previs_view); nothing is saved."""
        return Response(crop(await png(), await aspect()), media_type="image/png")

    async def aspect() -> str:
        return str((await call("get_state")).get("shot", {}).get("aspect") or "16:9")

    async def png() -> bytes:
        try:
            data = base64.b64decode((await call("screenshot")).get("imageBase64", ""), validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(502, "Blockout не отдал PNG кадра")
        return data

    @router.post("/snapshot", status_code=201)
    async def snapshot(payload: Snapshot) -> dict:
        """The staged frame (at `time` seconds of the shot) into the library as «Композиция», in the shot's aspect."""
        if payload.role not in FRAME_ROLES:
            raise HTTPException(422, f"Кадр превиза бывает только «{'» или «'.join(sorted(FRAME_ROLES))}»")
        if payload.time is not None:
            await call("set_time", {"t": payload.time})
        data = crop(await png(), await aspect(), grey=True)
        return save(data, "image", payload.role, payload.name, "png", "image/png")

    def save(data: bytes, kind: str, role: str, name: str, ext: str, mime: str) -> dict:
        sha256 = hashlib.sha256(data).hexdigest()
        folder = store.assets_dir / sha256[:2]
        folder.mkdir(exist_ok=True)
        destination = folder / sha256
        if not destination.exists():
            temporary = folder / f".previs-{sha256[:8]}.part"
            temporary.write_bytes(data)
            os.replace(temporary, destination)
        name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in name).strip() or "previs"
        return store.add_asset(kind=kind, role=role, original_name=f"previs-{name}.{ext}", mime=mime,
                               size=len(data), sha256=sha256, path=destination)

    @router.post("/motion", status_code=201)
    async def motion(payload: Motion) -> dict:
        """The shot's camera move as a grey video (Blockout's whole shot timeline, its aspect, 24 fps) into the library
        as «Камера»: H3 takes framing, movement and depth from it; heroes, place and acting come from their own
        references and the text."""
        shot = (await call("get_state")).get("shot", {})
        if len(shot.get("cameraMarks") or []) < 2:
            raise HTTPException(422, "Камера шота в превизе стоит (меньше двух меток камеры) — видео не нужно, хватит "
                                     "«Композиции». Для движения поставь метки камеры или apply_camera_move")
        duration = float(shot.get("duration") or 0)
        if not 1 <= duration <= H3_MAX_SECONDS:
            raise HTTPException(422, f"Шот превиза {duration:g} с: для H3 нужно 1–15 с (set_shot duration)")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(503, "Для видео превиза нужен ffmpeg на Mac")
        shape = str(shot.get("aspect") or "16:9")
        count = int(duration * payload.fps) + 1
        with tempfile.TemporaryDirectory(prefix="h3-previs-") as tmp:
            try:
                for i in range(count):
                    await call("set_time", {"t": min(i / payload.fps, duration)})
                    (Path(tmp) / f"f{i:04d}.png").write_bytes(crop(await png(), shape, grey=True, blur=0))
            finally:
                await call("set_time", {"t": 0})
            out = Path(tmp) / "camera.mp4"
            result = await asyncio.to_thread(subprocess.run, [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(payload.fps),
                "-i", str(Path(tmp) / "f%04d.png"), "-vf",
                "scale=trunc(iw*2/2)*2:trunc(ih*2/2)*2:flags=lanczos,fps=24,format=yuv420p",
                "-t", f"{duration:.3f}", "-c:v", "libx264", "-crf", "18", "-an", str(out)],
                capture_output=True, timeout=300)
            if result.returncode or not out.is_file():
                raise HTTPException(500, "Видео превиза не собрано: " + result.stderr.decode(errors="replace")[-300:])
            data = out.read_bytes()
        return save(data, "video", "Камера", payload.name, "mp4", "video/mp4") | {"duration_s": duration}

    return router
