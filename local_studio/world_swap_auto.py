"""«Смена мира по видео» built by Studio, not by an agent's hands.

World swaps assembled by hand drift: v1.5/reskin adapters, the whole episode in a part's summary, parts cut on round
seconds, no «Кадр сцены» per shot, costume and room changing between parts. Gates refuse such jobs; this module
makes the right one, so there is nothing to refuse:

1. The source is split on its own measured cuts (video_swap.split_parts); ONE part is built per call.
2. The brain sees only that part's frames and writes one action line per shot (no other part's story can get in).
3. Codex draws a «Кадр сцены» for every shot from the hero face + full body, the franchise place and the source still;
   Claude accepts it next to those references and the previous shot's frame (scene_frames.draw/review, 3 tries).
4. Settings are the built-in world-swap defaults (mode, adapter, steps, cache, optics); guide_frames sit on the
   measured cuts and are checked with guide_cut_gate before any picture is drawn.
5. enqueue=true sends only this part; the next part is built after the user or the take check accepted this one.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import agent_brain, guide_cut_gate, scene_frames, scene_kit, shot_contract, video_swap
from app.contract import ASPECTS, resolution

from .franchise_profile import MAX_IMAGES, translit
from .reference_segments import SegmentRequest, reference_segment
from .store import _media_duration_seconds
from .video_swap_auto import (AutoRuns, SHOT_SCHEMA, shot_lines, describe_prompt, extract_frames, frame_times, short_performer,
                              shot_spans)

FPS = 24
FRAME_SIZE = "768x1344"
FRAME_TRIES = 3
# Built-in world-swap settings that belong to the method; the rest (duration, guides, seed, frame size) belong to each
# part. Deep focus at f/8 keeps fur, hair and small props sharp; the safe cache keeps fine detail.
DEFAULT_SETTINGS = {"mode": "animation3d", "style_adapter": "style3d", "megapixels": 0.8, "steps": 24,
                    "cache_mode": "safe", "ref_image_size": "max", "profile": "bf16",
                    "director_config": {"focus": {"id": "focus.sharp_deep", "parameters": {
                                            "behavior": "tracking", "depth": "deep", "target": "subject"}},
                                        "lens": {"id": "lens.sharp_deep", "parameters": {"aperture_f": 8}}},
                    "keep_source_audio": True, "prompt_format": "h3_ir"}
GARMENT_RULE = ("Putting on, taking off or handling a garment IS an action: name the garment and its colour "
                "(\"pulls a blue sweater over his head\"). Describe only what is visible in these frames; never "
                "what happens before or after this part. Name every prop a performer holds, balances or lifts and "
                "what they do with it (\"balances a pink ball of yarn on his nose\"); a prop left out of the action "
                "is left out of the video.")


class Pair(BaseModel):
    hero: str = Field(min_length=1, max_length=80)
    performer: str = Field(min_length=3, max_length=300)


class WorldSwapInput(BaseModel):
    asset_id: str = Field(min_length=1, max_length=100, description="the source video in the media library")
    franchise_id: str = Field(min_length=1, max_length=100)
    pairs: list[Pair] = Field(min_length=1, max_length=2)
    environment_asset_id: str = Field(min_length=1, max_length=100, description="the franchise place («Окружение»)")
    part: int = Field(1, ge=1, le=30, description="which part to build; parts after 1 only after part 1 was accepted")
    provider: str = Field(default="claude", pattern="^(claude|codex)$")
    enqueue: bool = False


def recipe_settings() -> tuple[dict, str]:
    """The world-swap settings and their origin: built-in defaults (a fresh Studio has no accepted recipe)."""
    return json.loads(json.dumps(DEFAULT_SETTINGS)), "default"


def part_shots(duration: float, cuts: list[float]) -> list[tuple[float, float]]:
    """The part's shots on the same measured cuts guide_cut_gate checks; slivers under 1 s join a neighbour."""
    return shot_spans({"duration": duration, "source_cuts": cuts})


def guides_for(spans: list[tuple[float, float]], first_picture: int) -> list[list[int]]:
    return [[first_picture + n, 0 if n == 0 else round(a * FPS)] for n, (a, _) in enumerate(spans)]


KEY_STEP_S = 2.0  # a scene frame inside a long shot every ~2 s


