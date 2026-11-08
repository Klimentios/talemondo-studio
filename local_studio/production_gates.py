"""Production gates checked in store.create_job before a job reaches the GPU queue.

Three mistakes a check can see before the render cost hours of GPU time:
- a part slicer wrote duplicate and overlapping «[Shot N: a–b s]» lines and time references that belong to the
  neighbouring part; H3 drew exactly that — leaked actions, invented and reordered shots (10 prompts);
- (A/B routes queued in bulk — reskin, swap v1.5 — are gated in store.py by require_ab_request/experimental_reasons.)
- the same source part was rendered again and again after rejections, without anyone stopping.
"""
from __future__ import annotations

import re

from .production_review import load_reviews

# «[Shot 2: 0.86–1.83s]», «[Shot 2: 0.86 - 1.83 s]»
SHOT_RANGE = re.compile(r"\[Shot\s+(\d+)\s*:\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*s?\s*\]", re.I)
# «[Shot 2] At 00:03.500»
SHOT_AT = re.compile(r"\[Shot\s+(\d+)\]\s*At\s+(\d\d):(\d\d(?:\.\d+)?)", re.I)
# A time the shot text points at: «at 4.8 s», «around 3.9 s», «4.8–5.4 s», «(6.2s)»
TIME_REF = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:[–—-]\s*(\d+(?:\.\d+)?)\s*)?s(?:ec(?:onds?)?)?\b", re.I)
TOLERANCE_S = 0.1

SOURCE_ROLES = {"Исходное видео", "Движение", "Движение и камера", "Актёрская игра"}
REJECT_STREAK = 2
CONFIRM_KEY = "confirm_after_rejects"


def shot_cuts_to_at(prompt: str, duration: float | None = None) -> str:
    """Rewrite range-form shot tags into MiniMax's own cut timing before anything else reads the prompt.

    «[Shot 2: 1.63–3.40s] NOVA jumps» → «[Shot 2] At 00:01.630, the shot cuts: NOVA jumps»; a tag that starts at
    0 becomes plain «[Shot 1]». Measured (docs/reference/H3_ENCODER_RESEARCH.md, same seed): H3 does not read
    the range as a cut — a take cut +0.95/+0.64 s late with it, −0.05/−0.28 s with «At». Studio fixes
    the form itself; the timeline gate below still checks
    the numbers."""
    from .shot_contract import FRAMING

    if not SHOT_RANGE.search(prompt) or shot_timeline_problems(prompt, duration):
        return prompt  # a broken plan keeps its ranges so the timeline gate can name the overlap or overrun

    def rewrite(match: re.Match) -> str:
        number, start = int(match.group(1)), float(match.group(2))
        rest = match.group(4)
        if start <= TOLERANCE_S:
            return f"[Shot {number}]{rest}"
        minutes, seconds = divmod(round(start, 3), 60)
        at = f"[Shot {number}] At {int(minutes):02d}:{seconds:06.3f}, the shot cuts"
        text = re.sub(r"^\s*(?:cut\s+to\s+)", "", rest.lstrip(" :—–-"), flags=re.I)
        if FRAMING.match(text):  # a shot size: «… the shot cuts to a low close insert …»
            text = text[:1].lower() + text[1:]
            if not re.match(r"(?:an?|the)\s", text, re.I):
                text = ("an " if text[:1] in "aeiou" else "a ") + text
            return f"{at} to {text}"
        return f"{at}: {text}" if text else at

    return re.sub(SHOT_RANGE.pattern + r"([^\n\[]*)", rewrite, prompt, flags=re.I)


def _shot_lines(prompt: str) -> list[tuple[int, str]]:
    """Each «[Shot N…]» block with its own text up to the next shot tag or blank line."""
    tags = list(re.finditer(r"\[Shot\s+\d+[^\]]*\]", prompt, re.I))
    blocks = []
    for i, tag in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(prompt)
        body = prompt[tag.end():end].split("\n\n", 1)[0]
        blocks.append((tag.start(), tag.group(0) + body))
    return blocks


