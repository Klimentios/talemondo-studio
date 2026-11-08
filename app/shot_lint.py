"""Cinematographer's check of a shot list before any keyframe or GPU render.

Pure rules, no model: shot sizes, 30°/two-size rule, axis and screen direction, eyelines,
establishing shots, motivated and varied camera movement, lens vs framing, look room,
reaction after a key action, transition pairs and rhythm. Values come from
app/director_schema.json (shot.* and lighting.*); scene-planner shots (camera_motion text,
framing wide/medium/close/insert) are accepted as well.

Severity: error = will read as a mistake on screen, fix before render; warning = usually
wrong, keep only on purpose (say so in `intent`); tip = craft suggestion.
"""
from __future__ import annotations

import statistics

from pydantic import BaseModel, ConfigDict, Field

from .director_config import SCHEMA

# Framing ladder: wider → closer. Steps between sizes drive the 30°/two-size rule.
LADDER = {"extreme_wide": 0, "wide": 1, "full": 2, "medium_full": 3, "two_shot": 3, "medium": 4,
          "over_shoulder": 5, "medium_close": 5, "close": 6, "extreme_close": 7, "insert": 7}
EMOTIONAL = {"reaction", "payoff"}
INTENTIONAL_JUMPS = {"jump_cut", "match_cut", "smash_cut", "morph", "dissolve", "fade"}
BLURRED = {"whip_pan", "pass_through", "morph"}  # need the same device on both sides of the cut
DARK_SCHEMES = {"low_key", "chiaroscuro", "rembrandt", "split", "silhouette"}


def _options(category: str, key: str) -> set[str]:
    field = next(f for f in SCHEMA["categories"][category] if f["key"] == key)
    return {o["value"] for o in field.get("options", [])}