def frame_keys(spans: list[tuple[float, float]], cuts: list[float], room: int) -> list[tuple[int, float]]:
    """(shot, time) of every «Кадр сцены»: each shot's start, plus anchors every KEY_STEP_S inside a long shot, never
    within 1 s before a measured cut or the shot's end, at most `room` in all (starts first).

    With a frame only at the start of a long following shot H3 keeps the mapping for about 2 s, then cuts to an
    invented wide shot and brings back the source performer; a frame inside a sharp pan or orbit holds it."""
    starts = []
    for n, (a, b) in enumerate(spans, 1):
        # A shot that absorbed a sub-second sliver (a short insert before a close-up) anchors on its longest piece: the frame must show what the shot's action describes.
        edges = [a, *[c for c in cuts if a + 0.05 < c < b - 0.05], b]
        pieces = list(zip(edges, edges[1:]))
        main = max(pieces, key=lambda p: p[1] - p[0])
        starts.append((n, round(main[0], 3)))
    extra = []
    for n, (a, b) in enumerate(spans, 1):
        t = a + KEY_STEP_S
        while t <= b - 1.0:
            if not any(t < c <= t + 1.0 for c in cuts):
                extra.append((n, round(t, 3)))
            t += KEY_STEP_S
    return sorted(starts + extra[:max(0, room - len(starts))], key=lambda k: k[1])


def guides_for_keys(keys: list[tuple[int, float]], first_picture: int) -> list[list[int]]:
    return [[first_picture + i, 0 if t <= 0 else round(t * FPS)] for i, (_, t) in enumerate(keys)]


def source_aspect(video: Path) -> str:
    """The job frame of the reference's own format : a 4:3
    source renders 4:3 instead of being cropped (heroes fell out) or extended (invented furniture)."""
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(video)], capture_output=True, text=True, timeout=30, check=True)
    width, height = (int(v) for v in probe.stdout.strip().split(",")[:2])
    return min(ASPECTS, key=lambda name: abs(ASPECTS[name] - width / height))


def portrait_still(video: Path, t: float, out: Path, size: str) -> str:
    """The source frame at full resolution, scaled to the job frame of the same format (never cropped or extended)."""
    width, height = size.split("x")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={width}:{height}", str(out)], check=True, timeout=60, capture_output=True)
    return str(out)


def frame_text(kit: dict, heroes: list[dict], n: int, span: tuple[float, float], action: str) -> str:
    looks = " ".join(f"{h['name']}: {shot_contract._kit_words(scene_kit.sheet_words(kit, h['name'], scene_kit.LOOK_KEYS))}"
                     for h in heroes)
    return (f"FIRST FRAME of shot {n} ({span[0]:.2f}-{span[1]:.2f} s of the part): {action}\n"
            f"Heroes, copied from their own face and full-body pictures: {looks}\n"
            "Camera, framing, poses and action come from the source still: each hero stands on the same side of the "
            "screen and turns the head and body the same way as the performer they replace (a performer turned "
            "three-quarters to screen-left stays turned to screen-left, never squared to the camera) and keeps the "
            "performer's exact body position — lying stays lying, sitting stays sitting, head and feet at the same "
            "ends (a frame whose hero pose differs from the source still is rejected); every prop a performer holds, "
            "balances or lifts in the source still (a barbell, a ball of yarn, a phone) is in the hero's paws or "
            "hands the same way; "
            "the room keeps the source's layout — the same walls, windows, doors, shelves and furniture in the same "
            "places, and the same things behind each hero — and only its look (colours, materials, decor and style) "
            "comes from the franchise place picture; never invent or rearrange the room. Time of day and where the light comes from (night or day, which lamps and windows) follow the "
            "source still; the colour, softness and mood of the light are the franchise's own look, the same in every "
            "shot of this part. Each hero keeps their own real size from "
            "their pictures and passport, relative to the other hero and the furniture — the size of the source "
            "performer in the frame never sets it. A picture inside the frame (phone or TV screen, poster, photo) "
            "shows what the source shows there — a different animal or person, never a copy of a hero. "
            "The source performers become the heroes: "
            + "; ".join(f"{h['performer']} becomes {h['name']}" for h in heroes) + ".")


