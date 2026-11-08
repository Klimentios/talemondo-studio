"""The contract every scene take must meet, before and after the GPU.

Typical broken takes (scripts/take_scan.py): hard cuts and dissolves inside one take, a hero's reference picture
pasted on its studio backdrop, nothing happening. The model is not the cause; the prompts are:

1. Every reference was a standalone «<Picture N> NOVA face;» line. In the official MiniMax H3 schema a standalone
   <Picture N> is "a concrete target frame or shot-planning anchor", so H3 showed the pictures as frames. Hero, place
   and object pictures belong inside a <Subject N> line; only the start frame stands alone (app/h3_ir.build_skeleton).
2. Pictures were sent that the prompt never described (extra «Окружение» files).
3. One take asked for montage: "wide → close → insert", "cut on action", several [Shot N]; H3 obeyed with cuts.
4. The action was 2–3 sentences under 150 words of style boilerplate and "never …" rules; the guide asks for
   350–500 words of composition, positions, light, action and camera. Negations name the thing they forbid.
5. A scene-wide rule (push into the hero's eyes) went into every shot, so every take dived into the hero's eyes.

normalize() rewrites what is mechanical (1, 2, 5, the negations); problems() rejects what needs a writer (3, 4).
"""
from __future__ import annotations

import re

from app.h3_ir import (AUDIO_ROLES, CONTINUATION_ROLE, MOTION_VIDEO_ROLES, STRUCTURE_VIDEO_ROLES, Reference,
                       _kept_description, build_skeleton, is_ir_prompt, parse_ir, render_ir)

MIN_TAKE_S = 10  # H3 makes clean 10–15 s takes; 4–6 s takes came back as montage
MIN_ACTION_WORDS = 45  # words of sentences where a named hero acts: who is where, sees, decides, does, ends
FRAMING = re.compile(r"^\s*(?:an?\s+|the\s+)?(?:\d+\s*mm\s+)?(?:static\s+|slow\s+|handheld\s+|locked[- ]off\s+)?"
                     r"(?:extreme\s+)?(?:wide|medium|close|full|long|two|over|low|high|eye|bird|tight|establishing|"
                     r"aerial|top|side|front|three-quarter|cowboy|knee|waist|insert|macro|tracking|dolly|crane|"
                     r"overhead|dutch|worm|point-of-view|pov)", re.I)
MONTAGE = re.compile(r"\b(?:cuts?\s+(?:to|back|away)|smash[- ]cut|match[- ]cut|jump[- ]cut|hard\s+cut|cut\s+on\s+action|"
                     r"whip[- ]pan\s+to|insert\s+shot|reverse\s+shot|then\s+we\s+see|montage|split[- ]?screen|"
                     r"side[- ]by[- ]side\s+(?:panels?|frames?|images?|pictures?|shots?|views?)|picture[- ]in[- ]picture|"
                     r"(?:split|comic|two)\s+panels?)\b", re.I)
NEGATIVE = re.compile(r"\b(?:never|no|not|without|avoid|don't|do not)\b", re.I)
FANTASY = re.compile(r"\b(?:fantas\w*|imagin\w*|daydream\w*|dream\w*|transforms?)\b", re.I)


LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l",
               "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _names(body: dict) -> set[str]:
    """Owner names as the English prompt spells them: ВОЛЬТ is Volt, БЕЛЛ is Bell."""
    names = set()
    for owner in (body.get("reference_owners") or {}).values():
        name = str(owner or "").casefold().strip()
        if name:
            names |= {name, "".join(LATIN.get(ch, ch) for ch in name)}
    # Heroes the prompt itself defines («<Subject 1> is ALEX.»), so a take without owner pictures is still read.
    for name in re.findall(r"<Subject \d+> is (?!the environment)([A-Z][A-Za-z'-]+)", str(body.get("prompt") or "")):
        names.add(name.casefold())
    return names


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def _kind(role: str) -> str:
    if role in AUDIO_ROLES:
        return "audio"
    return "video" if role in MOTION_VIDEO_ROLES or role in STRUCTURE_VIDEO_ROLES or role == CONTINUATION_ROLE else "image"


def _references(body: dict) -> list[Reference]:
    """The job's files numbered per kind, as the GPU numbers them (store._check_official_prompt).
    Counting every file as a picture, a hero's voice file took a <Picture N> and moved every picture after it."""
    ids = body.get("asset_ids") or []
    roles, owners = body.get("reference_roles") or {}, body.get("reference_owners") or {}
    kinds = {asset: _kind(roles.get(asset, "")) for asset in ids}
    videos = [a for a in ids if kinds[a] == "video" and roles.get(a) != CONTINUATION_ROLE]
    offset = len(videos) if (body.get("settings") or {}).get("use_video_audio") else 0
    count = {"image": 0, "video": 0, "audio": offset}
    refs = []
    for asset in ids:
        kind, role = kinds[asset], roles.get(asset, "")
        if role != CONTINUATION_ROLE:
            count[kind] += 1
        refs.append(Reference(kind, count[kind], role, owners.get(asset, "")))
    return refs


def _speaking_order(body: dict, prompt: str) -> list[str]:
    """The job's heroes in the order of their (S1), (S2) in the take (dialogue() numbers them as they speak), so the voice
    file's «<Audio N> is the voice-timbre reference for <Subject M> (S1)» names the hero who says the (S1) lines."""
    found = {}
    for owner in set((body.get("reference_owners") or {}).values()):
        match = re.search(rf"(?<![\w<]){re.escape(owner)} \(S(\d+)\)", prompt, re.I) if owner else None
        if match:
            found[int(match.group(1))] = owner
    # A speaker without files of their own (a stranger) keeps their number: the placeholder matches no owner.
    return [found.get(n, f"#S{n}") for n in range(1, max(found, default=0) + 1)]


# Generic blocks Studio appends to every prompt (model.mjs SCALE_CONTINUITY_DIRECTION, store.py bindings, director
# presets). In a 5 s take they were ~70 % of the text and named "every frame and cut", "its adult owner", "adult men and
# adult women": a cat-only shot of S01E03 got a stranger and another cat. The skeleton and the shot text replace them.
BOILERPLATE = re.compile(r"(?ms)^(?:SUBJECT SCALE AND HEIGHT CONTINUITY|DIRECTOR STATE —[^\n]*|VOICE PERFORMANCE DIRECTION|"
                         r"EMOTIONAL PERFORMANCE DIRECTION|VIDEO PERFORMANCE DIRECTION|AUDIO DIRECTION|VISUAL DIRECTION|"
                         r"REFERENCE ROLE CONTRACT|CHARACTER REFERENCE BINDINGS):.*?(?=\n\s*\n|\Z)")
SCENE_STYLE = re.compile(r"(?m)^Scene style, identical in every shot of this scene:\s*(.+)$")
TAG = re.compile(r"\[(Style|Identity|Shot \d+|Camera|Dialogue|Sound|Action|Lighting|Light)\]\s*", re.I)


def to_ir(body: dict) -> bool:
    """Turn a prose, tagged or integrated_multimodal prompt into the six official sections; True when converted."""
    prompt = str(body.get("prompt") or "")
    if is_ir_prompt(prompt):
        return False
    style = " ".join(m.group(1).strip().rstrip(".") + "." for m in SCENE_STYLE.finditer(prompt))
    text = BOILERPLATE.sub("", SCENE_STYLE.sub("", prompt))
    text = re.sub(r"(?m)^(?:integrated_multimodal_description|overall_soundscape|non_diegetic_music):\s*", "\n[\\g<0>]", text)
    sound, music = "", ""
    parts = re.split(r"\n\[(overall_soundscape|non_diegetic_music):\s*\]", text)
    text = parts[0]
    for name, chunk in zip(parts[1::2], parts[2::2]):
        sound, music = (chunk.strip(), music) if name == "overall_soundscape" else (sound, chunk.strip())
    text = re.sub(r"\n\[integrated_multimodal_description:\s*\]", "\n", text)
    pieces, at, current = {"style": [], "shot": [], "camera": [], "dialogue": [], "sound": []}, 0, "style"
    for match in TAG.finditer(text):
        pieces[current].append(text[at:match.start()])
        name = match.group(1).lower()
        current = ("shot" if name.startswith(("shot", "action")) else "camera" if name == "camera" else
                   "dialogue" if name == "dialogue" else "sound" if name == "sound" else
                   "identity" if name == "identity" else "style")
        pieces.setdefault(current, [])
        at = match.end()
    pieces[current].append(text[at:])
    join = lambda key: re.sub(r"\s+", " ", " ".join(pieces.get(key, []))).strip()
    preamble, shot = join("style"), join("shot")
    if not shot:  # untagged prose: the style is the first sentence, the rest is the take
        sentences = _sentences(preamble)
        preamble, shot = (sentences[0] if sentences else ""), " ".join(sentences[1:])
    shot = re.sub(r"^At\s+00:00(?:\.0+)?\s*", "", shot)
    action = " ".join(filter(None, [shot, join("camera"), join("dialogue")]))
    sections = {"subject_definitions": "", "summary": " ".join(_sentences(preamble)[:1]),
                "retention_analysis": "", "detailed_description": " ".join(filter(None, [style, preamble]))
                + "\n\n[Shot 1] " + action,
                "overall_soundscape": sound or join("sound") or "Natural synchronized sound of the visible action.",
                "non_diegetic_music": music or "None."}
    body["prompt"] = render_ir(sections)
    return True


