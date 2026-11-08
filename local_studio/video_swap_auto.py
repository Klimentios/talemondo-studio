"""«Замена по видео» end to end: plan → an agent writes every part's [Shot N] action map from its frames → the
six-section prompt → all parts queued.

Until 03.11.2026 an agent did the middle by hand for every swap (asset_frames, then a prompt per part), and the user
asked for it to happen by itself. The split of work stays the same as in the accepted swaps: the subscription agent
(agent_brain, no API key) only says WHAT happens in each source shot — who does what with which prop, face, camera.
Who the heroes are (passport look lines), which set they are in, the leak guard and the render settings come from
the franchise and video_swap's plan, so the agent cannot drift the heroes or the room. Jobs go through the Studio's
own POST /api/jobs, with every check that a hand-made job gets.

Character Swap preserves the source set. A request to change the environment must use the separate world-swap
route; reject it before planning or queuing any part.
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
from pydantic import Field

from app.swap_mask import normalize_swap_mask
from . import agent_brain, scene_kit, shot_contract, video_swap
from .franchise_profile import translit

MAX_FRAMES = 14
LONG_SHOT_S = 2.5  # a longer shot gets two frames: the action inside it changes (push-in, a step, a jump)
PERFORMER_MAX = 80  # store.py: one line, at most 80 characters
SHOT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots"],
               "properties": {"shots": {"type": "array", "items": {
                   "type": "object", "additionalProperties": False, "required": ["index", "action"],
                   "properties": {"index": {"type": "integer"}, "action": {"type": "string"}}}}}}


class AutoInput(video_swap.SwapPlanInput):
    provider: str = Field(default="claude", pattern="^(claude|codex)$")
    enqueue: bool = False
    # A/B masked swap (app/swap_mask.py): the brain also marks each performer on the part's first frame.
    mask: bool = False
    # The user's words asking for the A/B mask (store.require_ab_request refuses the mask without them).
    ab_user_request: str = Field(default="", max_length=400)


BOX = {"type": "array", "minItems": 4, "maxItems": 4, "items": {"type": "number"}}
MASK_SHOT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["shots", "boxes"],
                    "properties": {"shots": SHOT_SCHEMA["properties"]["shots"],
                                   "boxes": {"type": "array", "items": {
                                       "type": "object", "additionalProperties": False, "required": ["hero", "box"],
                                       "properties": {"hero": {"type": "string"}, "box": BOX}}}}}


def mask_from_boxes(heroes: list[dict], boxes: list[dict], label: str) -> dict:
    """The brain's first-frame boxes → a checked swap_mask; every hero needs its performer on frame 0."""
    by_hero = {str(b.get("hero", "")).casefold(): b.get("box") for b in boxes}
    missing = [h["name"] for h in heroes if not by_hero.get(h["name"].casefold())]
    if missing:
        raise ValueError(f"{label}: на первом кадре не отмечен исполнитель для {', '.join(missing)}; маска невозможна")
    return normalize_swap_mask({"performers": [{"name": h["name"], "box": by_hero[h["name"].casefold()]}
                                               for h in heroes], "grow": 24})


def shot_lines(spans: list[tuple[float, float]], actions: dict[int, str]) -> list[str]:
    """[Shot N] lines in MiniMax's official timing: «[Shot 2] At 00:01.630, the shot cuts to …».

    Measured 06.11 (docs/reference/H3_ENCODER_RESEARCH.md): the range form («[Shot 2: 1.63–3.40s]», «[Shot 2]
    01.63-03.40.») is not read as a cut — the night take 97d46b7c cut at +0.95/+0.64 s with it and at −0.05/−0.28 s
    with «At», same seed and references; on a two-shot test +0.54 s (+1.75 s planned earlier) against +0.04 s."""
    lines = []
    for n, (a, _b) in enumerate(spans, 1):
        action = actions.get(n, "").strip()
        if n == 1 or a <= 0:
            lines.append(f"[Shot {n}] {action}")
            continue
        minutes, seconds = divmod(round(a, 3), 60)
        action = re.sub(r"^cut\s+to\s+", "", action, flags=re.I)
        at = f"[Shot {n}] At {int(minutes):02d}:{seconds:06.3f}, the shot cuts"
        if shot_contract.FRAMING.match(action):  # a shot size: «… the shot cuts to a low close insert …»
            lines.append(f"{at} to {action[:1].lower() + action[1:]}")
        else:  # a sentence (NOVA's paw…, He turns…, Kai jumps…): keep it as written, names and case untouched
            lines.append(f"{at}: {action}")
    return lines