def compose(kit: dict, heroes: list[dict], frames_at: int, spans: list[tuple[float, float]], actions: dict[int, str],
            index: int, total: int, keys: list[tuple[int, float]] | None = None) -> str:
    names = " and ".join(h["name"] for h in heroes)
    subjects = []
    for at, h in enumerate(heroes, 1):
        look = shot_contract._kit_words(scene_kit.sheet_words(kit, h["name"], scene_kit.LOOK_KEYS))
        subjects.append(f"<Subject {at}> is {h['name']}: face from <Picture {h['face_number']}>, full body and outfit "
                        f"from <Picture {h['body_number']}>. {look} {h['name']} replaces {h['performer']} of <Video 1> "
                        "and takes only actions, expressions and timing from it.")
    keys = keys or [(n, a) for n, (a, _) in enumerate(spans, 1)]
    starts = {next(t for m, t in keys if m == n) for n in {m for m, _ in keys}}
    frames = [f"<Picture {frames_at + i}> is the "
              + (f"first frame of [Shot {n}]" if t in starts else f"frame of [Shot {n}] at {t:.2f} s")
              + ": its set, light, heroes and framing." for i, (n, t) in enumerate(keys)]
    shots = shot_lines(spans, actions)
    guard = (f"{video_swap.LEAK_GUARD_MARKER}: Only {names} appear, each exactly once; no other person or animal and "
             "none of the source performers. Every wall, piece of furniture and light comes from the scene frames, "
             "none from <Video 1>. The [Shot N] actions are mandatory, in order and on their seconds. "
             "No on-screen text, captions, logos or watermarks.")
    return "\n".join([
        "subject_definitions:", *subjects, *frames,
        "<Video 1> provides only the motion, camera, shot order and timing, and the synchronized soundtrack.", "",
        "summary:", f"[video editing] Part {index} of {total}. {names} in the franchise world of the scene frames "
        f"{'repeats' if len(heroes) == 1 else 'repeat'} the actions, camera and timing of <Video 1>.", "",
        "retention_analysis:",
        *[f"<Subject {at}>: {h['name']}'s face, proportions and outfit only from its pictures, the same in every shot."
          for at, h in enumerate(heroes, 1)],
        "Each shot keeps the set and light of its own scene frame.", "",
        "detailed_description:", *shots, "", guard, "",
        "overall_soundscape:", "Reuse the synchronized original soundtrack of <Video 1>.", "",
        "non_diegetic_music:", "Reuse the original music of <Video 1>."])


def hero_pictures(store, payload: WorldSwapInput) -> list[dict]:
    names = [p.hero for p in payload.pairs]
    found, environment, problems = video_swap.api_pictures(store, payload.franchise_id, names, payload.environment_asset_id)
    if problems:
        raise ValueError("; ".join(problems))
    performers = {translit(p.hero): short_performer(p.performer) for p in payload.pairs}
    return [{"name": found[n]["name"], "performer": performers[translit(n)], "face": found[n]["face"],
             "body": found[n]["picture"]} for n in names]


