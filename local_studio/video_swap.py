"""«Замена по видео»: one source video + a franchise + «who becomes whom» → a plan of ≤10 s parts.

The plan covers the WHOLE source (skills/minimax-video-production: never part 1 presented as the film): parts break
on the source's own cuts where they can, each part is an immutable library excerpt (reference_segments), every hero
gets one «Персонаж целиком» picture from the franchise passport, and each part carries the built-in Character Swap
settings (DEFAULT_SWAP_SETTINGS). The agent still looks at every part (asset_frames) and writes its six-section
prompt; nothing here queues GPU work.

route «minimax_api» is disabled: plan() accepts only the GPU route. The cloud API treats the source video as a guide,
so its prompts needed a [Shot N] action map plus the SOURCE LEAK GUARD block below (without them the API invents
actions and lets the source room, fur and performers leak in).
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .franchise_profile import bible_roles, entity_cards, load_profile, resolve_shot, translit
from .franchises import FranchiseStore
from .minimax_cloud import LEAK_GUARD_MARKER
from .reference_segments import SegmentRequest, reference_segment
from .store import _media_duration_seconds

# A 15 s part samples about 2.2× longer than a 10 s one at the same settings, so swap parts stay at ≤10 s.
MAX_PART_S, MIN_PART_S = 10, 4  # within reference_segments.SegmentRequest
MAX_SOURCE_S = 180
FPS = 24  # reference_segment re-encodes every excerpt at 24 fps
MIN_SHOT_S = 1.0  # shorter source shots are merged into a neighbour in the [Shot] plan (h3_take_check, 06.11)
SCENE_THRESHOLD = 0.35
# Built-in Character Swap settings: full BF16 quality, 24 steps, safe cache, max reference size, 0.8 MP; the user
# changes the size by hand in Studio. Seed and duration belong to each part.
DEFAULT_SWAP_SETTINGS = {"aspect_ratio": "9:16", "quality": "custom", "megapixels": 0.8, "steps": 24,
                         "cache_mode": "safe", "ref_image_size": "max", "mode": "replace", "style_adapter": "auto",
                         "use_video_audio": True, "keep_source_audio": True, "emotional_direction": False,
                         "seed": None}


def leak_guard(heroes: list[dict], environment_number: int) -> str:
    """Rules against the typical API leaks: source furniture, a second (source) animal, source fur in inserts."""
    names = ", ".join(h["name"] for h in heroes)
    performers = "; ".join(h["performer"] for h in heroes)
    looks = " ".join(f"In hand, paw and body close-ups {h['name']} keeps exactly the skin, fur and clothes of "
                     f"<Picture {h['body_number']}>." for h in heroes)
    return (f"{LEAK_GUARD_MARKER}: Only {names} appear, each exactly once; there is no other person or animal. "
            f"The source performers ({performers}) never appear — not in wide shots, the background or close-up "
            f"inserts. {looks} Every wall, piece of furniture, prop colour and TV/console comes from "
            f"<Picture {environment_number}>; none of the source room's furniture, colours or walls.\n"
            "ACTION FIDELITY: The [Shot N] actions are mandatory, in the listed order and seconds. Each prop appears "
            "where and when the source shows it. Do not invent, skip or reorder actions, props or shots; match each "
            "shot's camera size, angle and movement to <Video 1>. No on-screen text, captions, logos or watermarks.")


class Pair(BaseModel):
    hero: str = Field(min_length=1, max_length=80)
    performer: str = Field(min_length=3, max_length=300, description="who in the source: look, place, action")


class SwapPlanInput(BaseModel):
    asset_id: str = Field(min_length=1, max_length=100)
    franchise_id: str = Field(min_length=1, max_length=100)
    pairs: list[Pair] = Field(min_length=1, max_length=4)
    audio: Literal["source", "hero_voices"] = "source"
    route: Literal["gpu", "minimax_api"] | None = Field(None, description="None: the Studio «Свой GPU / MiniMax API» switch")
    environment_asset_id: str | None = Field(None, max_length=100, description="API route: the franchise set picture")
    source_short_edge: Literal[480] | None = Field(None, description="A/B: source excerpts at 480 short edge (faster)")


def source_cuts(path: Path, duration: float) -> list[float]:
    """Hard cuts of the source (ffmpeg scene score), so a part does not start in the middle of a shot."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0",
                             "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=300, check=False)
    times = sorted({round(float(t), 3) for t in re.findall(r"pts_time:([\d.]+)", result.stderr)})
    return [t for t in times if 0.5 < t < duration - 0.5]