def _split_description(text: str) -> tuple[str, str]:
    """(scene-wide preamble, the take's own words from [Shot 1] on)."""
    at = text.find("[Shot 1]")
    return (text, "") if at < 0 else (text[:at], text[at:])


# Shot size (app/director_schema.json shot.framing) → (words, kind): a size adjective goes before the angle and «shot»
# («a wide eye-level shot»), a framing noun after the angle («an eye-level close-up», «a low-angle two-shot»).
FRAMING_WORDS = {"extreme_wide": ("extreme wide", "size"), "wide": ("wide", "size"), "full": ("full", "size"),
                 "medium_full": ("medium-full", "size"), "medium": ("medium", "size"),
                 "medium_close": ("medium close-up", "noun"), "close": ("close-up", "noun"),
                 "extreme_close": ("extreme close-up", "noun"), "two_shot": ("two-shot", "noun"),
                 "over_shoulder": ("over-the-shoulder shot", "noun"), "point_of_view": ("point-of-view shot", "noun"),
                 "insert": ("insert close-up", "noun")}
# shot.angle as a cinematographer says it before a shot noun (not «a wide worm shot», «a close-up dutch shot»).
ANGLE_WORDS = {"eye": "eye-level", "low": "low-angle", "high": "high-angle", "overhead": "overhead",
               "dutch": "Dutch-angle", "worm": "worm's-eye"}
_DIRECTIONS = {"forward": "in", "backward": "out", "side": ""}
# A move the schema allows without the direction camera_words needs: plain words, never a guessed side.
_UNDIRECTED = {"pan": "pans across the scene", "tilt": "tilts", "truck": "trucks sideways", "pedestal": "cranes",
               "zoom": "zooms", "roll": "rolls"}


def camera_move(shot: dict) -> tuple[str, str]:
    """(app/camera_words move, direction) of the shot's camera_motion + camera_direction (schema movement.*)."""
    move = str(shot.get("camera_motion") or "").strip().lower().replace(" ", "_").replace("-", "_")
    direction = str(shot.get("camera_direction") or "").strip().lower()
    direction = _DIRECTIONS.get(direction, direction)
    if move in {"", "static", "locked", "hold", "none"}:
        return "static", ""
    if move in {"dolly", "dolly_in", "push", "push_in"}:
        return (("pull", "out") if direction == "out" else ("truck", direction) if direction in {"left", "right"}
                else ("push", "in"))
    if move in {"dolly_out", "pull", "pull_out"}:
        return "pull", "out"
    if move in {"truck", "slider"}:
        return "truck", direction
    if move in {"crane", "pedestal", "jib"}:
        return "pedestal", direction
    if move in {"orbit", "arc"}:
        return "arc", direction if direction in {"left", "right"} else ""
    if move == "handheld":
        return "shake", "slight"
    if move in {"tracking", "track", "follow"}:
        return "tracking", ""
    return move, direction


def camera_sentence(shot: dict) -> str:
    """The shot's move in H3's trained camera words (app/camera_words, guide §4.3): «The camera pushes in at slow
    speed.» Speed from shot.camera_speed (none/slow/medium/fast; a moving shot without it stays slow, as Studio wrote
    it before), amplitude from shot.camera_amplitude (small/medium/large)."""
    from app.camera_words import AMPLITUDES, camera_phrase

    move, direction = camera_move(shot)
    speed = str(shot.get("camera_speed") or "slow").strip().lower()
    speed = "" if speed in {"none", "medium", "normal"} else speed if speed in {"slow", "fast"} else "slow"
    amplitude = str(shot.get("camera_amplitude") or "").strip().lower()
    amplitude = amplitude if amplitude in AMPLITUDES else ""
    try:
        return camera_phrase(move, direction, "" if move == "static" else speed, amplitude)
    except ValueError:
        verb = _UNDIRECTED.get(move)
        if not verb:
            return ""
        return " ".join(filter(None, [f"The camera {verb}", f"at {speed} speed" if speed else ""])) + "."


def opening(shot: dict, *, camera: bool = True) -> str:
    """'A wide eye-level shot on a 35 mm lens, one continuous take. The camera pushes in at slow speed.' from the
    storyboard fields of the shot. camera=False leaves the move to the writer's own camera sentence (one move, once)."""
    framing, kind = FRAMING_WORDS.get(str(shot.get("framing") or ""), ("", ""))
    if not framing:
        return ""
    angle = ANGLE_WORDS.get(str(shot.get("angle") or ""), "")
    words = (f"{framing} {angle} shot" if angle else f"{framing} shot") if kind == "size" else \
        " ".join(filter(None, [angle, framing]))
    article = "an" if words[:1].lower() in "aeiou" else "a"
    lens = shot.get("lens_mm")
    lens = (f"{lens:g}" if isinstance(lens, (int, float)) else str(lens or "").strip()) if lens else ""
    article = "an" if re.match(r"8|1[18](?!\d)", lens or "") else "a"  # no backslash inside an f-string: Python 3.11
    lens = f" on {article} {lens} mm lens" if lens else ""
    move = camera_sentence(shot) if camera else ""
    text = f"{article} {words}{lens}, one continuous take." + (" " + move if move else "")
    return re.sub(r"\s+", " ", text[0].upper() + text[1:])


def staging_sentence(shot: dict) -> str:
    """The shot's staging line (staging_compile → sceneShot.staging_line): who is where, screen side, size in frame."""
    return _sentence(re.sub(r"\s+", " ", str((shot or {}).get("staging_line") or "")).strip())


# Camera moves as a writer names them in take.camera, by app/camera_words family.
CAMERA_TEXT = {
    "static": r"\bstatic\b|\bhold(?:s|ing)?\s+(?:\w+\s+)?(?:still|steady|static|fixed)\b|\block(?:ed)?[- ]off\b|"
              r"\b(?:stays?|remains?)\s+(?:still|fixed|locked)\b|\bmotionless\b|\bdoes(?:\s+not|n't)\s+move\b",
    "push": r"\bpush(?:es|ing)?\b|\bdoll(?:y|ies|ying)\s+(?:in|forward)\b|\b(?:moves?|creeps?|drifts?)\s+(?:in|closer|forward)\b",
    "pull": r"\bpull(?:s|ing)?\s+(?:out|back)\b|\bdoll(?:y|ies|ying)\s+(?:out|back)\b|\b(?:backs?|drifts?)\s+(?:away|off|back)\b|"
            r"\bmoves?\s+back(?:ward)?\b",
    "zoom": r"\bzoom",
    "pan": r"\bpan(?:s|ning|ned)?\b",
    "tilt": r"\btilt",
    "truck": r"\btruck|\bcrab|\bslider\b|\bslides?\s+(?:left|right|sideways)\b",
    "tracking": r"\btrack(?:s|ing)?\b|\bfollow(?:s|ing)?\b",
    "arc": r"\borbit|\barcs?\b|\bcircles?\b",
    "pedestal": r"\bcran(?:e|es|ing)\b|\bjib\b|\bpedestal|\bcamera\s+(?:slowly\s+)?(?:rises|descends|lifts|lowers)\b|\bbooms?\b",
    "shake": r"\bhandheld\b|\bhand-held\b|\bshak",
    "roll": r"\brolls?\s+(?:clockwise|counter)",
}
# Families one take may name together without contradicting the shot's move.
_COMPATIBLE = {"tracking": {"truck", "push", "pull"}, "truck": {"tracking"}, "push": {"tracking"}, "pull": {"tracking"},
               "pedestal": {"tilt"}}