async def build(store, app, run: dict, payload: WorldSwapInput, save) -> None:
    def step(**change):
        run.update(change)
        save(run)

    try:
        asset = store.get_asset(payload.asset_id)
        if not asset or asset.get("kind") != "video":
            raise ValueError("Исходное видео не найдено в медиатеке")
        source = Path(asset["path"])
        duration = _media_duration_seconds(source)
        if not duration:
            raise ValueError("Не удалось прочитать длительность исходника")
        card = video_swap.end_card_start(source, duration)
        duration = card or duration
        parts = video_swap.split_parts(duration, video_swap.source_cuts(source, duration))
        if payload.part > len(parts):
            raise ValueError(f"В исходнике {len(parts)} частей, части {payload.part} нет")
        part = parts[payload.part - 1]
        heroes = await asyncio.to_thread(hero_pictures, store, payload)
        if 2 * len(heroes) >= MAX_IMAGES:
            raise ValueError("Слишком много героев для кадров сцены")
        settings, recipe_job = recipe_settings()
        aspect = await asyncio.to_thread(source_aspect, source)
        width, height = resolution(aspect, "custom", float(settings.get("megapixels") or 0.8))
        settings.update(aspect_ratio=aspect, width=width, height=height)
        frame_size = FRAME_SIZE if aspect == "9:16" else f"{width}x{height}"
        segment = await asyncio.to_thread(reference_segment, store, payload.asset_id,
                                          SegmentRequest(start=part["start"], duration=part["duration"]))
        video = Path(store.get_asset(segment["asset_id"])["path"])
        cuts = await asyncio.to_thread(guide_cut_gate.measured_cuts, video, float(part["duration"]))
        spans = part_shots(float(part["duration"]), cuts)
        frames_at = 2 * len(heroes) + 1
        if len(spans) > MAX_IMAGES - 2 * len(heroes):
            raise ValueError(f"В части {len(spans)} шотов — больше, чем мест под «Кадр сцены» "
                             f"({MAX_IMAGES - 2 * len(heroes)}); раздели исходник короче")
        keys = frame_keys(spans, cuts, MAX_IMAGES - 2 * len(heroes))
        guides = guides_for_keys(keys, frames_at)
        problems = guide_cut_gate.guide_cut_problems(guides, cuts, float(part["duration"]))
        if problems:
            raise ValueError("Шоты части не ложатся на склейки исходника: " + "; ".join(problems))
        step(state="describing", recipe_job=recipe_job, total_parts=len(parts), part=payload.part,
             segment_asset_id=segment["asset_id"], start=part["start"], duration=part["duration"],
             glue_trim_frames=part["trim_frames"], glue_tail_frames=part["tail_frames"],
             shots=[{"start": a, "end": b} for a, b in spans])

        # 2. Only this part's frames: the brain cannot write another part's story.
        label = f"часть {payload.part}/{len(parts)}"
        roles = [{"name": h["name"], "performer": h["performer"]} for h in heroes]
        with tempfile.TemporaryDirectory(prefix="h3-world-") as tmp:
            picks = frame_times(spans)
            stills = await asyncio.to_thread(extract_frames, video, picks, Path(tmp))
            answer = await agent_brain.ask(agent_brain.BrainRequest(
                provider=payload.provider, schema=SHOT_SCHEMA, read_repo=False, images=stills, timeout_s=600,
                prompt=describe_prompt(roles, spans, picks, label, stills, payload.franchise_id) + "\n" + GARMENT_RULE))
        actions = {int(s["index"]): str(s["action"]) for s in (answer.get("data") or {}).get("shots") or []
                   if s.get("action")}
        missing = [n for n in range(1, len(spans) + 1) if n not in actions]
        if missing:
            raise ValueError(f"{label}: агент не описал шоты {missing}")
        for n, shot in enumerate(run["shots"], 1):
            shot["action"] = actions[n]
        step(state="drawing")

        # 3. A «Кадр сцены» per shot, accepted by Claude next to the references and the shot before it.
        kit = await asyncio.to_thread(scene_kit.get_scene_kit, store, payload.franchise_id)
        refs = [r for h in heroes for r in ({"role": "Лицо героя", "owner": h["name"], "id": h["face"]},
                                             {"role": "Персонаж целиком", "owner": h["name"], "id": h["body"]})]
        refs.append({"role": "Окружение", "owner": "", "id": payload.environment_asset_id})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", timeout=120) as client:
            previous = None
            with tempfile.TemporaryDirectory(prefix="h3-world-frames-") as tmp:
                folder = Path(tmp)
                key_frames = []
                for k, (n, t) in enumerate(keys, 1):
                    a, b = spans[n - 1]
                    still = await asyncio.to_thread(portrait_still, video, round(t + 0.08, 2), folder / f"still{k}.png",
                                                    frame_size)
                    references = [*({k: r[k] for k in ("role", "owner")} for r in refs),
                                  {"role": "Кадр исходника", "owner": ""}]
                    pictures = [Path(store.get_asset(r["id"])["path"]) for r in refs] + [Path(still)]
                    text = frame_text(kit, heroes, n, (a, b), actions[n])
                    if t > a + 0.01:
                        text += (f"\nThis frame is the moment {t:.2f} s inside shot {n}, not its start: draw exactly "
                                 "what the source still shows at this moment — camera, framing and poses.")
                    if previous:
                        # The review compares with the accepted previous frame; the drawer sees it for the look. The
                        # layout comes from the source still: the franchise replaces the surroundings, the room's
                        # layout is not rebuilt.
                        references.append({"role": "Кадр сцены", "owner": "accepted previous frame"})
                        pictures.append(previous["frame"])
                        text += ("\nThe accepted frame of the previous shot is attached: keep its colours, materials, "
                                 "decor and light; the layout of this frame still follows this shot's source still. "
                                 "Add no object that is in neither the source still nor the previous frame (no new "
                                 "pillows, plants, lamps or props); the same props keep the same count and places. "
                                 "A hero's body position (lying or sitting, where the head and feet are) continues "
                                 "from the previous frame exactly as the source performer's does.")
                    feedback, verdict, frame_id = "", None, None
                    for attempt in range(1, FRAME_TRIES + 1):
                        edit = {"edit_image": references.index({"role": "Кадр исходника", "owner": ""}) + 1}
                        data = await scene_frames.draw({"prompt": text, "size": frame_size, **edit}, references,
                                                       pictures, feedback)
                        if await asyncio.to_thread(scene_frames.split_panel, data):
                            feedback = "the picture is two panels side by side; draw one single frame"
                            continue
                        response = await client.post("/api/assets", params={"role": "Кадр сцены"},
                                                     files={"file": (f"world-frame{k}.png", data, "image/png")})
                        if response.status_code >= 400:
                            raise ValueError(f"кадр шота {n} не сохранён: HTTP {response.status_code}")
                        frame_id = response.json()["asset_id"]
                        frame = folder / f"frame{k}-{attempt}.png"
                        frame.write_bytes(data)
                        verdict = await scene_frames.review(frame, text, references, pictures, previous)
                        run["shots"][n - 1].setdefault("attempts", []).append(
                            {"asset_id": frame_id, "accept": verdict["accept"], "problems": verdict["problems"]})
                        step()
                        if verdict["accept"]:
                            break
                        feedback = verdict["problems"]
                    if not verdict or not verdict["accept"]:
                        raise ValueError(f"{label}: кадр шота {n} ({t:.2f} с) не принят за {FRAME_TRIES} попытки — "
                                         f"{(verdict or {}).get('problems') or feedback}; на GPU ничего не ушло")
                    run["shots"][n - 1].setdefault("frame_asset_id", frame_id)
                    key_frames.append(frame_id)
                    run["frames"] = [{"shot": m, "t": tt, "asset_id": fid}
                                     for (m, tt), fid in zip(keys, key_frames)]
                    previous = {"frame": frame, "text": text}
                    step()

            # 4. The job: built-in settings, guides on the measured cuts.
            pictures = [i for h in heroes for i in (h["face"], h["body"])] + key_frames
            numbers = {asset_id: n for n, asset_id in enumerate(pictures, 1)}
            roles_by_id = {}
            for at, h in enumerate(heroes):
                h["face_number"], h["body_number"] = 2 * at + 1, 2 * at + 2
                roles_by_id[h["face"]], roles_by_id[h["body"]] = "Лицо героя", "Персонаж целиком"
            for fid in key_frames:
                roles_by_id[fid] = "Кадр сцены"
            roles_by_id[segment["asset_id"]] = "Движение и камера"
            numbers[segment["asset_id"]] = 1
            prompt = compose(kit, heroes, frames_at, spans, actions, payload.part, len(parts), keys)
            body = {"client_request_id": f"world-swap-{run['id'][:12]}-{payload.part}",
                    "asset_ids": [*pictures, segment["asset_id"]], "reference_roles": roles_by_id,
                    "reference_owners": {i: h["name"] for h in heroes for i in (h["face"], h["body"])},
                    "reference_numbers": numbers,
                    "reference_performers": {segment["asset_id"]: {
                        "pairs": [{"owner": h["name"], "performer": h["performer"]} for h in heroes],
                        "keep_others": False}},
                    "settings": {**settings, "duration": part["duration"], "guide_frames": guides},
                    "prompt": prompt}
            step(state="ready", job=body)
            if payload.enqueue:
                response = await client.post("/api/jobs", json=body)
                if response.status_code >= 400:
                    raise ValueError(f"Studio не приняла задание: {response.text[:400]}")
                step(state="queued", job_id=response.json()["job_id"])
    except (ValueError, OSError, subprocess.SubprocessError, agent_brain.BrainError, RuntimeError,
            asyncio.TimeoutError) as exc:
        step(state="failed", error=str(exc)[:800])


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    runs = AutoRuns(ctx.store.root / "world-swap-auto")
    tasks: set[asyncio.Task] = set()

    @router.post("/api/world-swap/auto")
    async def start(payload: WorldSwapInput, request: Request) -> dict:
        run = runs.save({"id": uuid.uuid4().hex, "state": "planning", "asset_id": payload.asset_id,
                         "franchise_id": payload.franchise_id, "environment_asset_id": payload.environment_asset_id,
                         "part": payload.part, "enqueue": payload.enqueue, "shots": [],
                         "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        task = asyncio.create_task(build(ctx.store, request.app, run, payload, runs.save))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return run

    @router.get("/api/world-swap/auto/{run_id}")
    async def status(run_id: str) -> dict:
        return runs.load(run_id)

    return router