def end_card_start(path: Path, duration: float) -> float | None:
    """Start of a black end card that runs to the end (a channel outro on black); the swap must not render it. A small logo on black still counts as black (pic_th 0.90)."""
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0",
                             "-vf", "blackdetect=d=0.5:pic_th=0.90:pix_th=0.10", "-an", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=300, check=False)
    spans = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", result.stderr)]
    tail = [a for a, b in spans if b >= duration - 0.3 and a >= MIN_PART_S]
    return round(tail[-1], 3) if tail else None


def split_parts(duration: float, cuts: list[float], max_part: int = MAX_PART_S) -> list[dict]:
    """4–10 s parts that tile the source end to end. A part starts exactly on a source cut (or where the part before
    ends) so H3 never meets a sliver of the previous shot; its whole-second length runs on past the boundary and the
    glue cuts that run-on off its tail (`tail_frames`). The last part ends exactly at the source's end and reaches back
    instead (`trim_frames` off its head)."""
    if duration < MIN_PART_S:
        raise ValueError(f"Исходное видео короче {MIN_PART_S} с")
    parts, start = [], 0.0
    while duration - start > max_part:
        fits = [c for c in cuts if start + MIN_PART_S <= c <= start + max_part and duration - c >= MIN_PART_S]
        end = max(fits) if fits else start + max_part
        if duration - end < MIN_PART_S:  # leave the last part its 4 s
            end = duration - MIN_PART_S
        seconds = math.ceil(end - start - 1e-6)
        parts.append({"start": round(start, 3), "duration": seconds, "trim_frames": 0,
                      "tail_frames": round((seconds - (end - start)) * FPS), "keep": [round(start, 3), round(end, 3)]})
        start = end
    seconds = max(MIN_PART_S, math.ceil(duration - start - 1e-6))
    begin = max(0.0, duration - seconds)
    # The last part reaches back; never let it open on a sliver of a shot (a cut < MIN_SHOT_S after its first frame):
    # a 0.33–0.68 s opening shot collapses the whole cut timeline of a take.
    while seconds < max_part and begin >= 1.0 and any(0 < c - begin < MIN_SHOT_S for c in cuts):
        seconds, begin = seconds + 1, begin - 1.0
    parts.append({"start": round(begin, 3), "duration": seconds, "trim_frames": round((start - begin) * FPS),
                  "tail_frames": 0, "keep": [round(start, 3), round(duration, 3)]})
    for index, part in enumerate(parts, 1):
        end = part["start"] + part["duration"]
        inside = [round(c - part["start"], 2) for c in cuts if part["start"] < c < end]
        edges = [0.0, *inside, round(end - part["start"], 2)]
        part.update(index=index, end=round(end, 3), source_cuts=inside,
                    # shots under MIN_SHOT_S drift, swap order or spawn a second hero (h3_take_check, 25/27 night
                    # takes): the prompt merges them into a neighbour instead of giving them their own [Shot]
                    short_shots=[[a, b] for a, b in zip(edges, edges[1:]) if b - a < MIN_SHOT_S])
    return parts


