"""Scene staging by geometry: a floor plan in metres → what every camera setup actually sees.

The agent places heroes, props and set pieces on a top-down plan (x to the right, y away from the set's front, metres;
facing 0° = +y, 90° = +x) and names camera setups (position, height, lens, what they aim at). compile() projects
everything through a pinhole camera with the real angle of view of the lens for the frame format, so screen sides,
depth order, the size of each hero in frame, eyelines and occlusion are computed, not written by hand. From that it
checks the 180° line between the axis pair, heroes swapping screen sides, the shot size the card declares against
the one the lens and distance give, heroes the shot names but the frame does not show, and the camera height
against the eye level it should hold (cats at 20–30 cm). Each shot gets an English staging line for its first frame
and prompt, and the scene a top-down previz (SVG). No GPU, no model call.
"""
from __future__ import annotations

import math
from html import escape

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contract import ASPECT_PATTERN, ASPECTS

FRAMINGS = ("extreme_wide", "wide", "full", "medium_full", "medium", "medium_close", "close", "extreme_close")
# Full-frame 36×24 sensor cropped to the delivery frame: (w, h). 16:9 → 36×20.25, 9:16 → 13.5×24, 4:3 → 32×24.
FRAME_MM = {a: (36.0, 36.0 / r) if r >= 1.5 else (24.0 * r, 24.0) for a, r in ASPECTS.items()}
SIDE_DEAD_ZONE = 0.2   # |screen x| below this reads as centre
NEUTRAL_LINE_M = 0.1   # a camera this close to the axis line sits on it, either side is fine after it
EYE_TOLERANCE_CM = 12


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="hero", pattern="^(hero|prop|set)$")
    x: float = Field(ge=-100, le=100)
    y: float = Field(ge=-100, le=100)
    facing: float = Field(default=0, ge=-360, le=360)
    height_cm: float = Field(default=170, gt=0, le=2000)
    width_cm: float = Field(default=45, gt=0, le=2000)
    eye_height_cm: float | None = Field(default=None, gt=0, le=2000)


class Setup(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=40)
    x: float = Field(ge=-100, le=100)
    y: float = Field(ge=-100, le=100)
    aim: str | list[float] = Field(description="An actor name or [x, y] on the plan")
    height_cm: float = Field(gt=0, le=5000)
    lens_mm: float = Field(ge=8, le=600)
    eye_level_of: str = ""  # hero whose eye level this setup must hold


class StagedShot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    setup: str
    characters: list[str] = Field(default_factory=list, max_length=8)
    subject: str = ""  # the hero the declared framing is about; default: the first character
    framing: str = Field(default="", pattern=f"^(|{'|'.join(FRAMINGS)})$")
    crossing: bool = False  # the move or cut that deliberately takes the camera across the line


