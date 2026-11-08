"""«Проверить в Blockout»: the whole scene staged in Blockout from its shot cards and played back as one grey reel.

Each shot card already says who is in it, the shot size, the angle, the lens and the camera move (sceneShot). The
heroes stand once on one floor (one geography for every shot, so screen sides hold across cuts) at their real
scale from the franchise passport (a cat beside a man); every shot gets its camera from the card: the distance comes
from the lens and the shot size on the subject, the height from the angle, the move as camera marks. Blockout keeps
one shot at a time and cannot delete shots, so its one shot is re-set per scene shot, filmed frame by frame and
glued with ffmpeg. No GPU, nothing written into the scene: the reel is a check of the staging before any render.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.contract import ASPECT_PATTERN, ASPECTS

from . import previs
from .scene_kit import get_scene_kit

# Subject height ÷ frame height at the subject for each shot size (the middle of staging.framing_for's bands).
FRAME_RATIO = {"extreme_wide": 0.1, "wide": 0.3, "full": 0.75, "medium_full": 1.1, "medium": 1.6, "two_shot": 0.9,
               "over_shoulder": 1.6, "point_of_view": 0.75, "medium_close": 2.3, "close": 3.8, "insert": 5.0,
               "extreme_close": 7.0}
SENSOR_H_MM = 14.0       # Blockout lenses are Super 35; its 16:9 frame is 14 mm high and the 9:16 crop keeps that height
SPACING_M = 0.7          # heroes side by side on the floor (a cat at a man's feet)
FLOOR_Y = 3.0            # their line, metres away from the set's front
MIN_DISTANCE_M = 0.3
FPS = 12                 # frames filmed per second of the shot; the reel is re-timed to 24 fps
HEIGHTS = {"cat": 0.28, "woman": 1.66, "man": 1.78}
BODY = {"cat": (0.5, 0.2), "woman": (0.45, 0.3), "man": (0.5, 0.3)}  # (width, depth) in metres, for what the lens sees
# Set pieces behind the heroes, by words of the place: depth and scale for the eye, not decoration.
SET = (("door", [("furniture.doorOpen", 0.0)]), ("window", [("furniture.window", -1.6), ("furniture.window", 1.6)]),
       ("sofa|couch|living room|apartment|flat|room", [("furniture.couch", -2.2), ("furniture.rug", 0.0), ("furniture.lamp", 2.4)]),
       ("desk|laptop|monitor", [("furniture.desk", 2.0)]), ("kitchen", [("furniture.counter", -2.0), ("furniture.fridge", 2.4)]),
       ("balcony|loggia", [("furniture.window", -1.0), ("furniture.window", 1.0)]))
SET_Y = FLOOR_Y + 1.6
BACKGROUND_M = 3.0       # a hero the shot sets in the background stands this much further from the camera
ASSETS = {"cat": "animal.cat", "woman": "person.woman", "man": "person.man"}


class SceneShot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    duration: float = Field(ge=1, le=15.08)
    framing: str = "medium"
    angle: str = "eye"
    lens_mm: float = Field(default=35, ge=8, le=600)
    camera_motion: str = "static"
    characters: list[str] = Field(default_factory=list, max_length=8)
    # who the shot text puts behind (ALEX in the kitchen behind NOVA): judged there, not on the shared floor line
    background: list[str] = Field(default_factory=list, max_length=8)


class SceneCheck(BaseModel):
    place: str = Field(default="", max_length=2000)  # the scene's place in words (the shots' «is the environment:» line)
    cast: list[dict] = Field(default_factory=list, max_length=40)  # the passport cast (scene-kit), for real scale
    aspect: str = Field(default="9:16", pattern=ASPECT_PATTERN)
    shots: list[SceneShot] = Field(min_length=1, max_length=24)


def body_of(member: dict) -> str:
    """cat / woman / man from the passport's own words for the hero's scale."""
    # Only the hero's own first words: ALEX's sheet also mentions NOVA («NOVA's whole body reaches only his shin»).
    sheet = member.get("sheet") or {}
    own = str(sheet.get("scale") if isinstance(sheet, dict) else "") or str(member.get("line") or "")
    text = " " + own.split(".")[0].split(";")[0].split(":")[0].lower()
    if " cat" in text or "kitten" in text or "кот" in text:
        return "cat"
    if "woman" in text or "girl" in text or "craftswoman" in text:
        return "woman"
    return "man"


def cast_bodies(cast: list[dict], names: list[str]) -> dict[str, str]:
    known = {}
    for member in cast or []:
        for alias in [member.get("name", ""), *(member.get("aliases") or [])]:
            known[str(alias).strip().lower()] = body_of(member)
    return {name: known.get(name.lower(), "man") for name in names}


def floor(names: list[str]) -> dict[str, dict]:
    """Heroes in one line facing their partner and the camera side; plan metres (x right, y away)."""
    n = len(names)
    spots = {}
    for i, name in enumerate(names):
        x = (i - (n - 1) / 2) * SPACING_M
        facing = 180 if n == 1 else 135 if x < 0 else 225 if x > 0 else 180
        spots[name] = {"x": round(x, 3), "y": FLOOR_Y, "facing": facing}
    return spots


def camera_for(shot: SceneShot, spots: dict, bodies: dict) -> tuple[list[dict], list[str]]:
    """Camera marks (plan metres, height, pan, tilt, focal) for one shot, and what does not add up."""
    notes = []
    names = [c for c in shot.characters if c in spots] or list(spots)
    subject = names[0]
    height = HEIGHTS[bodies[subject]]
    eye = height * (0.72 if bodies[subject] == "cat" else 0.93)
    # A card that names several heroes in a shot no tighter than medium-full wants them together: aim between them.
    two = len(names) > 1 and shot.framing in {"two_shot", "extreme_wide", "wide", "full", "medium_full"}
    tx = sum(spots[n]["x"] for n in names[:2]) / 2 if two else spots[subject]["x"]
    ty = spots[subject]["y"]
    seen_h = max(height, max(HEIGHTS[bodies[n]] for n in names[:2]) if two else height) / FRAME_RATIO.get(shot.framing, 1.6)
    distance = seen_h * shot.lens_mm / SENSOR_H_MM
    if distance < MIN_DISTANCE_M:
        notes.append(f"камера в {distance * 100:.0f} см от {subject}: на {shot.lens_mm:g} мм такой план не снять — нужен длиннее объектив")
        distance = MIN_DISTANCE_M
    aim = eye if seen_h < height * 0.9 else min(eye, max(seen_h / 2, height / 2))
    cam_h = {"eye": eye, "low": max(0.08, eye * 0.35), "worm": 0.05, "high": eye + (0.6 if bodies[subject] == "cat" else 1.0),
             "overhead": eye + 2.5, "dutch": eye}.get(shot.angle, eye)
    if shot.angle == "dutch":
        notes.append("голландский наклон Blockout не показывает — камера ровная")

    def mark(t: float, back: float = 0.0, side: float = 0.0, lift: float = 0.0, orbit: float = 0.0, pan: float = 0.0,
             tilt: float = 0.0, focal: float | None = None) -> dict:
        d = distance + back
        angle = math.radians(orbit)
        x, y = tx - math.sin(angle) * d + side, ty - math.cos(angle) * d
        h = cam_h + lift
        look = math.degrees(math.atan2(tx - x, ty - y))  # the camera keeps the subject in frame as it moves
        reach = math.hypot(tx - x, ty - y)
        return {"x": round(x, 3), "plan_y": round(y, 3), "y": round(h, 3), "time": round(t, 3),
                "panDeg": round(look + pan, 2), "tiltDeg": round(math.degrees(math.atan2(aim - h, reach)) + tilt, 2),
                "focalLength": round(focal or shot.lens_mm, 1)}

    end = shot.duration
    move = shot.camera_motion
    if move == "dolly":
        marks = [mark(0, back=distance * 0.35), mark(end)]
    elif move in {"tracking", "truck", "slider"}:
        marks = [mark(0, side=-distance * 0.25), mark(end, side=distance * 0.25)]
    elif move == "orbit":
        marks = [mark(0, orbit=-15), mark(end, orbit=15)]
    elif move == "pan":
        marks = [mark(0, pan=-12), mark(end, pan=12)]
    elif move == "tilt":
        marks = [mark(0, tilt=-8), mark(end, tilt=8)]
    elif move in {"crane", "pedestal"}:
        marks = [mark(0), mark(end, lift=max(0.3, seen_h * 0.6))]
    elif move == "zoom":
        marks = [mark(0, focal=shot.lens_mm * 0.7), mark(end)]
    else:
        if move == "handheld":
            notes.append("ручная камера показана как статика")
        marks = [mark(0), mark(end)]
    return marks, notes


def set_pieces(place: str) -> list[dict]:
    import re
    text = place.lower()
    pieces, taken = [], set()
    for words, items in SET:
        if re.search(rf"\b({words})", text):
            for asset, x in items:
                if (asset, x) not in taken:
                    taken.add((asset, x))
                    pieces.append({"asset": asset, "x": x, "y": SET_Y})
    return pieces


def sees(mark: dict, aspect: str, hero: dict, height: float, size: tuple[float, float]) -> dict:
    """What one camera mark sees of one hero: distance, horizontal and vertical share of the body in frame."""
    half_h = SENSOR_H_MM / (2 * mark["focalLength"])
    half_w = half_h * ASPECTS.get(aspect, 16 / 9)
    pan = math.radians(mark["panDeg"])
    fx, fy = math.sin(pan), math.cos(pan)
    vx, vy = hero["x"] - mark["x"], hero["y"] - mark["plan_y"]
    depth = vx * fx + vy * fy
    if depth <= 0.05:
        return {"depth": depth, "x": 0.0, "y": 0.0}
    cx = (vx * fy - vy * fx) / depth / half_w
    half = size[0] / 2 / depth / half_w
    across = max(0.0, min(cx + half, 1) - max(cx - half, -1)) / (2 * half)
    tilt = math.radians(mark["tiltDeg"])
    top = math.tan(math.atan2(height - mark["y"], depth) - tilt) / half_h
    bottom = math.tan(math.atan2(-mark["y"], depth) - tilt) / half_h
    up = max(0.0, min(top, 1) - max(bottom, -1)) / max(1e-6, top - bottom)
    return {"depth": depth, "x": across, "y": up}


def findings(shot: SceneShot, marks: list[dict], heroes: dict, bodies: dict, aspect: str) -> list[str]:
    """Plain words for what the frame will get wrong: a camera inside a hero, a named hero out of or cut by the frame."""
    out = []
    names = [c for c in shot.characters if c in heroes]
    for name in names:
        worst = None
        for when, mark in (("в начале", marks[0]), ("в конце", marks[-1])):
            seen = sees(mark, aspect, heroes[name], HEIGHTS[bodies[name]], BODY[bodies[name]])
            if seen["depth"] < BODY[bodies[name]][1] / 2 + 0.25:
                worst = worst or f"камера вплотную к {name} ({max(0, seen['depth']) * 100:.0f} см) {when} — он закроет весь кадр"
            elif seen["x"] < 0.05 or seen["y"] < 0.05:
                worst = worst or f"{name} не в кадре {when}, хотя шот его называет"
            elif seen["x"] < 0.6 and shot.framing in {"extreme_wide", "wide", "full", "medium_full", "two_shot"}:
                worst = worst or f"{name} обрезан краем кадра {when}"
        if worst:
            out.append(worst)
    return out


def plan_scene(check: SceneCheck, cast: list[dict]) -> dict:
    names = []
    for shot in check.shots:
        for name in shot.characters:
            if name not in names:
                names.append(name)
    if not names:
        raise HTTPException(422, "В шотах не названы герои — ставить в Blockout некого")
    bodies = cast_bodies(cast, names)
    spots = floor(names)
    shots = []
    start = 0.0
    for i, shot in enumerate(check.shots, 1):
        marks, notes = camera_for(shot, spots, bodies)
        seen = {n: {**spot, "y": spot["y"] + BACKGROUND_M} if n in shot.background else spot for n, spot in spots.items()}
        notes = findings(shot, marks, seen, bodies, check.aspect) + notes
        shots.append({"n": i, "start": round(start, 3), "duration": shot.duration, "marks": marks, "notes": notes,
                      "label": f"{shot.framing} · {shot.angle} · {shot.lens_mm:g} мм · {shot.camera_motion}"})
        start += shot.duration
    return {"heroes": [{"name": n, "body": bodies[n], "asset": ASSETS[bodies[n]], **spots[n]} for n in names],
            "shots": shots, "aspect": check.aspect, "set": set_pieces(check.place)}


def scene_place(cuts: list[dict]) -> str:
    """The scene's place in words: each shot's «is the environment:» line, once (story-canvas-blockout scenePlace)."""
    lines = [m.group(1).strip() for c in cuts if (m := re.search(r"is the environment:\s*([^\n]+)", c.get("prompt") or "", re.I))]
    return " ".join(dict.fromkeys(lines))[:2000]


def check_from_scene(scene: dict, aspect: str, cast: list[dict]) -> SceneCheck:
    """The check of a saved scene, read the way the canvas reads it (story-canvas-blockout checkShots)."""
    refs = (scene.get("referenceState") or {}).get("references", scene.get("references", []))
    heroes = list(dict.fromkeys(r["owner"] for r in refs if r.get("owner")))
    shots = []
    for cut in scene.get("cuts") or []:
        card = cut.get("sceneShot") or {}
        named = [h for h in heroes if re.search(rf"\b{re.escape(h)}\b", cut.get("prompt") or "", re.I)]
        back = [h for h in heroes if re.search(rf"\b{re.escape(h)} is screen (?:left|right|centre|center) background\b", cut.get("prompt") or "")]
        shots.append(SceneShot(background=back, duration=min(15, max(1, float(cut.get("duration") or 5))), framing=card.get("framing") or "medium",
                               angle=card.get("angle") or "eye", lens_mm=float(card.get("lens_mm") or 35),
                               camera_motion=card.get("camera_motion") or "static",
                               characters=(card.get("characters") or named)[:8]))
    if not shots:
        raise HTTPException(422, "В сцене нет шотов")
    return SceneCheck(place=scene_place(scene.get("cuts") or []), cast=cast, aspect=aspect if aspect in ASPECTS else "9:16", shots=shots)


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/previs/scene")
    folder = store.assets_dir.parent / "previs-scene"

    async def png() -> bytes:
        try:
            data = base64.b64decode((await previs.call("screenshot")).get("imageBase64", ""), validate=True)
        except (binascii.Error, ValueError):
            data = b""
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(502, "Blockout не отдал PNG кадра")
        return data

    async def ready() -> None:
        """Opens Blockout when it is installed but not running, and waits for its control port."""
        if previs.control():
            try:
                await previs.call("get_state")
                return
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
        if not previs.APP.is_dir():
            raise HTTPException(503, "Blockout не установлен (~/Applications/Blockout.app)")
        await asyncio.to_thread(subprocess.run, ["open", str(previs.APP)], capture_output=True, timeout=30)
        for _ in range(40):
            await asyncio.sleep(0.5)
            if previs.control():
                try:
                    await previs.call("get_state")
                    return
                except HTTPException as exc:
                    if exc.status_code != 503:
                        raise
        raise HTTPException(503, previs.NOT_RUNNING)

    async def stage(plan: dict) -> None:
        """Clears what an earlier check placed (remembered by entity id) and stands this scene's heroes and set."""
        memo = folder / "staged.json"
        try:
            ours = set(json.loads(memo.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            ours = set()
        state = await previs.call("get_state")
        for entity in state.get("scene", {}).get("entities") or []:
            if entity.get("id") in ours:
                await previs.call("delete_entity", {"entityId": entity["id"]})
        placed = []
        for hero in plan["heroes"]:  # no label: a name tag fills the frame in close shots
            placed.append(await previs.call("add_entity", {"assetId": hero["asset"], "x": hero["x"], "z": -hero["y"], "rotationDeg": hero["facing"]}))
        for piece in plan["set"]:
            placed.append(await previs.call("add_entity", {"assetId": piece["asset"], "x": piece["x"], "z": -piece["y"], "rotationDeg": 180}))
        folder.mkdir(exist_ok=True)
        memo.write_text(json.dumps([str(p.get("id") or p.get("entityId") or "") for p in placed]), encoding="utf-8")

    @router.get("/plan/{scene_id}")
    def plan(scene_id: str) -> dict:
        """The staging check without Blockout: the same geometry, words only. The agent runs it before a build."""
        project, revision = store.load_draft_snapshot()
        scene = next((s for s in (project or {}).get("scenes", []) if s.get("id") == scene_id), None)
        if not scene:
            raise HTTPException(404, "Сцена не найдена в сохранённом черновике")
        fid = (scene.get("franchiseSource") or {}).get("franchise_id") or \
            ((scene.get("sceneDirector") or {}).get("agentScene") or {}).get("franchise_id") or \
            next((((c.get("productionSpec") or {}).get("franchise_id")) for c in scene.get("cuts") or [] if (c.get("productionSpec") or {}).get("franchise_id")), "")
        cast = get_scene_kit(store, fid).get("cast", []) if fid else []
        result = plan_scene(check_from_scene(scene, scene.get("aspect") or (project or {}).get("aspect") or "9:16", cast), cast)
        problems = [f"Шот {shot['n']}: {note}" for shot in result["shots"] for note in shot["notes"]]
        return {"scene_id": scene_id, "revision": revision, "ok": not problems, "problems": problems,
                "heroes": [{"name": h["name"], "body": h["body"]} for h in result["heroes"]],
                "shots": [{"n": shot["n"], "label": shot["label"], "notes": shot["notes"]} for shot in result["shots"]]}

    @router.post("/check")
    async def check(payload: SceneCheck) -> dict:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise HTTPException(503, "Для ролика превиза нужен ffmpeg на Mac")
        plan = plan_scene(payload, payload.cast)
        await ready()
        await stage(plan)
        key = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:16]
        out = folder / f"{key}.mp4"
        with tempfile.TemporaryDirectory(prefix="h3-previs-scene-") as tmp:
            parts = []
            try:
                for shot in plan["shots"]:
                    await previs.call("set_shot", {"name": f"H3 {shot['n']:02d}", "duration": shot["duration"],
                                                   # blockout knows 16:9 / 9:16; other GPU formats are cut from it by previs.crop
                                                   "aspect": "9:16" if ASPECTS[payload.aspect] < 1 else "16:9", "fps": 24})
                    await previs.call("clear_camera_marks")
                    for m in shot["marks"]:
                        await previs.call("add_camera_mark", {"x": m["x"], "y": m["y"], "z": -m["plan_y"], "panDeg": m["panDeg"],
                                                              "tiltDeg": m["tiltDeg"], "time": m["time"], "focalLength": m["focalLength"]})
                    frames = Path(tmp) / f"s{shot['n']:02d}"
                    frames.mkdir()
                    count = int(shot["duration"] * FPS) + 1
                    for i in range(count):
                        await previs.call("set_time", {"t": min(i / FPS, shot["duration"])})
                        (frames / f"f{i:04d}.png").write_bytes(previs.crop(await png(), payload.aspect))
                    shutil.copyfile(frames / f"f{count // 2:04d}.png", folder / f"{key}-{shot['n']}.png")
                    shot["still"] = f"/api/previs/scene/{key}-{shot['n']}.png"
                    part = Path(tmp) / f"s{shot['n']:02d}.mp4"
                    result = await asyncio.to_thread(subprocess.run, [
                        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "f%04d.png"),
                        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2:flags=lanczos,fps=24,format=yuv420p",
                        "-t", f"{shot['duration']:.3f}", "-c:v", "libx264", "-crf", "20", "-an", str(part)],
                        capture_output=True, timeout=300)
                    if result.returncode:
                        raise HTTPException(500, "Шот превиза не собран: " + result.stderr.decode(errors="replace")[-300:])
                    parts.append(part)
            finally:
                await previs.call("set_time", {"t": 0})
            listing = Path(tmp) / "parts.txt"
            listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
            result = await asyncio.to_thread(subprocess.run, [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c", "copy", str(out)], capture_output=True, timeout=300)
            if result.returncode or not out.is_file():
                raise HTTPException(500, "Ролик превиза не склеен: " + result.stderr.decode(errors="replace")[-300:])
        return {"reel": key, "url": f"/api/previs/scene/{key}.mp4", "shots": plan["shots"], "heroes": plan["heroes"]}

    @router.get("/{name}.png")
    async def still(name: str) -> FileResponse:
        path = folder / f"{name}.png"
        if not name.replace("-", "").isalnum() or not path.is_file():
            raise HTTPException(404, "Кадр превиза не найден")
        return FileResponse(path, media_type="image/png")

    @router.get("/{key}.mp4")
    async def reel(key: str) -> FileResponse:
        path = folder / f"{key}.mp4"
        if not key.isalnum() or not path.is_file():
            raise HTTPException(404, "Ролик превиза не найден")
        return FileResponse(path, media_type="video/mp4")

    return router