def camera_families(text: str) -> set[str]:
    return {family for family, pattern in CAMERA_TEXT.items() if re.search(pattern, str(text or ""), re.I)}


def camera_text_conflicts(take: dict | None, shot: dict | None) -> list[str]:
    """take.camera free text against the shot's camera_motion: «holds still» on a push, a pan on a static shot.
    H3's deep blocks weight camera words most (attention probe): two moves in one take are a coin toss."""
    said = camera_families((take or {}).get("camera") if isinstance(take, dict) else "")
    if not said or not (shot or {}).get("camera_motion"):
        return []
    move, _ = camera_move(shot or {})
    if move == "shake" or "shake" in said and len(said) == 1:
        return []  # handheld goes with any move
    said.discard("shake")
    if move == "static":
        moving = sorted(said - {"static"})
        return [f"take.camera называет движение ({', '.join(moving)}), а camera_motion шота — static: "
                "оставь одно — поправь camera_motion или текст"] if moving else []
    if "static" in said and not (said - {"static"}):
        return [f"take.camera говорит, что камера стоит, а camera_motion шота — {shot.get('camera_motion')}: "
                "оставь одно — поправь camera_motion или текст"]
    if said and move not in said and not (said & _COMPATIBLE.get(move, set())):
        return [f"take.camera называет {', '.join(sorted(said))}, а camera_motion шота — {shot.get('camera_motion')}: "
                "H3 получит два разных движения — поправь camera_motion или текст"]
    return []


def _camera_text(take_camera: str, shot: dict) -> tuple[str, bool]:
    """(the writer's camera sentences to keep, whether the opening still says the trained move).

    One move, once. A writer sentence that only restates the move («The camera holds a static side shot») goes: the
    trained words say it. One that adds where or why («pushes slowly into NOVA's right eye») stays, and then the
    opening leaves the move out. Sentences about focus or light that name no move stay as they are."""
    kept, trained = [], True
    for sentence in _sentences(_sentence(take_camera)):
        if not camera_families(sentence):
            kept.append(sentence)
            continue
        rest = sentence
        for pattern in CAMERA_TEXT.values():
            rest = re.sub(pattern, " ", rest, flags=re.I)
        content = [w for w in re.findall(r"[A-Za-z'’]+", rest) if w.casefold() not in CAMERA_FILLER]
        if len(content) >= 2:
            kept.append(sentence)
            trained = False
    return " ".join(kept), trained


CAMERA_FILLER = {"the", "a", "an", "camera", "shot", "slow", "slowly", "fast", "quickly", "gently", "steady", "steadily",
                 "static", "and", "in", "out", "on", "at", "speed", "move", "moves", "movement", "side", "wide", "medium",
                 "close", "two", "it", "this", "throughout", "take", "whole", "entire", "smoothly", "smooth", "small",
                 "large", "amplitude", "with", "of", "left", "right", "up", "down", "is", "stays", "still", "holds",
                 "locked", "frame", "shots", "level", "eye", "angle", "lens", "mm", "one", "continuous"}


def _forbids(sentence: str) -> bool:
    return bool(NEGATIVE.search(sentence) and (MONTAGE.search(sentence) or re.search(
        r"reference picture|backdrop|studio|caption|logo|text|writing|cut|insert", sentence, re.I)))


def normalize(body: dict, shot: dict | None = None) -> list[str]:
    """Rewrite the mechanical parts of a take's prompt in place; returns what was changed, in Russian."""
    changed = ["промпт переведён в официальный формат H3 без общих блоков Studio"] if to_ir(body) else []
    prompt = str(body.get("prompt") or "")
    prompt = BOILERPLATE.sub("", SCENE_STYLE.sub(lambda m: m.group(1), prompt))
    sections = parse_ir(prompt)
    skeleton = build_skeleton(_references(body), speaking_owners=_speaking_order(body, prompt))
    written = [line.strip() for line in sections.get("subject_definitions", "").splitlines() if line.strip()]
    lines = []
    for line in skeleton.subject_lines:  # Studio's binding of pictures + the writer's own words about the subject
        name = re.match(r"<Subject \d+> is (?:the (environment|object))?([^,.:]*)", line)
        own = ""
        if name and name.group(2).strip() and not name.group(1):
            hero = name.group(2).strip()
            mine = next((w for w in written if re.match(rf"<Subject \d+> is {re.escape(hero)}\b", w, re.I)), "")
            own = _kept_description(mine.split(hero, 1)[1]) if mine else ""
        elif name and name.group(1) == "environment":
            mine = next((w for w in written if re.match(r"<Subject \d+> is the (?:environment|location|place)\b", w, re.I)), "")
            own = _kept_description(re.sub(r"^<Subject \d+> is the (?:environment|location|place)", "", mine, flags=re.I)) if mine else ""
        lines.append(line.rstrip(".") + "." + own if own else line)
    subjects = "\n".join(lines).replace("[SHOTS]", "[Shot 1]")
    retention = "\n".join(skeleton.retention_lines).replace("[SHOTS]", "[Shot 1]")
    if sections.get("subject_definitions", "").strip() != subjects:
        sections["subject_definitions"], sections["retention_analysis"] = subjects, retention
        changed.append("subject_definitions и retention_analysis собраны из ролей референсов")
    summary = sections.get("summary", "")
    kinds = " + ".join(skeleton.task_types) or "reference generation"
    clean = [s for s in _sentences(re.sub(r"^\s*\[[^\]]*\]\s*", "", summary)) if not re.search(r"\bPictures?\s+\d", s)]
    new_summary = f"[{kinds}] " + " ".join(clean)
    if new_summary.strip() != summary.strip():
        sections["summary"] = new_summary
        changed.append("summary без ссылок на номера картинок")
    preamble, action = _split_description(sections.get("detailed_description", ""))
    fantasy_take = bool(FANTASY.search(action))
    kept = []
    for sentence in _sentences(preamble):
        if len(re.findall(r"<Picture \d+>", sentence)) > 1 or re.search(r"<Picture \d+>\s+[A-ZА-Я]{2,}", sentence):
            continue  # the hand-written «<Picture 1> ALEX face; <Picture 2> …» list: the skeleton says it right
        if MONTAGE.search(sentence) or NEGATIVE.search(sentence):
            continue  # "never a split screen…" names the split screen; the take is one shot by construction
        if not fantasy_take and FANTASY.search(sentence):
            continue  # the fantasy push-in belongs only to the take that shows the fantasy
        kept.append(sentence)
    if len(kept) != len(_sentences(preamble)):
        changed.append("из общей части описания убраны запреты, слова о склейках и чужая фантазия")
    if action:
        words = _sentences(action.removeprefix("[Shot 1]"))
        own = [w for w in words if not _forbids(w)]
        start = opening(shot or {}, camera=not any(camera_families(s) for s in own if re.search(r"(?i)\bcamera\b", s)))
        added = 0
        if start and not FRAMING.match(" ".join(own)):
            own.insert(0, start)
            added += 1
            changed.append("начало шота: крупность, ракурс, объектив и движение из раскадровки")
        line = staging_sentence(shot or {})
        if line and line not in " ".join(own):  # the staging is persisted per shot (sceneShot.staging_line)
            at = 1 if own and FRAMING.match(own[0]) else 0
            while at < len(own) and re.match(r"(?i)\s*the camera\b", own[at]):
                at += 1
            own.insert(at, line)
            added += 1
            changed.append("мизансцена шота (кто где в кадре) из постановки")
        if len(own) != len(words) + added:
            changed.append("из действия убраны запреты, которые называют брак")
        action = label_heroes("[Shot 1] " + " ".join(own), skeleton)
    for name in ("overall_soundscape", "non_diegetic_music"):
        if name in sections:
            sections[name] = " ".join(s for s in _sentences(sections[name]) if not _forbids(s)) or "None."
    sections["detailed_description"] = (" ".join(kept) + "\n\n" + action).strip() if action else " ".join(kept)
    body["prompt"] = render_ir(sections)
    return changed