class Staging(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    aspect_ratio: str = Field(default="9:16", pattern=ASPECT_PATTERN)
    actors: list[Actor] = Field(min_length=1, max_length=24)
    axis: list[str] = Field(default_factory=list, max_length=2)
    setups: list[Setup] = Field(min_length=1, max_length=24)
    shots: list[StagedShot] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def known_names(self):
        names = {a.name for a in self.actors}
        setups = {s.id for s in self.setups}
        if len(names) != len(self.actors) or len(setups) != len(self.setups):
            raise ValueError("имена объектов и ID установок камеры должны быть уникальны")
        if self.axis and (len(self.axis) != 2 or not set(self.axis) <= names):
            raise ValueError("ось — два объекта плана")
        for s in self.setups:
            if isinstance(s.aim, str) and s.aim not in names or isinstance(s.aim, list) and len(s.aim) != 2:
                raise ValueError(f"установка {s.id}: aim — имя объекта или [x, y]")
            if s.eye_level_of and s.eye_level_of not in names:
                raise ValueError(f"установка {s.id}: нет объекта {s.eye_level_of}")
        for i, shot in enumerate(self.shots, 1):
            if shot.setup not in setups:
                raise ValueError(f"шот {i}: нет установки {shot.setup}")
            unknown = set(shot.characters) - names | ({shot.subject} - names if shot.subject else set())
            if unknown:
                raise ValueError(f"шот {i}: нет на плане: {', '.join(sorted(unknown))}")
        return self


def framing_for(ratio: float) -> str:
    """Subject height / frame height at the subject → shot size."""
    for limit, name in ((0.15, "extreme_wide"), (0.45, "wide"), (0.95, "full"), (1.3, "medium_full"),
                        (1.9, "medium"), (2.8, "medium_close"), (5.0, "close")):
        if ratio < limit:
            return name
    return "extreme_close"


def unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def heading(degrees: float) -> tuple[float, float]:
    return math.sin(math.radians(degrees)), math.cos(math.radians(degrees))


def side_of_line(a: Actor, b: Actor, x: float, y: float) -> float:
    """Signed distance (m) of a point from the line a→b; the sign is the side."""
    ux, uy = unit(b.x - a.x, b.y - a.y)
    return ux * (y - a.y) - uy * (x - a.x)


def view(plan: Staging, setup: Setup) -> list[dict]:
    actors = {a.name: a for a in plan.actors}
    tx, ty = (actors[setup.aim].x, actors[setup.aim].y) if isinstance(setup.aim, str) else setup.aim
    fx, fy = unit(tx - setup.x, ty - setup.y)
    rx, ry = fy, -fx  # screen right on a plan seen from above
    width_mm, height_mm = FRAME_MM[plan.aspect_ratio]
    half_w = width_mm / (2 * setup.lens_mm)
    half_h = height_mm / (2 * setup.lens_mm)
    seen = []
    for actor in plan.actors:
        vx, vy = actor.x - setup.x, actor.y - setup.y
        depth = vx * fx + vy * fy
        if depth <= 0.05:
            continue
        screen_x = (vx * rx + vy * ry) / depth / half_w
        half_size = actor.width_cm / 200 / depth / half_w
        if abs(screen_x) - half_size > 1:
            continue
        ox, oy = heading(actor.facing)
        across, toward = ox * rx + oy * ry, ox * fx + oy * fy
        looking = ("camera" if toward < -0.75 else "away" if toward > 0.75 else
                   "screen_right" if across > 0 else "screen_left")
        side = "centre" if abs(screen_x) < SIDE_DEAD_ZONE else "right" if screen_x > 0 else "left"
        seen.append({"name": actor.name, "kind": actor.kind, "screen_x": round(screen_x, 2), "side": side,
                     "depth_m": round(depth, 2), "half_size": half_size, "looking": looking,
                     "framing": framing_for(actor.height_cm / 100 / (2 * depth * half_h)),
                     "partly_out": abs(screen_x) + half_size > 1})
    seen.sort(key=lambda item: item["depth_m"])
    for index, near in enumerate(seen):
        for far in seen[index + 1:]:
            if far["kind"] == "hero" and abs(far["screen_x"] - near["screen_x"]) < (near["half_size"] + far["half_size"]) * 0.6:
                far.setdefault("hidden_by", near["name"])
    for item in seen:
        item.pop("half_size")
    return seen


SIDE_WORDS = {"left": "screen-left", "right": "screen-right", "centre": "centre frame"}
LOOK_WORDS = {"camera": "toward the camera", "away": "away from the camera", "screen_right": "screen-right",
              "screen_left": "screen-left"}


def staging_line(setup: Setup, frame: list[dict]) -> str:
    level = ""
    if setup.eye_level_of:
        level = f" at {setup.eye_level_of}'s eye level"
    parts = [f"Camera{level}, {round(setup.height_cm)} cm above the floor, {round(setup.lens_mm)} mm lens."]
    heroes = [item for item in frame if item["kind"] == "hero"]
    for order, item in enumerate(heroes):
        place = "foreground" if order == 0 and len(heroes) > 1 else "background" if order == len(heroes) - 1 and len(heroes) > 1 else "midground"
        if len(heroes) == 1:
            place = f"{item['depth_m']} m from camera"
        parts.append(f"{item['name']}: {SIDE_WORDS[item['side']]}, {place}, {item['framing'].replace('_', ' ')} size, "
                     f"looking {LOOK_WORDS[item['looking']]}.")
    props = [item["name"] for item in frame if item["kind"] != "hero"]
    if props:
        parts.append("Also in frame: " + ", ".join(props) + ".")
    return " ".join(parts)


# Size in frame as plain English for the prompt (the card's framing ladder, FRAMINGS).
SIZE_WORDS = {"extreme_wide": "tiny in the frame", "wide": "small in the frame", "full": "seen head to toe",
              "medium_full": "framed from the knees up", "medium": "framed from the waist up",
              "medium_close": "framed from the chest up", "close": "in close-up", "extreme_close": "in extreme close-up"}


def prompt_line(setup: Setup, frame: list[dict], characters: list[str] = ()) -> str:
    """The staging of one shot for its H3 prompt, inside [Shot N]: who is where, screen side, depth and size, plain
    English. Lens and height are not repeated (the shot's opening names them); only the shot's own heroes are named — a hero
    the frame shows but the shot does not cast has no pictures in the job, so H3 would draw a stranger by name."""
    heroes = [item for item in frame if item["kind"] == "hero" and (not characters or item["name"] in characters)]
    parts = []
    for order, item in enumerate(heroes):
        depth = ("in the foreground" if order == 0 else "in the background" if order == len(heroes) - 1
                 else "in the midground") if len(heroes) > 1 else f"{item['depth_m']:g} m from the camera"
        side = {"left": "screen left", "right": "screen right", "centre": "centre frame"}[item["side"]]
        look = {"camera": "toward the camera", "away": "away from the camera", "screen_right": "toward screen right",
                "screen_left": "toward screen left"}[item["looking"]]
        hidden = f", partly hidden by {item['hidden_by']}" if item.get("hidden_by") else ""
        parts.append(f"{item['name']} is {side} {depth}, {SIZE_WORDS[item['framing']]}{hidden}, looking {look}.")
    return " ".join(parts)  # camera height stays out: the shot's angle words say it once


def compile_staging(plan: Staging) -> dict:
    setups = {s.id: s for s in plan.setups}
    actors = {a.name: a for a in plan.actors}
    findings, shots = [], []

    def add(severity, rule, index, message):
        findings.append({"severity": severity, "rule": f"staging_{rule}", "shot": index, "message": message})

    previous_side, previous_order, previous_look = None, None, None
    for index, shot in enumerate(plan.shots, 1):
        setup = setups[shot.setup]
        frame = view(plan, setup)
        on_screen = {item["name"]: item for item in frame}
        subject = shot.subject or (shot.characters[0] if shot.characters else "")
        # 30° rule: a cut to the same subject needs a new angle (≥ 30°) or a clear change of size (≥ 2 steps).
        look = None
        if subject in on_screen:
            hero = actors[subject]
            look = (subject, math.degrees(math.atan2(setup.x - hero.x, setup.y - hero.y)),
                    FRAMINGS.index(on_screen[subject]["framing"]))
            if previous_look and previous_look[0] == subject:
                turn = abs((look[1] - previous_look[1] + 180) % 360 - 180)
                if turn < 30 and abs(look[2] - previous_look[2]) < 2:
                    add("warning", "jump_cut", index, f"на {subject} с угла {round(turn)}° и почти той же крупности — "
                        "склейка прыгнет: смени угол на 30°+ или крупность на две ступени")
        previous_look = look
        for name in shot.characters:
            item = on_screen.get(name)
            if not item:
                add("error", "not_in_frame", index, f"{name} не попадает в кадр установки {setup.id} ({round(setup.lens_mm)} мм)")
            elif item.get("hidden_by"):
                add("warning", "hidden", index, f"{name} закрыт объектом {item['hidden_by']}")
            elif item["partly_out"]:
                add("tip", "edge", index, f"{name} обрезан краем кадра")
        if shot.framing and subject in on_screen:
            real = on_screen[subject]["framing"]
            if abs(FRAMINGS.index(real) - FRAMINGS.index(shot.framing)) > 1:
                add("error", "framing", index, f"заявлен {shot.framing}, а {subject} на {on_screen[subject]['depth_m']} м "
                    f"с {round(setup.lens_mm)} мм даёт {real}: подвинь камеру или смени объектив")
        if setup.eye_level_of:
            hero = actors[setup.eye_level_of]
            eye = hero.eye_height_cm or hero.height_cm * 0.93
            if abs(setup.height_cm - eye) > EYE_TOLERANCE_CM:
                add("warning", "eye_level", index, f"камера {round(setup.height_cm)} см, глаза {hero.name} — {round(eye)} см")
        side = None
        if plan.axis:
            a, b = actors[plan.axis[0]], actors[plan.axis[1]]
            distance = side_of_line(a, b, setup.x, setup.y)
            side = 0 if abs(distance) < NEUTRAL_LINE_M else (1 if distance > 0 else -1)
            if previous_side and side and side != previous_side and not shot.crossing:
                add("error", "axis", index, f"камера перешла линию {a.name}–{b.name} без шота-перехода (crossing)")
            if side:
                previous_side = side
        # Left-to-right order of the named heroes; two of them seen again must keep it.
        order = sorted((n for n in shot.characters if n in on_screen), key=lambda n: on_screen[n]["screen_x"])
        if previous_order and not shot.crossing:
            common = [n for n in order if n in previous_order]
            if len(common) >= 2 and [n for n in previous_order if n in common] != common:
                add("error", "screen_swap", index, "герои поменялись сторонами экрана: " + " ↔ ".join(common))
        if len(order) >= 2:
            previous_order = order
        shots.append({"shot": index, "setup": setup.id, "frame": frame, "staging_line": staging_line(setup, frame),
                      "prompt_line": prompt_line(setup, frame, shot.characters), "lens_mm": setup.lens_mm,
                      "framing": shot.framing,
                      "subject_framing": on_screen[subject]["framing"] if subject in on_screen else "",
                      "axis_side": {1: "A", -1: "B", 0: "on_line"}.get(side, "")})
    order = {"error": 0, "warning": 1, "tip": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["shot"]))
    return {"ok": not any(f["severity"] == "error" for f in findings), "shots": shots, "findings": findings,
            "previz_svg": previz(plan)}