def hero_pictures(store, franchise_id: str, heroes: list[str], voices: bool) -> tuple[dict, list[str]]:
    """One «Персонаж целиком» per hero from the passport (the accepted swaps had exactly that), plus the hero's
    «Голос» when their own voices are asked for. A hero without a full-body picture is a stop, not a guess."""
    data = FranchiseStore(store.root)
    profile, _ = load_profile(data, franchise_id)
    cards = entity_cards(data, franchise_id)
    ids = {a.asset_id for r in profile.roles for a in r.assets} | {a for c in cards for a in c.get("asset_ids", [])}
    assets = {i: a for i in ids if (a := store.get_asset(i))}
    resolved = resolve_shot(profile, bible_roles(profile, cards, assets), assets,
                            {"id": "swap", "characters": heroes})
    roles, owners = resolved["reference_roles"], resolved["reference_owners"]
    found, problems = {}, []
    for hero in heroes:
        mine = [a for a in resolved["asset_ids"] if translit(owners.get(a, "")) == translit(hero)]
        body = [a for a in mine if roles.get(a) == "Персонаж целиком"]
        if not body:
            problems.append(f"{hero}: в паспорте франшизы нет фото «Персонаж целиком» — замена без него не держит "
                            "лицо и костюм; добавь фото героя целиком с пустыми руками")
            continue
        voice = [a for a in mine if roles.get(a) == "Голос"]
        if voices and not voice:
            problems.append(f"{hero}: в паспорте нет голоса героя («Голос») для его реплик")
        found[hero] = {"picture": body[0], "voice": voice[0] if voices and voice else None,
                       "name": next((owners[a] for a in body), hero)}
    return found, problems


def api_pictures(store, franchise_id: str, heroes: list[str], environment_asset_id: str | None) -> tuple[dict, str | None, list[str]]:
    """API route: body + face per hero and one franchise set picture — the 02.11 accepted set, nothing guessed."""
    data = FranchiseStore(store.root)
    profile, _ = load_profile(data, franchise_id)
    cards = entity_cards(data, franchise_id)
    ids = {a.asset_id for r in profile.roles for a in r.assets} | {a for c in cards for a in c.get("asset_ids", [])}
    assets = {i: a for i in ids if (a := store.get_asset(i))}
    resolved = resolve_shot(profile, bible_roles(profile, cards, assets), assets, {"id": "swap", "characters": heroes})
    roles, owners = resolved["reference_roles"], resolved["reference_owners"]
    found, problems = {}, []
    for hero in heroes:
        mine = [a for a in resolved["asset_ids"] if translit(owners.get(a, "")) == translit(hero)]
        body = [a for a in mine if roles.get(a) == "Персонаж целиком"]
        face = [a for a in mine if roles.get(a) == "Лицо героя"]
        if not body or not face:
            problems.append(f"{hero}: для замены через MiniMax API нужны фото «Персонаж целиком» и «Лицо героя» "
                            "из паспорта франшизы — без лица API берёт морду исполнителя")
            continue
        found[hero] = {"picture": body[0], "face": face[0], "name": next((owners[a] for a in body), hero)}
    environment = environment_asset_id
    if environment:
        asset = store.get_asset(environment)
        if not asset or asset.get("kind") != "image":
            problems.append("Окружение: картинка не найдена в медиатеке")
        elif not any(a.asset_id == environment and a.role == "Окружение"
                     for role in profile.roles for a in role.assets):
            problems.append("Окружение: выбранная картинка не закреплена как место этой франшизы")
    else:
        environment = next((a for a in resolved["asset_ids"] if roles.get(a) == "Окружение"), None)
        if not environment:
            problems.append("Окружение: укажи environment_asset_id — фото места франшизы. Без него API переносит "
                            "комнату исходника")
    return found, environment, problems


def recipe_settings(pairs: int) -> tuple[dict, str]:
    """Settings of every swap part: the built-in defaults (a fresh Studio has no accepted recipe of its own)."""
    return dict(DEFAULT_SWAP_SETTINGS), "default"