def label_heroes(action: str, skeleton) -> str:
    """ref-en.txt §5.3–5.4: a hero's <Subject N> at their first appearance and at each vocal event «<Subject 2> (S1)».
    Numbers come from the launch skeleton (reference order), not from the take's cast order."""
    for line in skeleton.subject_lines:
        match = re.match(r"(<Subject \d+>) is ([^,.:<]+?), whose", line)
        if not match:
            continue
        label, name = match.group(1), match.group(2).strip()
        first = re.search(rf"(?<![\w<]){re.escape(name)}\b(?!['’]s| \(S\d)", action)
        if first and f"({label})" not in action:
            action = action[:first.end()] + f" ({label})" + action[first.end():]
        action = re.sub(rf"(?<![\w<]){re.escape(name)} (\(S\d+\))", rf"{label} \1", action)
    return action


def problems(body: dict) -> list[str]:
    """What only a writer can fix; any item stops the take before the GPU."""
    prompt = str(body.get("prompt") or "")
    if not is_ir_prompt(prompt):
        return ["промпт не в официальном формате H3 (шесть секций)"]
    sections = parse_ir(prompt)
    found = []
    russian = sorted({m.group(0).strip() for m in re.finditer(r"[А-Яа-яЁё][А-Яа-яЁё .,:;«»-]*", prompt)})
    if russian:  # every franchise video is English; H3 reads English prompts (Studio role names, owners, labels leaked)
        found.append("русский текст в промпте: " + ", ".join(f"«{item[:30]}»" for item in russian[:5]))
    definitions = sections.get("subject_definitions", "")
    for line in definitions.splitlines():
        line = line.strip()
        if re.match(r"<Picture \d+>", line) and "first frame" not in line:
            found.append(f"отдельная строка картинки «{line[:60]}» — H3 покажет её как кадр")
    described = {int(n) for n in re.findall(r"<Picture (\d+)>", definitions)}
    for ref in _references(body):
        if ref.kind == "image" and ref.number not in described:
            found.append(f"картинка {ref.number} ({ref.role or 'без роли'}) не описана в subject_definitions")
    _, action = _split_description(sections.get("detailed_description", ""))
    if not action:
        return found + ["в detailed_description нет [Shot 1] с действием дубля"]
    duration = float((body.get("settings") or {}).get("duration") or 0)
    if duration and duration < MIN_TAKE_S:
        found.append(f"дубль {duration:g} с: H3 делает ровные ролики по 10–15 с, а в 4–6 с втискивает монтаж; "
                     "объедини соседние шоты сцены в один дубль 10–15 с")
    later = re.findall(r"\[Shot (\d+)\]\s*(?:At\s+(\d\d):(\d\d(?:\.\d+)?))?", action)
    times = [int(m) * 60 + float(sec) for _, m, sec in later[1:] if m]
    if len(later) > 1 and (len(times) != len(later) - 1 or times != sorted(set(times))
                           or (duration and times and times[-1] >= duration - 1)):
        found.append("склейки внутри дубля только в официальном виде: [Shot 2] At 00:05.000, … — время растёт и "
                     "кончается раньше конца ролика")
    plain = re.sub(r"\[Shot \d+\]\s*At\s+\d\d:\d\d(?:\.\d+)?,?\s*(?:the\s+shot\s+)?(?:cuts?\s+to\s+)?", " ", action)
    montage = sorted({m.group(0).lower() for m in MONTAGE.finditer(plain)})
    if montage:
        found.append("монтаж внутри дубля: " + ", ".join(montage) + " — склейки только между шотами")
    names = _names(body)
    acting = [sentence for sentence in _sentences(re.sub(r"<d>.*?</d>", " ", action.removeprefix("[Shot 1]"), flags=re.S))
              if not FRAMING.match(sentence) and not re.match(r"\s*(?:Camera|Music|Sound|Light)\s*:", sentence, re.I)
              and (any(name in sentence.casefold() for name in names)
                   or re.search(r"\b(?:he|she|they|his|her|their|him|them)\b", sentence, re.I))]
    words = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", " ".join(acting)))
    if words < MIN_ACTION_WORDS * max(1, len(later)):
        found.append(f"игра героев {words} слов (нужно ≥ {MIN_ACTION_WORDS}): кто где стоит, что видит, что "
                     "решает и делает, чем кончается дубль")
    heroes_in_view = {name for name in names if re.search(rf"\b{re.escape(name)}\b", action.casefold())}
    if names and not heroes_in_view:
        found.append("в действии нет ни одного героя, а их фото приложены — H3 вставит портрет вместо действия; "
                     "либо герой в кадре, либо шот без фото героев")
    if not FRAMING.match(action.removeprefix("[Shot 1]")):
        found.append("[Shot 1] должен начинаться с крупности и ракурса открытия (A wide eye-level shot …)")
    negatives = len(NEGATIVE.findall(re.sub(r"<d>.*?</d>", " ", action, flags=re.S)))
    if negatives > 2:
        found.append(f"{negatives} отрицаний в действии: H3 рисует то, что названо; пиши, что происходит")
    return found


def prompt_warnings(prompt: str) -> list[str]:
    """Advice that does not stop the take: rules measured on one scene and one seed and not
    yet confirmed on real franchise content. Promote a rule to problems() only after an A/B on real content."""
    if not is_ir_prompt(prompt):
        return []
    _, action = _split_description(parse_ir(prompt).get("detailed_description", ""))
    return size_action_conflicts(action) + optics_light_warnings(action)


# Control map (docs/reference/H3_ENCODER_RESEARCH.md, same seed): a static size holds only when the shot's
# actions read in it. «Extreme close-up of his eyes» + hands on a rope / wiping the face came out as a medium shot;
# the same close-up with eye-only acting held exactly. «Wide from the bow» + detailed facial acting came out medium;
# the same wide with a body gesture held. A camera move to the size (pushes in to…) is not affected.
FACE_SIZE = re.compile(r"(?<!medium\s)(?<!medium-)\b(?:extreme\s+close[- ]?up|close[- ]?up|ecu)\b[^.]*?"
                       r"\b(?:eyes?|face|brows?|lips|mouth|muzzle)\b", re.I)
WIDE_SIZE = re.compile(r"\b(?:(?:extreme\s+)?wide(?:\s+establishing)?\s+(?:shot|view)|establishing\s+shot|long\s+shot|"
                       r"(?:extreme\s+)?wide\s+from)\b", re.I)
# Only camera moves count: «he moves closer» or «she pulls back her hand» is acting, not a size change.
SIZE_MOVE = re.compile(r"\b(?:camera\s+(?:\w+\s+){0,2}?(?:moves?|push(?:es)?|pulls?|dollies|zooms?|tracks?|cranes?|orbits?)|"
                       r"push(?:es|ing)?\s+in|pull(?:s|ing)?\s+(?:out|back)\s+to|dolly(?:\s+in|\s+out)?|"
                       r"zoom(?:s|ing)?\s+(?:in|out)|orbits?|cranes?\s+(?:up|down))\b", re.I)
# Verbs, not bare nouns: «his hands stay out of frame» is not an action; «raises his brows» is face acting.
BODY_ACTION = re.compile(r"\b(?:grip(?:s|ping)?|wipes?|lurch\w*|kneels?|climbs?|"
                         r"(?:he|she|they|it|[A-Z]{2,})\s+(?:walks?|runs?|jumps?|stands?\s+up|waves?|sits?\s+(?:up|down))|"
                         r"(?:raises?|lifts?|waves?)\s+(?:his|her|their|its|one|both)\s+(?:hands?|arms?|paws?)|"
                         r"(?:with|in)\s+(?:his|her|their|its|both)\s+(?:hands?|paws?)|"
                         r"pulls?\s+(?:the|a|on)|holds?\s+(?:the|a)\s+(?!gaze\b|look\b|breath\b|stare\b)\w+)\b")
FACE_ACTION = re.compile(r"\b(?:eyes?\s+(?:widen|narrow|soften|fill|crinkle)\w*|brows?|lips?\s+\w+|jaw|nostrils?|"
                         r"tears?\s+(?:well|roll|stream|fill)\w*|blinks?|breath\s+catches|smile\w*|smirk\w*|frown\w*|"
                         r"wince\w*|whiskers?\s+twitch\w*)\b", re.I)