def shot_spans(part: dict, video: Path | None = None) -> list[tuple[float, float]]:
    """The part's own shots: its cuts inside (0, duration), clipped to the part."""
    duration = float(part["duration"])
    cuts = list(part.get("source_cuts") or [])
    if video is not None:
        result = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-i", str(video), "-map", "0:v:0",
                                 "-vf", "select='gt(scene,0.2)',showinfo", "-f", "null", "-"],
                                capture_output=True, text=True, timeout=90, check=False)
        cuts.extend(float(t) for t in re.findall(r"pts_time:([\d.]+)", result.stderr))
    inner = []
    for c in sorted(c for c in cuts if 0.20 < c < duration - 0.05):
        if not inner or c - inner[-1] >= 0.30:
            inner.append(c)
    edges = [0.0, *inner, duration]
    spans = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 0.05]
    # A shot under video_swap.MIN_SHOT_S gets no [Shot] of its own: H3 drifts, reorders or doubles a hero on such
    # slivers (h3_take_check). It joins the previous shot (the first joins the next).
    merged: list[tuple[float, float]] = []
    for a, b in spans:
        if merged and (b - a < video_swap.MIN_SHOT_S or merged[-1][1] - merged[-1][0] < video_swap.MIN_SHOT_S):
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def frame_times(spans: list[tuple[float, float]]) -> list[tuple[int, float]]:
    """(shot index, time) — the middle of a short shot, its thirds for a long one, at most MAX_FRAMES."""
    picks = []
    for n, (a, b) in enumerate(spans, 1):
        marks = (a + (b - a) / 3, a + 2 * (b - a) / 3) if b - a > LONG_SHOT_S else ((a + b) / 2,)
        picks += [(n, round(t, 2)) for t in marks]
    if len(picks) > MAX_FRAMES:  # keep one per shot first, then the rest in order
        firsts = {n for n, _ in picks}
        keep = [p for p in picks if p == next(q for q in picks if q[0] == p[0])]
        keep += [p for p in picks if p not in keep][:MAX_FRAMES - len(firsts)]
        picks = sorted(keep, key=lambda p: p[1])
    return picks