def plan(store, payload: SwapPlanInput, default_route: str = "gpu") -> dict:
    route = payload.route or default_route
    if route != "gpu":
        raise ValueError("Замена героев по видео через MiniMax API не поддерживается: план замены строится для своего GPU")
    if payload.environment_asset_id:
        raise ValueError("Смена всего окружения по фото не поддерживается: GPU Character Swap сохраняет исходную "
                         "сцену. Убери фото «Окружение» из плана")
    if len(payload.pairs) > 2:
        raise ValueError("Для трёх и более исполнителей нет проверенного маршрута замены: раздели сцену на части")
    asset = store.get_asset(payload.asset_id)
    if not asset or asset["kind"] != "video":
        raise ValueError("Исходное видео не найдено в медиатеке")
    source = Path(asset["path"])
    duration = _media_duration_seconds(source)
    if duration is None:
        raise ValueError("Не удалось прочитать длительность исходного видео")
    if duration > MAX_SOURCE_S:
        raise ValueError(f"Исходное видео длиннее {MAX_SOURCE_S} с: раздели его на эпизоды")
    heroes = [pair.hero for pair in payload.pairs]
    if len({translit(h) for h in heroes}) != len(heroes):
        raise ValueError("Один герой назначен двум исполнителям: опиши одного исполнителя на героя")
    full, card = duration, end_card_start(source, duration)
    if card:
        duration = card
    result = gpu_plan(store, payload, asset, duration, source, heroes)
    if card and result.get("ready"):
        result["source"]["full_duration"] = round(full, 3)
        result["notes"].append(f"Чёрная заставка {card:.2f}–{full:.2f} с в конце исходника в план не входит.")
    return result


def gpu_plan(store, payload: SwapPlanInput, asset: dict, duration: float, source: Path, heroes: list[str]) -> dict:
    found, problems = hero_pictures(store, payload.franchise_id, heroes, payload.audio == "hero_voices")
    if problems:
        return {"ready": False, "problems": problems}
    cuts = source_cuts(source, duration)
    settings, recipe_id = recipe_settings(len(payload.pairs))
    if payload.audio == "hero_voices":
        settings.update(use_video_audio=False, keep_source_audio=False)
    pairs = [{"owner": found[p.hero]["name"], "performer": p.performer} for p in payload.pairs]
    parts = []
    for part in split_parts(duration, cuts):
        segment = reference_segment(store, payload.asset_id, SegmentRequest(
            start=part["start"], duration=part["duration"], short_edge=payload.source_short_edge))
        heroes_ids = [found[h]["picture"] for h in heroes] + [found[h]["voice"] for h in heroes if found[h]["voice"]]
        ids = heroes_ids + [segment["asset_id"]]
        roles = {found[h]["picture"]: "Персонаж целиком" for h in heroes}
        roles.update({found[h]["voice"]: "Голос" for h in heroes if found[h]["voice"]})
        roles[segment["asset_id"]] = "Исходное видео"
        owners = {found[h]["picture"]: found[h]["name"] for h in heroes}
        owners.update({found[h]["voice"]: found[h]["name"] for h in heroes if found[h]["voice"]})
        parts.append({**part, "label": f"часть {part['index']}/?", "segment_asset_id": segment["asset_id"],
                      "form": {"asset_ids": ids, "reference_roles": roles, "reference_owners": owners,
                               "reference_performers": {segment["asset_id"]: {"pairs": pairs, "keep_others": True}},
                               "settings": {**settings, "duration": part["duration"]}}})
    for part in parts:
        part["label"] = f"часть {part['index']}/{len(parts)}"
    notes = ["Принятого рецепта пока нет: настройки — встроенные (BF16, 24 шага, safe cache, max refs, 0.8 МП); "
             "каждая часть проверяется по своему MP4 целиком."]
    if payload.audio == "hero_voices":
        notes.append("Голоса героев: H3 генерирует звук заново по голосам героев — музыка и шумы оригинала не "
                     "копируются. Экспериментально; исходный звук — проверенный путь.")
    return {"ready": True, "source": {"asset_id": payload.asset_id, "name": asset["original_name"],
                                      "duration": round(duration, 3), "cuts": cuts},
            "franchise_id": payload.franchise_id, "recipe_id": recipe_id, "audio": payload.audio,
            "pairs": pairs, "parts": parts, "glue_trim_frames": [p["trim_frames"] for p in parts],
            "glue_tail_frames": [p["tail_frames"] for p in parts],
            "notes": notes}


def make_router(ctx) -> APIRouter:
    router = APIRouter()

    @router.post("/api/video-swap/plan")
    async def swap_plan(payload: SwapPlanInput) -> dict:
        try:
            cloud = getattr(ctx.gpu_transport, "cloud", None)
            route = cloud.default_backend() if cloud else "gpu"
            return await asyncio.to_thread(plan, ctx.store, payload, route)
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