def shot_timeline_problems(prompt: str, duration: float | None) -> list[str]:
    """The shot plan must be one clean timeline inside the take: numbers 1, 2, 3…, no overlap, nothing past the end,
    no time reference in a shot that lies outside the take (it belongs to another part)."""
    found: list[str] = []
    ranges = [(int(n), float(a), float(b)) for n, a, b in SHOT_RANGE.findall(prompt)]
    starts = [(int(n), int(m) * 60 + float(s)) for n, m, s in SHOT_AT.findall(prompt)]
    limit = float(duration) + TOLERANCE_S if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
    # official IR: «[Shot 1]» has no «At» (it starts at 0), so the timed tags may begin with 2
    for kind, numbers in (("[Shot N: a–b s]", [n for n, _, _ in ranges]), ("[Shot N] At", [n for n, _ in starts])):
        first = numbers[0] if numbers and kind == "[Shot N] At" and numbers[0] == 2 else 1
        if numbers and numbers != list(range(first, first + len(numbers))):
            found.append(f"номера шотов {kind} идут не подряд с 1: {numbers} — задвоенный или потерянный шот")
    previous_end = None
    for number, start, end in ranges:
        if end <= start:
            found.append(f"шот {number}: конец {end:g} с не позже начала {start:g} с")
        if previous_end is not None and start < previous_end - TOLERANCE_S:
            found.append(f"шот {number} начинается в {start:g} с, а предыдущий идёт до {previous_end:g} с — шоты накладываются")
        if limit is not None and end > limit:
            found.append(f"шот {number} идёт до {end:g} с, а дубль {duration:g} с")
        previous_end = end if previous_end is None else max(previous_end, end)
    last = None
    for number, at in starts:
        if last is not None and at <= last:
            found.append(f"шот {number} начинается в {at:g} с, не позже предыдущего ({last:g} с)")
        if limit is not None and at >= limit:
            found.append(f"шот {number} начинается в {at:g} с, а дубль {duration:g} с")
        last = at
    if limit is not None:
        for _, block in _shot_lines(prompt):
            text = re.sub(r"^\[Shot[^\]]*\]\s*(?:At\s+\d\d:\d\d(?:\.\d+)?)?", "", block, flags=re.I)
            for match in TIME_REF.finditer(text):
                # «1950s» is a decade, not a time: no take is 100 s long
                values = [float(v) for v in match.groups() if v and float(v) < 100]
                if any(v > limit for v in values):
                    found.append(f"в тексте шота время «{match.group(0).strip()}» за пределами дубля {duration:g} с — "
                                 "это время соседней части; H3 нарисует её действие здесь")
                    break
    return found


def rejected_streak_problem(store, db, manifest: dict, settings: dict) -> str | None:
    """Two rejected renders in a row on the same source video stop the next one until a person confirms
    (settings.confirm_after_rejects): the third try of an unchanged idea is a habit, not a fix."""
    if settings.get(CONFIRM_KEY) is True:
        return None
    for video in manifest.get("videos", []):
        if video.get("role") not in SOURCE_ROLES:
            continue
        rows = db.execute(
            "SELECT job_id FROM jobs WHERE state='completed' AND manifest_json LIKE ? "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ?", (f'%"{video["asset_id"]}"%', REJECT_STREAK)).fetchall()
        if len(rows) < REJECT_STREAK:
            continue
        verdicts = []
        for row in rows:
            reviews = load_reviews(store, row["job_id"])
            verdicts.append(reviews[-1]["verdict"] if reviews else None)
        if all(v == "rejected" for v in verdicts):
            ids = ", ".join(row["job_id"][:8] for row in rows)
            return (f"по этому исходному видео {REJECT_STREAK} последних рендера отклонены ({ids}). Остановись: "
                    "найди причину в способе или задании, покажи пользователю и только с его согласия ставь "
                    f"следующий (settings.{CONFIRM_KEY}: true)")
    return None


def check(store, db, *, prompt: str, settings: dict, manifest: dict) -> None:
    problems = shot_timeline_problems(prompt, settings.get("duration"))
    from .take_timing import previous_part_drift  # plan vs MP4 of the last take of this source part (268d1d71)
    for problem in (rejected_streak_problem(store, db, manifest, settings),
                    previous_part_drift(store, db, manifest, settings)):
        if problem:
            problems.append(problem)
    from . import guide_cut_gate  # every source shot ≥ 1 s keyed by its own «Кадр сцены» (e91e701b)
    try:
        guide_cut_gate.check(store, settings=settings, manifest=manifest)
    except ValueError as error:
        problems.append(str(error))
    if problems:
        raise ValueError("Задание остановлено до GPU: " + "; ".join(problems[:6]))