class ShotCard(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(default="", max_length=120)
    duration: float = Field(default=5, gt=0, le=60)
    location: str = Field(default="", max_length=120)
    characters: list[str] = Field(default_factory=list, max_length=12)
    framing: str = ""
    angle: str = ""
    lens_mm: int | None = Field(default=None, ge=8, le=600)
    movement: str = ""          # schema movement value; free text accepted (camera_motion)
    camera_motion: str = ""     # scene planner field
    motivation: str = Field(default="", max_length=200)   # why the camera moves
    purpose: str = ""
    emotion: str = Field(default="", max_length=120)
    key_action: bool = False    # the beat others must react to
    axis_side: str = Field(default="", pattern="^(|A|B|crossing)$")
    screen_direction: str = Field(default="", pattern="^(|left_to_right|right_to_left|toward|away)$")
    eyeline: str = Field(default="", pattern="^(|left|right|camera|up|down)$")
    subject_side: str = ""
    look_room: str = ""
    composition: str = ""
    lighting_scheme: str = ""
    light_source: str = Field(default="", max_length=120)
    transition_in: str = ""
    transition_out: str = ""
    intent: str = Field(default="", max_length=200)  # deliberate rule break, explained


class ShotList(BaseModel):
    shots: list[ShotCard] = Field(min_length=1, max_length=60)
    target_seconds: float | None = Field(default=None, gt=0, le=600)
    max_job_seconds: float = Field(default=15, gt=0, le=60)


def _move(shot: ShotCard) -> str:
    text = (shot.movement or shot.camera_motion).strip().lower()
    if not text or text.startswith(("static", "locked")) or text in {"none", "статика"}:
        return "static"
    return text.split()[0].replace("-", "_")


def _finding(out, severity, rule, index, message, fix=""):
    out.append({"severity": severity, "rule": rule, "shot": index + 1,
                "message": message, **({"fix": fix} if fix else {})})


def _schema_values(shots, out):
    checks = (("framing", "shot", "framing"), ("angle", "shot", "angle"), ("purpose", "shot", "purpose"),
              ("composition", "shot", "composition"), ("subject_side", "shot", "subject_side"),
              ("look_room", "shot", "look_room"), ("transition_in", "shot", "transition_in"),
              ("transition_out", "shot", "transition_out"), ("lighting_scheme", "lighting", "scheme"))
    for i, shot in enumerate(shots):
        for attr, category, key in checks:
            value = getattr(shot, attr)
            if value and value not in _options(category, key):
                _finding(out, "error", "unknown_value", i, f"{attr}={value!r} нет в director_schema.json",
                         "возьми значение из схемы (shot.* / lighting.*)")


def _pairs(shots, out):
    for i in range(1, len(shots)):
        a, b = shots[i - 1], shots[i]
        same_place = bool(a.location) and a.location == b.location
        intended = b.transition_in in INTENTIONAL_JUMPS or bool(b.intent)
        size_a, size_b = LADDER.get(a.framing), LADDER.get(b.framing)
        same_angle = (a.angle or "eye") == (b.angle or "eye")
        if same_place and not intended and size_a is not None and size_a == size_b and same_angle \
                and _move(a) == _move(b) and set(a.characters) == set(b.characters):
            _finding(out, "error", "same_setup", i,
                     "тот же план подряд: крупность, ракурс и движение не меняются — склейка выглядит как скачок",
                     "смени крупность на 2 ступени или ракурс ≥30°, либо объедини в один шот")
        elif same_place and not intended and size_a is not None and size_b is not None \
                and abs(size_a - size_b) == 1 and same_angle:
            _finding(out, "warning", "thirty_degree", i,
                     f"соседняя крупность ({a.framing} → {b.framing}) при том же ракурсе даёт заметный скачок",
                     "правило 30°/2 крупностей: смени ракурс или шагни на две крупности")
        if same_place and not intended and size_a is not None and size_b is not None \
                and abs(size_a - size_b) >= 5 and not {a.purpose, b.purpose} & {"reveal", "detail"}:
            _finding(out, "warning", "size_leap", i,
                     f"прыжок {a.framing} → {b.framing} без среднего плана: зритель теряет связь",
                     "вставь средний план или оформи как смэш/матч-кат")
        if same_place and a.axis_side in {"A", "B"} and b.axis_side in {"A", "B"} and a.axis_side != b.axis_side:
            _finding(out, "error", "axis_jump", i, "камера перескочила ось 180°: герои поменяются местами на экране",
                     "оставь камеру на стороне " + a.axis_side + " или вставь шот с переходом через ось (axis_side=crossing)")
        if same_place and a.screen_direction in {"left_to_right", "right_to_left"} \
                and b.screen_direction in {"left_to_right", "right_to_left"} \
                and a.screen_direction != b.screen_direction and set(a.characters) & set(b.characters):
            _finding(out, "error", "screen_direction", i, "герой развернулся на экране между шотами",
                     "сохрани направление движения или покажи разворот в кадре")
        if same_place and len(a.characters) == 1 and len(b.characters) == 1 and a.characters != b.characters \
                and a.eyeline in {"left", "right"} and a.eyeline == b.eyeline:
            _finding(out, "warning", "eyeline", i, "в восьмёрке оба героя смотрят в одну сторону — не видно, что они смотрят друг на друга",
                     "взгляды должны быть встречными (left ↔ right)")
        if a.key_action and b.purpose not in EMOTIONAL and LADDER.get(b.framing, 0) < LADDER["medium_close"] \
                and not b.intent:
            _finding(out, "tip", "reaction_after_action", i,
                     "после ключевого действия нет реакции крупно — эмоция пропадает",
                     "следующим шотом покажи лицо того, кого это касается (purpose=reaction)")
        out_t, in_t = a.transition_out, b.transition_in
        if (out_t in BLURRED or in_t in BLURRED) and out_t != in_t:
            _finding(out, "error", "transition_pair", i,
                     f"переход не собран: конец предыдущего «{out_t or 'cut'}», начало «{in_t or 'cut'}»",
                     "вайп-пан, проход через предмет и морф пишутся в обоих шотах одинаково")


def _per_shot(shots, max_job, out):
    previous_place = None
    for i, shot in enumerate(shots):
        size, move = LADDER.get(shot.framing), _move(shot)
        new_place = shot.location and shot.location != previous_place
        if new_place and size is not None and size > LADDER["full"] \
                and shot.purpose not in {"establishing", "reveal", "detail"} and shot.transition_in not in {"match_cut", "morph"}:
            _finding(out, "warning", "no_establishing", i,
                     f"новая локация «{shot.location}» открывается {shot.framing}: зритель не понимает, где мы",
                     "начни с общего/установочного плана или явно задумай раскрытие (purpose=reveal)")
        previous_place = shot.location or previous_place
        if move != "static" and not shot.motivation and not shot.intent:
            _finding(out, "warning", "unmotivated_move", i, f"движение камеры «{move}» без причины",
                     "впиши motivation: за кем/чем идёт камера или что раскрывает; иначе статика")
        if shot.purpose in EMOTIONAL and shot.characters and size is not None and size <= LADDER["wide"]:
            _finding(out, "warning", "emotion_on_wide", i, "эмоциональный бит на общем плане — лица не прочитать",
                     "реакцию и развязку снимай средне-крупно или крупно")
        if shot.lens_mm and size is not None:
            if size >= LADDER["close"] and shot.lens_mm < 28 and not shot.intent:
                _finding(out, "warning", "wide_lens_face", i, f"крупный план на {shot.lens_mm} мм искажает лицо",
                         "для крупного 50–100 мм; широкий угол — только как приём (intent)")
            if size <= LADDER["wide"] and shot.lens_mm > 85:
                _finding(out, "tip", "long_lens_wide", i, f"общий план на {shot.lens_mm} мм сожмёт пространство",
                         "для географии сцены 18–35 мм")
        if shot.eyeline in {"left", "right"} and shot.subject_side == shot.eyeline and shot.look_room != "short_side":
            _finding(out, "warning", "look_room", i, "герой смотрит в ближний край кадра — нет места для взгляда",
                     "поставь героя на противоположную треть или задай look_room=short_side намеренно")
        if shot.lighting_scheme in DARK_SCHEMES and not shot.light_source:
            _finding(out, "tip", "light_motivation", i, f"схема «{shot.lighting_scheme}» без источника в сцене",
                     "назови источник: окно, лампа, экран — свет должен быть мотивирован")
        if shot.duration > max_job:
            _finding(out, "error", "too_long", i, f"{shot.duration:g} с длиннее одного задания H3 ({max_job:g} с)",
                     "раздели на два шота с запланированным стыком")
        elif shot.duration < 2:
            _finding(out, "warning", "too_short", i, f"{shot.duration:g} с — модель не успеет развить действие",
                     "генерируй длиннее и режь в монтаже")


def _sequence(shots, target, out):
    moves = [_move(s) for s in shots]
    run = 1
    for i in range(1, len(moves)):
        run = run + 1 if moves[i] == moves[i - 1] and moves[i] != "static" else 1
        if run == 3:
            _finding(out, "warning", "repeated_move", i, f"три шота подряд одно движение «{moves[i]}» — выглядит дёшево",
                     "чередуй статику, движение и смену ракурса")
    moving = [m for m in moves if m != "static"]
    if len(shots) >= 4 and moving and moving.count(max(set(moving), key=moving.count)) / len(shots) > 0.6:
        _finding(out, "warning", "monotone_camera", 0, "больше 60% шотов с одним и тем же движением камеры",
                 "энергию даёт монтаж и смена планов, а не один и тот же наезд")
    dutch = sum(1 for s in shots if s.angle == "dutch")
    if len(shots) >= 3 and dutch / len(shots) > 1 / 3:
        _finding(out, "warning", "dutch_overuse", 0, "заваленный горизонт в каждом третьем шоте теряет смысл",
                 "оставь dutch для одного пика тревоги")
    sizes = [LADDER[s.framing] for s in shots if s.framing in LADDER]
    if len(sizes) >= 4 and max(sizes) - min(sizes) <= 1:
        _finding(out, "warning", "flat_coverage", 0, "все шоты одной крупности — сцене не хватает общего и крупного",
                 "дай географию (общий) и эмоцию (крупный)")
    durations = [s.duration for s in shots]
    if len(durations) >= 4 and statistics.pstdev(durations) < 0.25:
        _finding(out, "tip", "flat_rhythm", 0, "все шоты одной длины — ритм метрический, без нарастания",
                 "укорачивай шоты к кульминации, держи паузу после развязки")
    if target and abs(sum(durations) - target) > max(2, target * 0.1):
        _finding(out, "warning", "runtime", 0, f"сумма шотов {sum(durations):g} с против цели {target:g} с",
                 "подгони длительности или число шотов")


def lint_shots(payload: ShotList | dict) -> dict:
    plan = payload if isinstance(payload, ShotList) else ShotList.model_validate(payload)
    out: list[dict] = []
    _schema_values(plan.shots, out)
    _per_shot(plan.shots, plan.max_job_seconds, out)
    _pairs(plan.shots, out)
    _sequence(plan.shots, plan.target_seconds, out)
    order = {"error": 0, "warning": 1, "tip": 2}
    out.sort(key=lambda f: (order[f["severity"]], f["shot"]))
    counts = {k: sum(1 for f in out if f["severity"] == k) for k in order}
    return {"ok": counts["error"] == 0, "counts": counts, "findings": out,
            "ladder": [LADDER.get(s.framing) for s in plan.shots],
            "seconds": round(sum(s.duration for s in plan.shots), 2)}