def size_action_conflicts(action: str) -> list[str]:
    """Shots whose static size cannot show their own actions (H3 widens or narrows the frame instead)."""
    found = []
    parts = re.split(r"(?=\[Shot \d+\])", action)
    for part in parts:
        if not part.strip():
            continue
        label = (re.match(r"\[Shot (\d+)\]", part) or [None, "1"])[1]
        text = re.sub(r"<d>.*?</d>", " ", part, flags=re.S)
        if SIZE_MOVE.search(text):
            continue
        if FACE_SIZE.search(text):
            body = sorted({m.group(0).lower() for m in BODY_ACTION.finditer(text)})
            if body:
                found.append(f"[Shot {label}] крупный план лица/глаз, а в шоте действия рук и тела ({', '.join(body[:4])}): "
                             "H3 расширит кадр до среднего. Оставь в шоте игру лица или дай план шире/наезд к лицу")
        elif WIDE_SIZE.search(text):
            face = sorted({m.group(0).lower() for m in FACE_ACTION.finditer(text)})
            if len(face) >= 2:
                found.append(f"[Shot {label}] общий план, а игра в шоте мимикой ({', '.join(face[:4])}): H3 сузит кадр до "
                             "среднего. В общем играй телом и жестом, мимику — в отдельном крупном шоте или наездом")
    return found


# Control map (same seed, docs/reference/H3_ENCODER_RESEARCH.md): H3 keeps everything sharp — «85 mm shallow
# depth of field, bokeh» and «focus racks from the lantern to his face» rendered sharp throughout (the objects of the
# phrase were placed, the optics were not). Deep focus is the default and is not flagged.
OPTICS = re.compile(r"\b(?:shallow\s+depth\s+of\s+field|bokeh|rack(?:s|ing)?\s+focus|focus\s+(?:racks?|pulls?|shifts?)|"
                    r"pull(?:s|ing)?\s+focus|out\s+of\s+focus|(?:melts?|melting|dissolves?)\s+into\s+(?:a\s+)?(?:soft\s+)?"
                    r"(?:blur|bokeh)|only\s+(?:his|her|its|their)\s+\w+\s+(?:is|are|stays?)\s+sharp)\b", re.I)
# One light event per shot: «flashes at 0:01, 0:02.5 and 0:04» gave one flash (at 2.67 s); untimed «lit only by
# flashes» gave one flash in the first frames.
LIGHT_EVENT = re.compile(r"\b(?:lightning|flash(?:es)?|strobes?|flicker\w*)\b", re.I)
REPEAT = re.compile(r"\b(?:flashes|again|repeatedly|between\s+flashes|each\s+flash|several|twice|three\s+times)\b|"
                    r"\d:\d\d(?:\.\d+)?[^.]*\d:\d\d", re.I)


def optics_light_warnings(action: str) -> list[str]:
    """Phrases H3 did not execute on the control map: optical focus effects and repeated light events."""
    found = []
    for part in re.split(r"(?=\[Shot \d+\])", action):
        if not part.strip():
            continue
        label = (re.match(r"\[Shot (\d+)\]", part) or [None, "1"])[1]
        text = re.sub(r"<d>.*?</d>", " ", part, flags=re.S)
        optics = sorted({m.group(0).lower() for m in OPTICS.finditer(text)})
        if optics:
            found.append(f"[Shot {label}] оптика ({', '.join(optics[:3])}): H3 по тексту не размывает фон и не "
                         "переводит фокус — всё будет резким. Выделяй героя крупностью или движением камеры")
        light = [sentence for sentence in _sentences(text) if LIGHT_EVENT.search(sentence) and REPEAT.search(sentence)]
        if light:
            found.append(f"[Shot {label}] повторяющееся световое событие («{light[0][:50]}»): H3 ставит одно событие "
                         "света на шот. Каждую вспышку — в свой шот или монтажом")
    return found


# --- after the GPU: what a finished take shows, measured without a model (calibrated on a set of real takes) ----
GRID_W, GRID_H, FPS = 36, 64, 24


def _gray_frames(mp4: str) -> list[list[int]]:
    import subprocess
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", mp4, "-vf", f"fps={FPS},scale={GRID_W}:{GRID_H}", "-f",
                           "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=120).stdout
    size = GRID_W * GRID_H
    return [list(data[at:at + size]) for at in range(0, len(data) - size + 1, size)]


def _picture_grid(path: str) -> list[int]:
    from PIL import Image
    image = Image.open(path).convert("L")
    width, height = image.size
    aspect = GRID_W / GRID_H
    crop_w, crop_h = min(width, int(height * aspect)), min(height, int(width / aspect))
    left, top = (width - crop_w) // 2, (height - crop_h) // 2
    return list(image.crop((left, top, left + crop_w, top + crop_h)).resize((GRID_W, GRID_H)).tobytes())


def _corr(a: list[int], b: list[int]) -> float:
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = var_a = var_b = 0.0
    for x, y in zip(a, b):
        dx, dy = x - mean_a, y - mean_b
        cov += dx * dy; var_a += dx * dx; var_b += dy * dy
    return cov / (var_a * var_b) ** 0.5 if var_a and var_b else 0.0


def planned_cuts(prompt: str) -> list[float]:
    """Cut times the prompt asks for in the official form «[Shot 2] At 00:05.000»."""
    return [int(m) * 60 + float(sec) for m, sec in re.findall(r"\[Shot \d+\]\s*At\s+(\d\d):(\d\d(?:\.\d+)?)", prompt)]