def extract_frames(video: Path, picks: list[tuple[int, float]], folder: Path) -> list[str]:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                            "default=noprint_wrappers=1:nokey=1", str(video)], check=True,
                           timeout=30, capture_output=True, text=True)
    video_duration = float(probe.stdout.strip())
    paths = []
    for at, (n, t) in enumerate(picks):
        out = folder / f"shot{n:02d}-{at:02d}.jpg"
        safe_t = min(t, max(0.0, video_duration - 0.20))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{safe_t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=512:-2", str(out)], check=True, timeout=60, capture_output=True)
        paths.append(str(out))
    return paths


def short_performer(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= PERFORMER_MAX:
        return text
    return text[:PERFORMER_MAX].rsplit(" ", 1)[0].rstrip(",;")


def describe_prompt(heroes: list[dict], spans: list[tuple[float, float]], picks: list[tuple[int, float]],
                    part_label: str, images: list[str] | None = None, franchise_id: str = "",
                    first_frame: str | None = None) -> str:
    who = "; ".join(f"{h['performer']} is {h['name']}" for h in heroes)
    frames = "\n".join(f"Image {i + 1}: shot {n} at {t:.2f} s, file {images[i]}"
                       for i, (n, t) in enumerate(picks)) if images else ""
    shots = "\n".join(f"Shot {n}: {a:.2f}-{b:.2f} s" for n, (a, b) in enumerate(spans, 1))
    return (
        f"You map the shots of one source clip ({part_label} of a character swap) for MiniMax H3.\n"
        f"Target franchise: {franchise_id}. Use only the mapped heroes below, with their own franchise identities.\n"
        f"Performers: {who}. Call them only by these hero names.\n{shots}\n{frames}\n\n"
        "The source video supplies actions and staging, not the performers' identity, clothes or body. "
        "For every shot write ONE English sentence (25-45 words): camera size, angle and movement; who is where on "
        "screen; who does what with which prop; their facial expression and where they look. If a shot is an "
        "imagined or dream scene, say so and describe its set in two or three plain words (\"the apartment at "
        "night\"). Never describe clothes, hair, fur, skin, body shape or the source room's furniture, colours or "
        "walls: identity and set come from the franchise pictures. Do not invent actions you do not see. "
        + (f"\nFirst frame (0.00 s): file {first_frame}. For each hero give the box [x, y, width, height] as fractions "
           "0..1 of that frame around the WHOLE source performer who becomes that hero (head to feet, tail and hair "
           "included), in \"boxes\": [{\"hero\": \"NAME\", \"box\": [x, y, w, h]}]. Leave a hero out if their "
           "performer is not visible on the first frame. " if first_frame else "")
        + "Return JSON {\"shots\": [{\"index\": 1, \"action\": \"...\"}, ...]"
        + (", \"boxes\": [...]" if first_frame else "") + "} with one item per shot, in order.")


def subject_lines(kit: dict, heroes: list[dict], environment_number: int | None) -> list[str]:
    lines = []
    for at, hero in enumerate(heroes, 1):
        where = (f"face from <Picture {hero['face_number']}>, full body and outfit from <Picture {hero['body_number']}>"
                 if hero.get("face_number") else f"face, full body and outfit from <Picture {hero['body_number']}>")
        # Passport sheets can mention alternative costumes in conditional clauses
        # (e.g. a wizard robe "only when the shot names it"). H3 may draw the
        # mentioned outfit despite the condition, so use the same sanitiser as
        # the scene prompt compiler before adding identity text to a swap.
        look = shot_contract._kit_words(scene_kit.sheet_words(kit, hero["name"], scene_kit.LOOK_KEYS))
        lines.append(f"<Subject {at}> is {hero['name']}: {where}. {look} {hero['name']} replaces "
                     f"{hero['performer']} of <Video 1> and takes only actions, expressions and timing from it, "
                     "never its clothes, hair, fur or body shape.")
    if environment_number:
        lines.append(f"<Subject {len(heroes) + 1}> is the environment of <Picture {environment_number}>: "
                     "the franchise set; its walls, furniture and light in every shot.")
    return lines


def gpu_guard(heroes: list[dict]) -> str:
    names = ", ".join(h["name"] for h in heroes)
    # Positive and without the performers' descriptions: naming the source looks again made H3 draw them.
    return (f"{video_swap.LEAK_GUARD_MARKER}: Only {names} appear, each exactly once in every shot, "
            "including close-up inserts, photos and screens. Clean frame without on-screen text, logos or watermarks.")


def compose(kit: dict, heroes: list[dict], environment_number: int | None, part: dict, total: int,
            spans: list[tuple[float, float]], actions: dict[int, str], guard: str) -> str:
    names = " and ".join(h["name"] for h in heroes)
    where = f" in the set of <Picture {environment_number}>" if environment_number else ""
    retention = [f"<Subject {at}>: {h['name']}'s identity, face, proportions and outfit only from its pictures; "
                 "<Video 1> gives only actions, expressions and timing." for at, h in enumerate(heroes, 1)]
    if environment_number:
        retention.append(f"<Subject {len(heroes) + 1}>: walls, furniture and light only from <Picture {environment_number}>.")
    shots = shot_lines(spans, actions)
    return "\n".join([
        "subject_definitions:", *subject_lines(kit, heroes, environment_number), "<Video 1> provides the performance, "
        "shot order, timing, camera and synchronized soundtrack.", "",
        "summary:", f"[video editing] Part {part['index']} of {total}. Repeat the story, actions, camera and timing of "
        f"<Video 1> with {names}{where}, in the franchise's own look, faces, proportions and emotions.", "",
        "retention_analysis:", *retention, "",
        "detailed_description:", *shots, "", guard, "",
        "overall_soundscape:", "Reuse the synchronized original soundtrack of <Video 1>.", "",
        "non_diegetic_music:", "Reuse the original music of <Video 1>."])


def job_payload(plan: dict, part: dict, heroes: list[dict], pictures: list[tuple[str, str, str]],
                environment: str | None, prompt: str, run_id: str) -> dict:
    segment = part["segment_asset_id"]
    ids = [a for a, _, _ in pictures] + [segment]
    roles = {a: r for a, r, _ in pictures}
    roles[segment] = "Исходное видео"
    owners = {a: o for a, _, o in pictures if o}
    numbers = {a: n for n, (a, _, _) in enumerate(pictures, 1)}
    numbers[segment] = 1
    pairs = [{"owner": h["name"], "performer": h["performer"]} for h in heroes]
    settings = dict(part["form"]["settings"])
    return {"client_request_id": f"swap-auto-{run_id[:12]}-{part['index']}", "asset_ids": ids,
            "reference_roles": roles, "reference_owners": owners, "reference_numbers": numbers,
            "reference_performers": {segment: {"pairs": pairs, "keep_others": environment is None}},
            "settings": settings, "prompt": prompt}


def hero_set(store, payload: AutoInput, plan: dict) -> tuple[list[dict], list[tuple[str, str, str]], str | None]:
    """Pictures in <Picture N> order. With a set picture: body + face per hero, then the set (the 02.11 set)."""
    names = [p.hero for p in payload.pairs]
    performers = {translit(p.hero): short_performer(p.performer) for p in payload.pairs}
    if payload.environment_asset_id:
        found, environment, problems = video_swap.api_pictures(store, payload.franchise_id, names, payload.environment_asset_id)
        if problems:
            raise ValueError("; ".join(problems))
        pictures, heroes = [], []
        for name in names:
            info = found[name]
            pictures += [(info["picture"], "Персонаж целиком", info["name"]), (info["face"], "Лицо героя", info["name"])]
            heroes.append({"name": info["name"], "performer": performers[translit(name)],
                           "body_number": len(pictures) - 1, "face_number": len(pictures)})
        pictures.append((environment, "Окружение", ""))
        return heroes, pictures, environment
    form = plan["parts"][0]["form"]
    pictures = [(a, form["reference_roles"][a], form["reference_owners"].get(a, ""))
                for a in form["asset_ids"] if form["reference_roles"].get(a) != "Исходное видео"]
    heroes = [{"name": o, "performer": performers.get(translit(o), o), "body_number": n}
              for n, (a, r, o) in enumerate(pictures, 1) if r == "Персонаж целиком"]
    return heroes, pictures, None


class AutoRuns:
    def __init__(self, root: Path):
        self.root = root

    def path(self, run_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise HTTPException(404, "Нет такой авто-замены")
        return self.root / f"{run_id}.json"

    def save(self, run: dict) -> dict:
        self.root.mkdir(parents=True, exist_ok=True)
        run["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = self.path(run["id"]).with_suffix(".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path(run["id"]))
        return run

    def load(self, run_id: str) -> dict:
        path = self.path(run_id)
        if not path.is_file():
            raise HTTPException(404, "Нет такой авто-замены")
        return json.loads(path.read_text(encoding="utf-8"))


async def run_auto(store, runs: AutoRuns, app, run: dict, payload: AutoInput, route: str) -> None:
    def step(**change):
        run.update(change)
        runs.save(run)

    try:
        if payload.environment_asset_id:
            raise ValueError("Смена окружения требует маршрута «Смена мира по видео» (Движение и камера, Кадр сцены на каждый шот, style3d). Автозамена Character Swap сохраняет исходную комнату; задания не отправлены.")
        plan = await asyncio.to_thread(video_swap.plan, store, payload, route)
        if not plan.get("ready"):
            raise ValueError("; ".join(plan.get("problems") or ["план замены не готов"]))
        heroes, pictures, environment = await asyncio.to_thread(hero_set, store, payload, plan)
        kit = await asyncio.to_thread(scene_kit.get_scene_kit, store, payload.franchise_id)
        total = len(plan["parts"])
        guard = (video_swap.leak_guard([{"name": h["name"], "performer": h["performer"], "body_number": h["body_number"]}
                                        for h in heroes], len(pictures)) if environment else gpu_guard(heroes))
        step(state="describing", source=plan["source"], glue_trim_frames=plan["glue_trim_frames"],
             glue_tail_frames=plan["glue_tail_frames"],
             parts=[{"index": p["index"], "label": p["label"], "start": p["start"], "duration": p["duration"],
                     "segment_asset_id": p["segment_asset_id"], "state": "pending"} for p in plan["parts"]])
        for part, entry in zip(plan["parts"], run["parts"]):
                video = Path(store.get_asset(part["segment_asset_id"])["path"])
                spans = await asyncio.to_thread(shot_spans, part, video)
                picks = frame_times(spans)
                with tempfile.TemporaryDirectory(prefix="h3-swap-auto-") as tmp:
                    frames = await asyncio.to_thread(extract_frames, video, picks, Path(tmp))
                    first = None
                    if payload.mask:
                        (Path(tmp) / "first").mkdir()
                        first = (await asyncio.to_thread(extract_frames, video, [(0, 0.0)], Path(tmp) / "first"))[0]
                    answer = await agent_brain.ask(agent_brain.BrainRequest(
                        provider=payload.provider, prompt=describe_prompt(
                            heroes, spans, picks, part["label"], frames, payload.franchise_id, first),
                        schema=MASK_SHOT_SCHEMA if payload.mask else SHOT_SCHEMA, read_repo=False,
                        images=frames + ([first] if first else []), timeout_s=600))
                shots = (answer.get("data") or {}).get("shots") or []
                actions = {int(s["index"]): str(s["action"]) for s in shots if s.get("action")}
                missing = [n for n in range(1, len(spans) + 1) if n not in actions]
                if missing or len(actions) != len(spans):
                    raise ValueError(f"{part['label']}: агент не описал шоты {missing}")
                if any(re.search(r"\b(unavailable|not attached|could not be found|cannot be described)\b", action, re.I)
                       for action in actions.values()):
                    raise ValueError(f"{part['label']}: агент не увидел кадры исходника; задания не отправлены")
                prompt = compose(kit, heroes, len(pictures) if environment else None, part, total, spans, actions, guard)
                if payload.mask:
                    entry["swap_mask"] = mask_from_boxes(heroes, (answer.get("data") or {}).get("boxes") or [],
                                                         part["label"])
                entry.update(state="described", shots=[{"start": a, "end": b, "action": actions[n]}
                                                       for n, (a, b) in enumerate(spans, 1)], prompt=prompt)
                step()
        if payload.enqueue:
            step(state="enqueuing")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://127.0.0.1", timeout=120) as client:
                # One paid pilot first. The remaining prompts stay in the run for review;
                # a completed render is not evidence that its identities and set are right.
                for part, entry in list(zip(plan["parts"], run["parts"]))[:1]:
                    body = job_payload(plan, part, heroes, pictures, environment, entry["prompt"], run["id"])
                    if entry.get("swap_mask"):
                        body["settings"]["swap_mask"] = entry["swap_mask"]
                        body["settings"]["ab_user_request"] = payload.ab_user_request
                    response = await client.post("/api/jobs", json=body)
                    if response.status_code >= 400:
                        raise ValueError(f"{part['label']}: Studio не приняла задание — {response.text[:300]}")
                    entry.update(state="queued", job_id=response.json()["job_id"])
                    step()
        step(state=("awaiting_qc" if total > 1 else "queued") if payload.enqueue else "described")
    except (ValueError, OSError, subprocess.SubprocessError, agent_brain.BrainError, asyncio.TimeoutError) as exc:
        step(state="failed", error=str(exc)[:600])


def make_router(ctx) -> APIRouter:
    router = APIRouter()
    runs = AutoRuns(ctx.store.root / "video-swap-auto")
    tasks: set[asyncio.Task] = set()

    @router.post("/api/video-swap/auto")
    async def start(payload: AutoInput, request: Request) -> dict:
        cloud = getattr(ctx.gpu_transport, "cloud", None)
        route = payload.route or (cloud.default_backend() if cloud else "gpu")
        if route != "gpu":
            raise HTTPException(422, "Автоматическая замена героев по видео работает только на своём GPU")
        run = runs.save({"id": uuid.uuid4().hex, "state": "planning", "route": route, "asset_id": payload.asset_id,
                         "franchise_id": payload.franchise_id, "provider": payload.provider,
                         "environment_asset_id": payload.environment_asset_id, "parts": [],
                         "created_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        task = asyncio.create_task(run_auto(ctx.store, runs, request.app, run, payload, route))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return run

    @router.get("/api/video-swap/auto/{run_id}")
    async def status(run_id: str) -> dict:
        return runs.load(run_id)

    return router