def previz(plan: Staging) -> str:
    """Top-down plan: actors with facing, the axis, every setup with its angle of view and shot numbers."""
    points = [(a.x, a.y) for a in plan.actors] + [(s.x, s.y) for s in plan.setups]
    min_x, max_x = min(p[0] for p in points) - 1, max(p[0] for p in points) + 1
    min_y, max_y = min(p[1] for p in points) - 1, max(p[1] for p in points) + 1
    scale = 600 / max(max_x - min_x, max_y - min_y)
    width, height = (max_x - min_x) * scale, (max_y - min_y) * scale
    px = lambda x: (x - min_x) * scale
    py = lambda y: height - (y - min_y) * scale  # +y up on the page
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" font-family="sans-serif" font-size="12">',
           f'<rect width="{width:.0f}" height="{height:.0f}" fill="#f6f5f1"/>']
    actors = {a.name: a for a in plan.actors}
    if plan.axis:
        a, b = actors[plan.axis[0]], actors[plan.axis[1]]
        dx, dy = unit(b.x - a.x, b.y - a.y)
        out.append(f'<line x1="{px(a.x - dx * 50):.0f}" y1="{py(a.y - dy * 50):.0f}" x2="{px(b.x + dx * 50):.0f}" '
                   f'y2="{py(b.y + dy * 50):.0f}" stroke="#c0392b" stroke-dasharray="6 5"/>')
    used = {}
    for index, shot in enumerate(plan.shots, 1):
        used.setdefault(shot.setup, []).append(index)
    for setup in plan.setups:
        tx, ty = (actors[setup.aim].x, actors[setup.aim].y) if isinstance(setup.aim, str) else setup.aim
        angle = math.atan2(tx - setup.x, ty - setup.y)
        half = math.atan(FRAME_MM[plan.aspect_ratio][0] / (2 * setup.lens_mm))
        reach = math.hypot(tx - setup.x, ty - setup.y) * 1.3
        wedge = [(setup.x + math.sin(angle + s * half) * reach, setup.y + math.cos(angle + s * half) * reach) for s in (-1, 1)]
        out.append(f'<polygon points="{px(setup.x):.0f},{py(setup.y):.0f} {px(wedge[0][0]):.0f},{py(wedge[0][1]):.0f} '
                   f'{px(wedge[1][0]):.0f},{py(wedge[1][1]):.0f}" fill="#2e6fbd" fill-opacity="0.12" stroke="#2e6fbd"/>')
        label = escape(f"{setup.id} · {round(setup.lens_mm)} мм · шоты {', '.join(map(str, used.get(setup.id, []))) or '—'}")
        out.append(f'<circle cx="{px(setup.x):.0f}" cy="{py(setup.y):.0f}" r="6" fill="#2e6fbd"/>'
                   f'<text x="{px(setup.x) + 9:.0f}" y="{py(setup.y) + 4:.0f}" fill="#1d3f6e">{label}</text>')
    for actor in plan.actors:
        r = max(5, actor.width_cm / 200 * scale)
        hx, hy = heading(actor.facing)
        color = {"hero": "#d35400", "prop": "#7f8c8d", "set": "#95a5a6"}[actor.kind]
        if actor.kind == "hero":
            out.append(f'<circle cx="{px(actor.x):.0f}" cy="{py(actor.y):.0f}" r="{r:.0f}" fill="{color}" fill-opacity="0.85"/>')
        else:  # a set piece or prop: its width across its facing, a third of that deep
            w, d = actor.width_cm / 100 * scale, max(8, actor.width_cm / 300 * scale)
            out.append(f'<rect x="{px(actor.x) - w / 2:.0f}" y="{py(actor.y) - d / 2:.0f}" width="{w:.0f}" height="{d:.0f}" '
                       f'fill="{color}" fill-opacity="0.6" transform="rotate({actor.facing:.0f} {px(actor.x):.0f} {py(actor.y):.0f})"/>')
        if actor.kind == "hero":
            out.append(f'<line x1="{px(actor.x):.0f}" y1="{py(actor.y):.0f}" x2="{px(actor.x + hx * 0.6):.0f}" '
                       f'y2="{py(actor.y + hy * 0.6):.0f}" stroke="{color}" stroke-width="3"/>')
        out.append(f'<text x="{px(actor.x) + r + 3:.0f}" y="{py(actor.y) - r:.0f}">{escape(actor.name)}</text>')
    out.append("</svg>")
    return "".join(out)


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/staging")

    @router.post("/compile")
    def compile_route(plan: Staging) -> dict:
        return compile_staging(plan)

    return router