def take_defects(mp4: str, references: list[tuple[str, str]], planned: list[float] = ()) -> list[str]:
    """Hard cuts, dissolves to another picture and reference pictures pasted into the take; [] for a clean take.

    references: (role, path) of the job's pictures; the start frame («Кадр сцены») is meant to match frame 0.
    """
    frames = _gray_frames(mp4)
    if len(frames) < 24:
        return ["дубль короче секунды или не читается"]
    size = GRID_W * GRID_H
    diffs = [sum(abs(x - y) for x, y in zip(a, b)) / size for a, b in zip(frames, frames[1:])]
    median = sorted(diffs)[len(diffs) // 2] or 0.1
    asked = lambda t, slack: any(abs(t - cut) <= slack for cut in planned)
    found = [f"склейка на {(at + 1) / FPS:.1f} с" for at, d in enumerate(diffs)
             if d > 22 and d > 5 * median and not asked((at + 1) / FPS, 0.6)]
    last = -1.0
    for at in range(0, len(frames) - 12, 3):
        if at / FPS - last > 0.6 and not asked(at / FPS + 0.25, 0.8) and _corr(frames[at], frames[at + 12]) < 0.2:
            last = at / FPS
            found.append(f"смена кадра (наплыв или склейка) около {last:.1f} с")
    for role, path in references:
        if role not in {"Лицо героя", "Персонаж целиком", "Костюм"}:
            continue  # a start frame opens the take by design; a wide view of the same room looks like its photo
        try:
            grid = _picture_grid(path)
        except OSError:
            continue
        best, when = max((_corr(frame, grid), at / FPS) for at, frame in enumerate(frames[::4]) for at in [at * 4])
        if best > 0.85:
            found.append(f"в дубль вставлена картинка-референс «{role}» около {when:.1f} с")
    return found


# --- the writer fills fields, the engine writes the prompt ------------------------------------------------------------
def _sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if not text or text[-1] in ".!?\"”" else text + "."


def _passport_words(name: str, roles: list[dict], pictures: set[str] = frozenset()) -> str:
    """The franchise passport's own words for a hero and their costume: identity text the agent no longer retypes.

    A costume line comes only when the scene carries that costume's picture or the passport marks it «main»: a hero's
    wizard robe («instead of the usual hoodie») is an alternative, not the daily look.
    """
    key = name.casefold()
    words = []
    for role in roles:
        names = {str(role.get("name") or "").casefold(), *(str(a).casefold() for a in role.get("aliases") or [])}
        worn = ({str(asset.get("asset_id") or "") for asset in role.get("assets") or []} & set(pictures)
                or "main" in str(role.get("name") or "").casefold())
        if role.get("kind") == "character" and key in names or (
                role.get("kind") == "costume" and str(role.get("owner") or "").casefold() == key and worn):
            words.append(_sentence(role.get("prompt")))
    return " ".join(filter(None, words))


OPENERS = re.compile(r"(?i)^(?:he|she|they|his|her|their|the|a|an|in|inside|on|at|as|when|while|through|then|with|"
                     r"from|behind|beside|after|before|for|one|both|each|it|its)\b")


BODY_PLURALS = {"brows", "eyebrows", "ears", "eyes", "paws", "hands", "whiskers", "shoulders", "pupils", "lips",
                "fingers", "legs", "arms", "feet", "knees", "hips", "cheeks", "wings", "antennae", "boots", "slippers"}


BODY_PARTS = ("ears?", "eyes?", "paws?", "hands?", "brows?", "whiskers?", "shoulders?", "tail", "head", "nose", "mouth",
              "jaw", "chin", "forepaws?", "hind ?legs?", "pupils?")


def _finite(text: str) -> bool:
    """«takes the ball», «checks Alex» — the field already starts with the hero's verb (lower case: «Nova» is a name)."""
    word = (re.match(r"[A-Za-z]+(?:['’]s)?", text.strip()) or [""])[0]
    return (len(word) > 2 and word[0].islower() and word.endswith("s") and not word.endswith(("ss", "'s", "’s"))
            and word not in BODY_PLURALS)


def _lower(text: str) -> str:
    """Lower-case the first letter unless the field starts with a name («Alex's open hand», «ALEX»)."""
    first = (re.match(r"[A-Za-z]+(['’]s)?", text) or [""])[0]
    keep = first[:1].isupper() and (first.isupper() or first.endswith(("'s", "’s")) or first[1:].islower() and
                                    first.lower() not in COMMON_STARTS)
    return text if keep else text[0].lower() + text[1:]


COMMON_STARTS = {"the", "a", "an", "his", "her", "its", "their", "one", "both", "each", "curtain", "ball", "soft", "warm",
                 "follow", "check", "track", "flick", "make", "test", "stand", "prompt", "takes", "looks", "eyes", "ears",
                 "brows", "hand", "tail", "head"}


def _clause(name: str, text: str, field: str, cast: tuple[str, ...] = ()) -> str:
    """One sentence of a hero's acting from a writer's field, whatever form the writer used:
    «walks three steps» → «NOVA walks three steps.»; eyes «follow the ball» → «ALEX's eyes follow the ball.»;
    notices «Nova approaching» → «ALEX notices Nova approaching.»; decides «make Alex come» → «NOVA decides to make…»."""
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    if not text:
        return ""
    heroes = {n.casefold() for n in (name, *cast)}
    # The cast is interleaved beat by beat, so «He catches…», «His brows turn…» after the partner's clause would name
    # the wrong hero; «Screen right background, ALEX pauses» would become «ALEX screen right…, ALEX».
    if field != "decides":  # decides «He was the fetcher» reads «ALEX realises he was the fetcher»
        text = re.sub(r"^(?:He|She|They)\b", name, text)
    text = re.sub(r"(?i)^(?:his|her|their)\b", f"{name}'s", text)
    body = re.match(rf"(?i)(one|both|the)\s+({'|'.join(BODY_PARTS)})\b", text)
    if body:
        part, rest = body.group(2), text[body.end(2):]
        if body.group(1).lower() == "the":
            return _sentence(f"{name}'s {part}{rest}")
        return _sentence(f"{body.group(1).capitalize()} of {name}'s {part if part.endswith('s') else part + 's'}{rest}")
    lead = re.match(r"([^,;.]{1,50}),\s*([A-Za-z]+)\b", text)
    if lead and lead.group(2).casefold() in heroes and SIDE.search(lead.group(1)):
        return _sentence(text[0].upper() + text[1:])
    first = (re.match(r"[A-Za-z]+", text) or [""])[0]
    if field == "notices" and first.casefold() != name.casefold():  # who notices is the hero of the field
        return _sentence(f"{name} notices {_lower(text)}")
    if field == "decides" and (first.casefold() in heroes - {name.casefold()} or re.match(r"(?i)(?:he|she|they)\b", text)):
        return _sentence(f"{name} realises {_lower(text[0].lower() + text[1:] if first.lower() in {'he', 'she', 'they'} else text)}")
    if first.casefold() in heroes or re.match(r"(?i)(?:he|she|they|as|when|while|after|before)\b", text):
        return _sentence(text[0].upper() + text[1:])  # a sentence of its own: «NOVA stops; ALEX raises his brows»
    if first[:1].isupper() and not first.isupper() and not re.match(r"[A-Za-z]+['’]s\b", text):
        text = text[0].lower() + text[1:]  # «Blinks, meets…», «Withhold ball» — capitalised verbs, not names
    low = _lower(text)
    if field == "position":
        return _sentence(f"{name} {low}" if _finite(text) or re.match(r"(?i)is\b", text) else f"{name} is {low}")
    if _finite(text):
        return _sentence(f"{name} {low}")
    if field == "notices":
        return _sentence(f"{name} notices {low}")
    if field == "decides":
        return _sentence(f"{name} decides {low}" if low.startswith("to ") else f"{name} decides to {low}")
    if field == "eyes" or field == "answers" and low.split(" ")[0] in BODY_PLURALS:
        return _sentence(f"{name}'s {low}" if low.split(" ")[0] in BODY_PLURALS else f"{name}'s eyes {low}")
    if field == "ends":
        return _sentence(f"{name} ends the take {low}")
    if OPENERS.match(text):
        return _sentence(text[0].upper() + text[1:])
    return _sentence(f"{name} {low}")


NEVER_CLAUSE = re.compile(r"(?i)(?:[,;:]|^)\s*[^,;:.]*\b(?:never|unless|only when|only if)\b[^,;.]*")


def _kit_words(text: str) -> str:
    """Passport words without their «never chibi» / «unless the shot names it» clauses: H3 draws what a prohibition
    or a condition names."""
    text = NEVER_CLAUSE.sub("", str(text or ""))
    return re.sub(r"\s+([;.,])", r"\1", text).replace(";.", ".").replace("..", ".").strip(" ;,")


def _sheet_sentences(kit: dict, name: str, keys: tuple[str, ...]) -> str:
    """A hero's sheet entries as plain sentences, without the «ALEX — scale: …;» labels."""
    from .scene_kit import _hero

    hero = _hero(kit, name)
    sheet = (hero or {}).get("sheet") or {}
    words = [_kit_words(sheet.get(key)) for key in keys]
    return " ".join(_sentence(text) for text in words if re.search(r"[A-Za-z]", text))


SIDE = re.compile(r"(?i)\b(?:screen[- ]?(?:left|right|cent(?:re|er))|left|right|cent(?:re|er)|middle|foreground|"
                  r"background|far end|near end)\b")
EYE_ENTRY = re.compile(r"(?i)fantasy[^.]{0,60}?\b(?:into|through)\s+([A-Z][A-Za-z]+)['’]s\s+eye")
FORMULA = re.compile(r"(?i)\b(?:his|her|their|the) partner\b|\b(?:changes|adjusts) (?:his|her|its|their) "
                     r"(?:next move|action)\b")
EYE_PUSH = re.compile(r"(?i)\b(?:into|through)\b[^.]{0,60}\beyes?\b")


def _action(body: dict) -> str:
    return parse_ir(str(body.get("prompt") or "")).get("detailed_description", "") if is_ir_prompt(
        str(body.get("prompt") or "")) else str(body.get("prompt") or "")


def staging_problems(body: dict, passport: str = "", previous: dict | None = None) -> list[str]:
    """The same staging law on a written prompt (canvas shots keep only the prompt): every hero in the action has a
    screen side, and a fantasy the passport opens through a hero's eye starts with that camera move."""
    action = _action(body).split("[Shot 1]", 1)[-1]
    found = []
    for name in sorted(_names(body)):
        said = [s for s in _sentences(action) if re.search(rf"(?i)\b{re.escape(name)}\b", s)]
        if said and not any(SIDE.search(s) for s in said):
            found.append(f"{name.upper()}: в действии нет стороны кадра (screen left / right / centre) — H3 ставит "
                         "героя куда попало")
    entry = EYE_ENTRY.search(passport or "")
    if entry and FANTASY.search(action) and not FANTASY.search(_action(previous or {}).split("[Shot 1]", 1)[-1]) \
            and not EYE_PUSH.search(action):
        found.append(f"фантазия входит только движением камеры в глаз {entry.group(1).upper()}")
    return found


def _take_text(take: dict) -> str:
    cast = " ".join(str(m.get(k) or "") for m in _members(take) for k in ("notices", "decides", "does", "ends"))
    return " ".join([str(take.get("summary") or ""), cast, str(take.get("end") or "")])


def is_fantasy(take: dict) -> bool:
    return isinstance(take, dict) and bool(FANTASY.search(_take_text(take)))


def place_problems(place: str, kit: dict | None) -> list[str]:
    """The scene's place goes into every take's environment subject: blocking there contradicts the later takes
    («Alex stands screen right gathering paper» in a take where he crouches)."""
    names = {str(alias) for hero in (kit or {}).get("cast") or [] for alias in [hero.get("name"), *(hero.get("aliases") or [])]
             if alias}
    acting = sorted({name.upper() for name in names
                     if re.search(rf"(?i)(?<![\w'’]){re.escape(name)}\b(?!['’]s)", str(place or ""))})
    return [f"place: {', '.join(acting)} — в описании места только комната и свет; где стоит и что делает герой — "
            "в take.cast[].position каждого дубля"] if acting else []


def style_problems(style: str, kit: dict | None) -> list[str]:
    """The scene style opens every take, so a hero named there is named in the shots they are not in
    («Iris and Mira are real performers…» in a shot with only Mira)."""
    names = {str(alias) for hero in (kit or {}).get("cast") or [] for alias in [hero.get("name"), *(hero.get("aliases") or [])]
             if alias}
    named = sorted({name.upper() for name in names if re.search(rf"(?i)(?<![\w'’]){re.escape(name)}\b", str(style or ""))})
    return [f"style: {', '.join(named)} — стиль сцены идёт в каждый дубль, имя героя в нём попадёт и в шоты без него; "
            "пиши стиль без имён (real performers in physical costumes)"] if named else []


def declared_cast(shot: dict | None) -> list[str] | None:
    """The shot's heroes: None when the shot does not say (every scene hero may come), [] for a shot with no hero
    (an empty room must not get the scene's heroes). storyboard-model.mjs autoKeyframeRequest reads it the same way."""
    names = (shot or {}).get("characters")
    return [n for n in names if isinstance(n, str) and n.strip()] if isinstance(names, list) else None


SPOKEN_WORDS_PER_S = 2.0  # natural English is ~2.5 words/s; a take also needs its looks, beats and reactions


def speech_problems(take: dict, duration) -> list[str]:
    """More lines than the take can say: H3 rushes them, swallows words or cuts the last line at the take's end."""
    try:
        seconds = float(duration or 0)
    except (TypeError, ValueError):
        return []
    said = " ".join(str(line.get("line") or line.get("text") or "") for line in _lines(take))
    words = len(re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", said))
    limit = int(seconds * SPOKEN_WORDS_PER_S)
    return [f"реплик {words} слов на {seconds:g} с — помещается около {limit}: H3 скомкает или оборвёт речь; "
            "сократи реплики или раздели дубль"] if seconds and words > limit else []


def absent_hero_mentions(take: dict, shot: dict | None, kit: dict | None) -> list[str]:
    """A passport hero named in a take they are not in (sceneShot.characters): their pictures stay out of the job
    (scene_runs.drop_absent_heroes), so H3 meets a bare name and draws someone. For example «glancing back toward
    the shutter where Iris is» in a shot with only Mira."""
    from .scene_kit import _hero
    characters = declared_cast(shot)
    if not kit or characters is None:
        return []
    present = {id(h) for h in map(lambda n: _hero(kit, n), characters) if h}
    text = " ".join(str(v) for v in _flat(take))
    named = sorted({str(hero.get("name") or "").upper() for hero in kit.get("cast") or [] if id(hero) not in present
                    and any(re.search(rf"(?i)(?<![\w'’]){re.escape(str(a))}\b", text) for a in [hero.get("name"), *hero.get("aliases", [])] if a)})
    return [f"{name} назван в дубле, но его нет в шоте (characters) — его фото в задание не идут, и H3 нарисует "
            "кого-то по имени: убери имя (the shutter behind him) или добавь героя в шот" for name in named]


def _flat(value) -> list:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _flat(v)]
    if isinstance(value, list):
        return [x for v in value for x in _flat(v)]
    return [value] if isinstance(value, str) else []


def thin_description(prompt: str) -> list[str]:
    """ref-en.txt §5.2: a generation take's detailed_description is normally 350–500 English words of what is seen
    and heard; fewer leaves H3 to invent the rest."""
    words = len(parse_ir(prompt).get("detailed_description", "").split())
    return [f"в описании дубля {words} слов, гайд H3 — 350–500: допиши видимое и слышимое (мир, реакции, звук действия)"
            ] if words < 350 else []


def repeated_sentences(takes: list[dict]) -> list[str]:
    """A sentence of six or more words in two takes is boilerplate the model reads as a rule, not as this take."""
    seen: dict[str, int] = {}
    found = []
    for at, take in enumerate(takes, 1):
        if not isinstance(take, dict):
            continue
        world = take.get("world") if isinstance(take.get("world"), dict) else {}
        text = " ".join([*(str(m.get(k) or "") for m in _members(take) for k in
                           ("position", "notices", "eyes", "decides", "does", "answers", "ends")),
                         *(str(v or "") for v in world.values())])
        for sentence in {s.strip().casefold() for s in re.split(r"(?<=[.!?;])\s+", text) if len(s.split()) >= 6}:
            if sentence in seen and seen[sentence] != at:
                found.append(f"шот {at}: та же фраза, что в шоте {seen[sentence]} — «{sentence[:70]}…»; у каждого дубля свои события")
            seen.setdefault(sentence, at)
    return found


def _members(take: dict) -> list[dict]:
    """Cast entries the engine can read; a bare «"NOVA"» string must not answer scene_draft with HTTP 500."""
    return [member for member in take.get("cast") or [] if isinstance(member, dict)]


def _lines(take: dict) -> list[dict]:
    return [line for line in take.get("lines") or [] if isinstance(line, dict)]


def take_problems(take: dict, kit: dict | None = None, *, passport: str = "", previous: dict | None = None) -> list[str]:
    """What the writer still owes before the engine may write the take: fields Studio cannot invent.

    Staging is checked here, not by eye: every hero has a screen side, every hero has a passport (heroes without a sheet
    come back as random look-alikes), and a fantasy that the passport opens through a hero's
    eye starts with that camera move («Fantasy entry is a motivated camera move into NOVA's eye»).
    """
    found = []
    if not isinstance(take, dict):
        return ["нет полей take"]
    from .scene_kit import _hero

    if any(not isinstance(member, dict) for member in take.get("cast") or []):
        found.append("take.cast — список объектов {name, position, notices, eyes, decides, does, answers, ends}, "
                     "а не строк с именами")
    if any(not isinstance(line, dict) for line in take.get("lines") or []):
        found.append("take.lines — список объектов {speaker, line}, а не строк")
    for member in _members(take):
        name = str(member.get("name") or "").strip()
        if not name:
            continue
        if not SIDE.search(str(member.get("position") or "")):
            found.append(f"{name}: в position нет стороны кадра (screen left / right / centre, foreground / background) — "
                         "без неё H3 ставит героя куда попало")
        if kit and kit.get("cast"):
            hero = _hero(kit, name)
            if not hero:
                found.append(f"{name}: такого героя нет в паспорте франшизы — H3 нарисует случайного персонажа")
            elif not any(str((hero.get("sheet") or {}).get(k) or "").strip() for k in ("scale", "body", "costume")):
                found.append(f"{name}: в паспорте пустой лист героя (scale, body, costume) — заполни паспорт, иначе "
                             "внешность и размер будут случайными")
    if isinstance(take.get("world"), str):
        found.append("take.world — объект {air, physics, light, background}, а не строка")
    for member in _members(take):
        if FORMULA.search(" ".join(str(member.get(k) or "") for k in ("eyes", "answers"))):
            found.append(f"{str(member.get('name') or '').strip()}: ответ формулой («his partner… in response») — "
                         "напиши, что именно он видит у другого героя и чем отвечает")
    sound = " ".join(str(take.get(k) or "") for k in ("sound", "music")).casefold()
    for line in _lines(take):
        said = str(line.get("line") or line.get("text") or "").strip(" .!?").casefold()
        if said and re.search(rf"(?<!\w){re.escape(said)}(?!\w)", sound):
            found.append(f"реплика «{said}» повторена в sound/music — слова реплик только в lines (гайд H3 §6)")
    for line in _lines(take):
        if not str(line.get("line") or line.get("text") or "").strip():
            found.append(f"реплика без текста у {line.get('speaker') or '?'}: поле line")
    entry = EYE_ENTRY.search(passport or "")
    if entry and is_fantasy(take) and not is_fantasy(previous or {}) and not EYE_PUSH.search(str(take.get("camera") or "")):
        found.append(f"фантазия входит только движением камеры в глаз {entry.group(1).upper()} — опиши это в camera "
                     "(«the camera pushes slowly into NOVA's right eye…»)")
    if kit and kit.get("life"):
        from .scene_kit import take_life
        found += take_life(kit, take)[1]
    return found


def _ending(end: str) -> str:
    """«Nova holds the ball» is a sentence of its own; «Nova holding the ball» is the image the take ends on."""
    end = re.sub(r"\s+", " ", end).strip().rstrip(".")
    if not end:
        return ""
    words = re.findall(r"[A-Za-z'’]+", end)[1:6]
    if any(w in {"has", "is", "are", "was", "were"} or _finite(w) for w in words):
        return _sentence("At the end of the take, " + _lower(end))
    return _sentence(f"The take ends on {_lower(end)}")


def without_absent_heroes(text: str, shot: dict | None, kit: dict | None) -> str:
    """The style without the heroes this shot does not have: «Iris, Mira and the surrounding crowd are living performers»
    in a shot with only Mira would name Iris. A name in a list goes, the rest of the sentence stays;
    a sentence about that hero alone goes."""
    from .scene_kit import _hero
    characters = declared_cast(shot)
    if not kit or characters is None or not text:
        return text
    present = {id(h) for h in map(lambda n: _hero(kit, n), characters) if h}
    absent = [str(a) for hero in kit.get("cast") or [] if id(hero) not in present
              for a in [hero.get("name"), *(hero.get("aliases") or [])] if a]
    if not absent:
        return text
    name = "(?:" + "|".join(re.escape(a) for a in sorted(absent, key=len, reverse=True)) + r")\b(?!['’])"
    kept = []
    for sentence in _sentences(text):
        sentence = re.sub(rf"(?i)(?<![\w'’]){name},\s*", "", sentence)          # «Iris, Mira and …»
        sentence = re.sub(rf"(?i)(?:,\s*|\s+and\s+){name}", "", sentence)   # «Mira and Iris», «…, Iris»
        sentence = re.sub(rf"(?i)(?<![\w'’]){name}\s+and\s+", "", sentence)  # «Iris and Mira»
        if not re.search(rf"(?i)(?<![\w'’]){name}", sentence):
            kept.append(sentence[:1].upper() + sentence[1:])
    return " ".join(kept)


def style_opening(style: str) -> str:
    """«The target video is in a … style» (ref-en.txt §5.2) from the passport style, without Studio's own notes:
    «Premium 3D animated feature look (visual benchmark job 1a2b3c4d): …» reached H3 with the job number."""
    style = re.sub(r"\s*\([^)]*\b(?:job|benchmark|asset|№)\b[^)]*\)", "", str(style or "")).strip()
    if not style or style.startswith("The target video"):
        return _sentence(style)
    head, colon, rest = style.partition(":")
    if not colon or len(head.split()) > 8:
        return _sentence(f"The target video is in this style: {style}")
    head = re.sub(r"(?i)\s+(?:look|style)$", "", head.strip())
    head = head if head[1:2].isupper() else head[:1].lower() + head[1:]
    return _sentence(f"The target video is in a {head} style: {rest.strip()}")


def dialogue(take: dict, kit: dict | None) -> list[str]:
    """Lines as H3 was trained on them (base-en.txt §4.4): a stable speaker ID, the voice once, the words in
    <d>[Language] …</d>. «ALEX says, "Boo."» was plain text to H3, with no speaker and no language tag."""
    from .scene_kit import _hero

    language = str((kit or {}).get("language") or "English")
    ids, out = {}, []
    for line in _lines(take):
        speaker, said = str(line.get("speaker") or "").strip(), str(line.get("line") or line.get("text") or "").strip()
        if not speaker or not said:
            continue
        said = said if said[-1] in ".!?" else said + "."
        if speaker not in ids:
            ids[speaker] = f"(S{len(ids) + 1})"
            hero = _hero(kit or {}, speaker)
            voice = _kit_words((hero or {}).get("sheet", {}).get("voice") if hero else "").rstrip(".")
            voice = re.sub(r"(?i)^(?:english|russian)[,;]\s*|[,;]\s*(?:english|russian)\b", "", voice).strip(" ,;")
            who = f"{speaker} {ids[speaker]}" + (f", whose voice is {voice[:1].lower() + voice[1:]}," if voice else "")
            out.append(f"{who} says, <d>[{language}] {said}</d>")
        else:
            out.append(f"{speaker} {ids[speaker]} says, <d>[{language}] {said}</d>")
    return out


def compose(take: dict, shot: dict, *, style: str = "", place: str = "", roles: list[dict] = (),
            pictures: set[str] = frozenset(), kit: dict | None = None, fantasy_world: str = "") -> str:
    """One take's official six-section prompt from the writer's fields, the storyboard and the franchise passport.

    take: summary, cast [{name, position, notices, eyes, decides, does, answers, ends}], world {air, physics, light,
    background}, camera, end, lines [{speaker, line}], sound, music.
    Subjects carry only how a hero looks (sheet scale/body/makeup/costume); how they move (gait/posture/poses/face)
    opens the action; voice goes with their lines; the sheet's «never» stays out (it is for Claude's check).
    Pictures are bound later from the reference roles (normalize); the writer never types <Picture N>.
    """
    from .scene_kit import LOOK_KEYS, WORLD_KEYS, world_brief, _hero

    kit = kit or {}
    cast = [member for member in _members(take) if str(member.get("name") or "").strip()]
    subjects, play = [], []
    for at, member in enumerate(cast, 1):
        name = member["name"].strip()
        look = _sheet_sentences(kit, name, LOOK_KEYS)
        costume = _passport_words(name, [r for r in roles if r.get("kind") == "costume"], pictures) if look else ""
        identity = look or _passport_words(name, list(roles), pictures)
        subjects.append(f"<Subject {at}> is {name}. {' '.join(filter(None, [identity, costume]))}".strip())
        # The sheet's face/gait/poses and the passport line are rules, not events: H3's guide wants only what is seen
        # and heard (base-en.txt §4.1). Otherwise they take ~110 words of every take; the take's fields carry the acting.
    # A fantasy take is drawn in the «Мир фантазии» picture, not in the real room («Enclosed
    # balcony» in the text against an ice-planet picture makes H3 slide back into the apartment). Its environment line is
    # the world's own description; without one the line is left out and the picture alone carries the place.
    fantasy = is_fantasy(take) or (isinstance(shot, dict) and shot.get("fantasy") is True)
    environment = fantasy_world if fantasy else place
    if environment:
        subjects.append(f"<Subject {len(cast) + 1}> is the environment: " + _sentence(environment))
    # Beat by beat across the cast, as H3 reads time: where everyone is, what each notices and decides, what each
    # does and answers, how each ends — not ALEX's whole take and then NOVA's (ALEX «ends puzzled» before NOVA acts).
    names = tuple(member["name"].strip() for member in cast)
    acting = [_clause(member["name"].strip(), str(member.get(key) or ""), key, names)
              for key in ("position", "notices", "eyes", "decides", "does", "answers", "ends") for member in cast]
    world = take.get("world") if isinstance(take.get("world"), dict) else {}
    life = [_sentence(world.get(key)) for key in WORLD_KEYS if str(world.get(key) or "").strip()]
    lines = dialogue(take, kit)
    camera, trained = _camera_text(str(take.get("camera") or ""), shot)
    action = " ".join(filter(None, [opening(shot, camera=trained), staging_sentence(shot), *play, *acting, *life,
                                     camera, *lines, _ending(str(take.get("end") or ""))]))
    sections = {"subject_definitions": "\n".join(subjects),
                "summary": "[reference generation] " + _sentence(take.get("summary")),
                "retention_analysis": "Studio binds the reference pictures.",
                # The take's own world events replace the passport's general line (which says «sunlight by day»
                # in a night scene); the general line is only the fallback for a take without them.
                "detailed_description": " ".join(filter(None, [style_opening(without_absent_heroes(style, shot, kit)), "" if len(life) >= 2 else
                                                               _kit_words(world_brief(kit))]))
                                        + "\n\n[Shot 1] " + action,
                "overall_soundscape": _sentence(take.get("sound")) or "Natural synchronized sound of the visible action.",
                "non_diegetic_music": _sentence(take.get("music")) or "None."}
    return render_ir(sections)
