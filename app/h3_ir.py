"""MiniMax H3 full-reference prompt format ("IR"): skeleton, scenarist request and validation.

H3-Base was trained on prompts written by MiniMax's rewriter (H3-Context-IR): six English
sections in a fixed order. The official guide (huggingface.co/MiniMaxAI/MiniMax-H3,
docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md) is the source of every rule here.

Split of work:
- Studio knows the references, their roles and owners, cut lengths and camera choices. This
  module turns them into the deterministic parts: <Subject N>/<Picture N>/<Video N>/<Audio N>
  definitions, retention markers, task-type prefix and shot timecodes.
- The local Qwen (GPU server) writes the creative part from the user's brief in any language:
  the English detailed_description, soundscape and music, inside that skeleton.
- validate_ir() rejects output that breaks the format before it reaches H3.
"""
from __future__ import annotations
from .motion_reference_policy import ROLE_INSTRUCTION as MOTION_CAMERA_RULE, MOTION_ONLY_INSTRUCTION

import re
from dataclasses import dataclass, field

SECTIONS = (
    "subject_definitions", "summary", "retention_analysis",
    "detailed_description", "overall_soundscape", "non_diegetic_music",
)
DESCRIPTION_WORDS = (350, 500)  # guide §5.2, generation tasks

CHARACTER_ROLES = {"Лицо героя": "face and identity", "Персонаж целиком": "full-body appearance and silhouette",
                   "Костюм": "costume and carried props"}
MOTION_VIDEO_ROLES = {"Движение": "body motion, action timing and facial performance", "Движение и камера": "body motion, action timing and facial performance",
                      "Актёрская игра": "facial performance, head motion and gestures"}
STRUCTURE_VIDEO_ROLES = {"Камера": "camera movement and spatial depth", "Ритм сцены": "cut and pacing structure",
                         "Движение и камера": "camera, framing, scene layout and lighting",
                         "Исходное видео": "scene composition and staging"}
CONTINUATION_ROLE = "Продолжение"  # app/continuation.py ROLE
AUDIO_ROLES = {"Голос": "voice timbre and delivery", "Музыка": "music style and rhythm",
               "Звуковой эффект": "sound-effect texture", "Атмосфера": "ambience"}


@dataclass
class Reference:
    kind: str            # "image" | "video" | "audio"
    number: int          # H3 label number within its kind (audio: after used video soundtracks)
    role: str            # Studio role, e.g. "Лицо героя"
    owner: str = ""      # hero name for character roles
    evidence: str = ""   # short visible description (Qwen), optional


@dataclass
class Skeleton:
    subject_lines: list[str] = field(default_factory=list)
    retention_lines: list[str] = field(default_factory=list)
    task_types: list[str] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    speakers: dict[str, str] = field(default_factory=dict)  # owner -> "(S1)"


_LATIN = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ("a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l",
                 "m", "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya")))


def latin_name(name: str) -> str:
    """A hero's name as H3 reads it: Studio owners in Cyrillic (ГЕРОЙ) used to reach English prompts as is."""
    name = str(name or "").strip()
    if not re.search("[А-Яа-яЁё]", name):
        return name
    words = []
    for word in name.split():
        latin = "".join(_LATIN.get(ch.lower(), ch) for ch in word)
        words.append(latin.upper() if word.isupper() and len(word) > 1 else latin[:1].upper() + latin[1:])
    return " ".join(words)


def _tag(ref: Reference) -> str:
    return f"<{ {'image': 'Picture', 'video': 'Video', 'audio': 'Audio'}[ref.kind] } {ref.number}>"


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def build_skeleton(references: list[Reference], *, speaking_owners: list[str] = (),
                   use_video_audio: bool = False) -> Skeleton:
    """Deterministic subject_definitions / retention_analysis / task types from Studio roles."""
    sk = Skeleton()
    subject_no = 0
    owners: dict[str, list[Reference]] = {}
    for ref in references:
        if ref.owner and (ref.role in CHARACTER_ROLES or ref.role in MOTION_VIDEO_ROLES or ref.role == "Голос"):
            owners.setdefault(ref.owner.casefold(), []).append(ref)
    owner_subject: dict[str, str] = {}
    for speaker_no, name in enumerate(dict.fromkeys(o.casefold() for o in speaking_owners), 1):
        sk.speakers[name] = f"(S{speaker_no})"

    for key, refs in owners.items():
        visual = [r for r in refs if r.role in CHARACTER_ROLES or r.role in MOTION_VIDEO_ROLES]
        if not visual:
            continue
        subject_no += 1
        label = f"<Subject {subject_no}>"
        owner_subject[key] = label
        name = latin_name(refs[0].owner)
        looks = [r for r in visual if r.role in CHARACTER_ROLES]
        moves = [r for r in visual if r.role in MOTION_VIDEO_ROLES]
        sources = [f"whose {CHARACTER_ROLES[r.role]} come from {_tag(r)}" for r in looks]
        # A movement video lends only its performer's motion (guide §4: attribute_transfer); the
        # performer's hair, beard and clothes leaked onto the hero when it was cited as a source.
        sources += [f"whose {MOTION_VIDEO_ROLES[r.role]} is transferred from the separate performer in {_tag(r)}"
                    for r in moves]
        evidence = "; ".join(r.evidence for r in looks if r.evidence)
        sk.subject_lines.append(f"{label} is {name}, {_join(sources)}" + (f": {evidence}." if evidence else "."))
        if looks:
            kept = _join(sorted({CHARACTER_ROLES[r.role] for r in looks}))
            sk.retention_lines.append(f"{label} (appears in [SHOTS]): fully_preserved - {name}'s {kept} are retained"
                                      + (", including hairstyle, hair length, hair color and facial hair." if moves else "."))
        for r in moves:
            look = _join([_tag(x) for x in looks]) if looks else "the subject definition"
            sk.retention_lines.append(
                f"{_tag(r)} ({MOTION_VIDEO_ROLES[r.role]}): attribute_transfer - only the performer's "
                f"{MOTION_VIDEO_ROLES[r.role]} are transferred to {label}; {name}'s hair, facial hair, face, "
                f"build and clothing stay as in {look}.")
        sk.labels.add(label)
        sk.labels.update(_tag(r) for r in visual)

    for ref in references:
        tag = _tag(ref)
        if ref.kind == "image" and ref.role == "Окружение":
            subject_no += 1
            label = f"<Subject {subject_no}>"
            sk.subject_lines.append(f"{label} is the environment in {tag}" + (f": {ref.evidence}." if ref.evidence else "."))
            sk.retention_lines.append(f"{label} (appears in [SHOTS]): fully_preserved - the location, layout and set dressing are retained.")
            sk.labels.update({label, tag})
        elif ref.kind == "image" and ref.role == "Предмет":
            subject_no += 1
            label = f"<Subject {subject_no}>"
            sk.subject_lines.append(f"{label} is the object in {tag}" + (f": {ref.evidence}." if ref.evidence else "."))
            sk.retention_lines.append(f"{label} (appears in [SHOTS]): fully_preserved - the object's shape, material and scale are retained.")
            sk.labels.update({label, tag})
        elif ref.kind == "image" and ref.role in {"Стиль", "Свет", "Цвет"}:
            subject_no += 1
            label = f"<Subject {subject_no}>"
            what = {"Стиль": "rendering style", "Свет": "lighting setup", "Цвет": "color grade and palette"}[ref.role]
            sk.subject_lines.append(f"{label} is the {what} of {tag}.")
            sk.retention_lines.append(f"{label} (appears in [SHOTS]): weak_reference - only the {what} is carried over.")
            sk.labels.update({label, tag})
        elif ref.kind == "image" and ref.role == "Композиция":
            subject_no += 1
            label = f"<Subject {subject_no}>"
            sk.subject_lines.append(f"Let {tag} be a guide for the general composition of the video only: "
                                    f"{label} is its overall layout, subject placement, shot size and camera angle.")
            # No negatives (H3 has no negative prompt: naming "people, faces" invited them). The picture itself is
            # sent as a blurred grey layout sketch (local_studio/worker.py composition_sketch), 2026-10-08.
            sk.retention_lines.append(f"{label} (appears in [SHOTS]): weak_reference - only the general composition, the "
                                      f"placement of the figures, the shot size and the camera angle are carried over; every "
                                      f"person takes face, hair and clothing from their own picture.")
            sk.labels.update({label, tag})
        elif ref.kind == "image" and ref.role == "Кадр сцены":
            sk.subject_lines.append(f"{tag} is the first frame of [Shot 1].")
            sk.retention_lines.append(f"{tag} ([Shot 1] first frame): fully_preserved - the composition is kept as the opening frame.")
            sk.labels.add(tag)
            if "keyframe completion" not in sk.task_types:
                sk.task_types.append("keyframe completion")
        elif ref.kind == "video" and (ref.role in STRUCTURE_VIDEO_ROLES or ref.role in MOTION_VIDEO_ROLES):
            owned = tag in sk.labels  # already cited inside a hero's <Subject N> line
            if owned and ref.role not in STRUCTURE_VIDEO_ROLES:
                continue
            if owned:
                what = STRUCTURE_VIDEO_ROLES[ref.role]  # performance sits in the hero's Subject line
            elif ref.role in MOTION_VIDEO_ROLES and ref.role in STRUCTURE_VIDEO_ROLES:
                what = "body motion, camera movement, shot sizes and spatial depth"
            else:
                what = STRUCTURE_VIDEO_ROLES.get(ref.role) or MOTION_VIDEO_ROLES[ref.role]
            sk.subject_lines.append(f"{tag} is the reference for the target video's {what}.")
            if ref.role in MOTION_VIDEO_ROLES and not owned:
                sk.retention_lines.append(
                    f"{tag} ({what}): attribute_transfer - only the performer's {what} are transferred; every "
                    "subject keeps the hairstyle, hair length, hair color, facial hair, face and clothing of their own picture; "
                    "its on-screen text, captions, logos and title cards stay in the reference file.")
            else:
                sk.retention_lines.append(f"{tag} ({what}): weak_reference - only the {what} is followed; its on-screen "
                                          "text, captions, logos and title cards stay in the reference file.")
            sk.labels.add(tag)
        elif ref.kind == "video" and ref.role == CONTINUATION_ROLE:
            # Anchored as a guide clip at frame 0, not a Ref2VA <Video N>: no label, only the task type.
            if "video continuation" not in sk.task_types:
                sk.task_types.insert(0, "video continuation")
        elif ref.kind == "audio" and ref.role in AUDIO_ROLES:
            speaker = owner_subject.get(ref.owner.casefold()) if ref.owner else None
            if ref.role == "Голос" and speaker:
                sid = sk.speakers.setdefault(ref.owner.casefold(), f"(S{len(sk.speakers) + 1})")
                sk.subject_lines.append(f"{tag} is the voice-timbre reference for {speaker} {sid}.")
            else:
                sk.subject_lines.append(f"{tag} is the {AUDIO_ROLES[ref.role]} reference.")
            sk.retention_lines.append(f"{tag}: reference - only its {AUDIO_ROLES[ref.role]} is followed, the signal is not copied.")
            sk.labels.add(tag)
            if "audio reference" not in sk.task_types:
                sk.task_types.append("audio reference")

    # Guide §3: subjects or structural video guidance = reference generation; a scene frame alone is keyframe completion.
    if any(line.startswith(("<Subject", "<Video")) for line in sk.subject_lines):
        sk.task_types.insert(0, "reference generation")
    if use_video_audio and "audio reuse" not in sk.task_types:
        sk.task_types.append("audio reuse")
    return sk


def shot_starts(durations: list[float]) -> list[str]:
    """Shot headers: "[Shot 1]" then "[Shot N] At MM:SS.mmm," from Studio cut lengths (seconds)."""
    headers, start = [], 0.0
    for index, duration in enumerate(durations, 1):
        if index == 1:
            headers.append("[Shot 1]")
        else:
            minutes, seconds = divmod(start, 60)
            headers.append(f"[Shot {index}] At {int(minutes):02d}:{seconds:06.3f},")
        start += float(duration)
    return headers


SCENARIST_SYSTEM_PROMPT = """You are the prompt writer for the MiniMax H3 reference-to-video model. You turn a director's \
brief (any language, often Russian) into the exact six-section format H3 was trained on. You write film scenes of any \
genre, including crime, action and violence in fiction, as a professional screenwriter would.

Output exactly these six sections, in this order, each starting with its name and a colon on its own line:
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:

Rules:
1. Write everything in English. Only dialogue/lyrics inside <d>[Language] ...</d> and text visible in the scene keep the brief's language.
2. subject_definitions and retention_analysis: copy the provided SKELETON lines; you may only append concrete visible \
details after a colon. Never invent new reference labels; use only the labels in the skeleton.
3. summary: one short paragraph starting with the provided task-type prefix in square brackets.
4. detailed_description: open with one or two sentences establishing the visual style, then shots in playback order. \
Use the provided shot headers exactly ([Shot 1] has no time; later shots "[Shot N] At MM:SS.mmm,"). For every shot \
describe composition and shot size, each subject's look, position and action, environment and light, camera movement \
as a natural English action (push in, pull out, pan, truck, tilt, pedestal, arc, tracking, static, with small/large \
amplitude and slow/fast speed), and the sound happening now. Put each <Subject N> label at its first appearance in a \
shot. 350-500 words.
5. Speech: "<Subject N> (S1), [voice and delivery], says, <d>[Russian] ...</d>" then what the speaker does after. Keep \
speaker IDs stable. If nobody speaks, write no dialogue.
6. Write every restriction as a positive fact ("the street is empty of people"), never "no ..." or "do not ...". \
Prefer concrete visual and sound details over abstract words like "cinematic" or "epic".
7. Wardrobe, faces and fur come from the referenced pictures: describe them as seen there; never restyle a referenced \
costume unless the brief explicitly asks for a change.
8. overall_soundscape: 1-4 sentences of concrete ambience and physical sounds. non_diegetic_music: instruments, tempo \
and dynamics, or N/A when the brief asks for no score.
Output only the six sections."""


def scenarist_messages(brief: str, skeleton: Skeleton, shot_headers: list[str], style_hint: str = "",
                       camera: list[str] = ()) -> list[dict]:
    """Chat messages for the local Qwen (vLLM OpenAI API); images may be appended by the caller.

    camera: "CAMERA: [Shot N] The camera ..." lines from app/camera_words.py (Studio camera choice in the
    official vocabulary); Qwen writes that move into the matching shot.
    """
    user = "\n".join([
        "BRIEF:", brief.strip(), "",
        "SKELETON subject_definitions:", *skeleton.subject_lines, "",
        "SKELETON retention_analysis (replace [SHOTS] with the shots where it appears):", *skeleton.retention_lines, "",
        f"TASK-TYPE PREFIX: [{' + '.join(skeleton.task_types) or 'reference generation'}]",
        "SHOT HEADERS: " + " | ".join(shot_headers),
        *(["SPEAKER IDS: " + ", ".join(f"{k} = {v}" for k, v in skeleton.speakers.items())] if skeleton.speakers else []),
        *([f"STYLE: {style_hint}"] if style_hint else []),
        *(["CONTINUATION: the video continues directly from the end of the previous clip, whose last frames and "
            "sound are anchored at the start of [Shot 1]; describe the opening as picking up exactly where that clip "
            "ended (same subjects, positions, lighting, camera and sound) and use no reference label for it."]
          if "video continuation" in skeleton.task_types else []),
        *(list(camera) + ["Write each CAMERA move into its shot (a CAMERA line without [Shot N] applies to every "
                          "shot) as the camera movement of that shot, keeping its motion type, amplitude and speed."]
          if camera else []),
    ])
    system = SCENARIST_SYSTEM_PROMPT
    if any('body motion, action timing and facial performance' in line for line in skeleton.subject_lines):
        motion_camera = any('camera, framing, scene layout and lighting' in line for line in skeleton.subject_lines)
        system = system.replace('350-500 words.', 'Concise prose without repeating the reference contract.')
        system += '\nMotion reference policy: ' + (MOTION_CAMERA_RULE if motion_camera else MOTION_ONLY_INSTRUCTION)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


_SECTION_RE = re.compile(r"(?m)^(%s):[ \t]*" % "|".join(SECTIONS))
_LABEL_RE = re.compile(r"<(Subject|Picture|Video|Audio) (\d+)>")
_NEGATION_RE = re.compile(r"(?i)\b(?:no|not|never|without|don't|do not|avoid)\b")


def is_ir_prompt(text: str) -> bool:
    """A prompt already in the official six-section format (e.g. from the scenarist)."""
    return str(text or "").lstrip().startswith("subject_definitions:")


def parse_ir(text: str) -> dict[str, str]:
    parts = _SECTION_RE.split(text.strip())
    sections: dict[str, str] = {}
    for name, body in zip(parts[1::2], parts[2::2]):
        sections.setdefault(name, body.strip())
    return sections


def render_ir(sections: dict[str, str]) -> str:
    return "\n\n".join(f"{name}:\n{sections[name].strip()}" for name in SECTIONS if name in sections)


_ROLE_WORDS = (("face and identity", "face and identity"), ("full-body appearance", "full-body appearance and silhouette"),
               ("costume", "costume and carried props"))


_SOURCE_TAG = re.compile(r"<(?:Picture|Video|Audio) \d+>")


def _kept_description(rest: str) -> str:
    """The user's own words about a hero (age, place in the shot, wardrobe, voice) after Studio's picture binding.

    The binding replaces only the claims about which picture gives what; everything else the user wrote stays.
    Until 2026-10-08 the whole line was replaced, so per-character voices, ages and towels never reached H3.
    """
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", rest.strip()) if part.strip()]
    kept = []
    for number, sentence in enumerate(sentences):
        if number == 0:
            # ", a man approximately 60 years old, whose face ... <Picture 1>." → "a man approximately 60 years old"
            sentence = re.split(r",?\s*\bwhose\b", sentence.lstrip(",: "), maxsplit=1)[0]
        sentence = sentence.strip().rstrip(".!?").strip()
        if sentence and not _SOURCE_TAG.search(sentence):
            kept.append(sentence)
    return "".join(f" {item[0].upper()}{item[1:]}." for item in kept)


def rebind_subject_pictures(text: str, skeleton: Skeleton) -> str:
    """Give each named hero their own pictures, whatever numbers the prompt used.

    The user writes "<Subject 1> is Anna, whose face ... <Picture 1>" without knowing the file order;
    Studio knows which pictures belong to the owner "Anna". When a Subject line's name equals a Studio
    owner (case-insensitive), its hero picture tags (and the same Subject's retention line) are replaced
    by that owner's pictures in file order; missing ones are appended with their role. Pictures that
    belong to no hero (environment, objects) and every other text stay as written.
    """
    def picture_sources(line: str) -> list[tuple[str, str]]:
        sources = []
        for tag_match in re.finditer(r"<Picture \d+>", line):
            before = line[max(0, tag_match.start() - 110):tag_match.start()].lower()
            role = next((word for key, word in reversed(_ROLE_WORDS) if key in before.rsplit(">", 1)[-1]),
                        "appearance")
            sources.append((tag_match.group(0), role))
        return sources

    heroes: dict[str, list[tuple[str, str]]] = {}
    face_owner: dict[str, str] = {}
    for line in skeleton.subject_lines:
        match = re.match(r"<Subject \d+> is ([^,.:]+), whose", line)
        if not match:
            continue
        pictures = picture_sources(line)
        owner = match.group(1).split(" in <", 1)[0].strip().casefold()
        heroes[owner] = pictures
        for tag, role in pictures:
            if role in {"face and identity", "full-body appearance and silhouette"}:
                face_owner[tag] = owner
    hero_tags = {tag for pictures in heroes.values() for tag, _ in pictures}
    hero_lines = {}
    for line in skeleton.subject_lines:
        source = re.match(r"<Subject \d+> is ([^,.:]+), whose", line)
        if source:
            hero_lines[source.group(1).split(" in <", 1)[0].strip().casefold()] = line
    if not heroes:
        return text
    sections = parse_ir(text)
    if "subject_definitions" not in sections:
        return text
    remaps: dict[str, dict[str, str]] = {}
    lines = sections["subject_definitions"].splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"(<Subject \d+>)\s+is\s+([^,.:]+)", line)
        owner = match.group(2).strip().casefold() if match else None
        matched_by_name = owner in heroes if owner else False
        if match and owner is None:
            anchored = {face_owner[tag] for tag in re.findall(r"<Picture \d+>", line) if tag in face_owner}
            if len(anchored) == 1:
                owner = next(iter(anchored))
        pictures = heroes.get(owner) if owner else None
        if not pictures:
            continue
        own = [tag for tag, _ in pictures]
        written_sources = [(tag, role) for tag, role in picture_sources(line) if tag in hero_tags]
        written = [tag for tag, _ in written_sources]
        if set(written) == set(own):
            if (not matched_by_name or dict(written_sources) == dict(pictures)
                    or all(role == "appearance" or (tag, role) in pictures for tag, role in written_sources)):
                continue
        mapping = {old: own[i] for i, old in enumerate(dict.fromkeys(written)) if i < len(own)}
        expected = hero_lines[owner]
        # Role metadata is authoritative for source attribution. Rewriting only tag numbers
        # leaves claims like "complete costume from the face image" attached to the new tag.
        expected_content = expected.split(" is ", 1)[1].split(",", 1)[1]
        lines[index] = (f"{match.group(1)} is {match.group(2).strip()},{expected_content}"
                        + _kept_description(line[match.end():]))
        remaps[match.group(1)] = mapping
    if not remaps:
        return text
    sections["subject_definitions"] = "\n".join(lines)
    retention = sections.get("retention_analysis", "").splitlines()
    for index, line in enumerate(retention):
        label = re.match(r"(<Subject \d+>)", line)
        if label and label.group(1) in remaps:
            expected = next((item for item in skeleton.subject_lines
                             if item.startswith(label.group(1) + " is ")), None)
            name_match = re.match(r"<Subject \d+> is ([^,.:]+)", expected or "")
            sources = re.findall(r"whose (.+?) come from (<Picture \d+>)", expected or "")
            appearances = re.search(r"\((appears in [^)]*)\)", line)
            if name_match and sources and appearances:
                subject = next((item for item in lines if item.startswith(label.group(1) + " is ")), expected)
                subject_name = re.match(r"<Subject \d+> is ([^,.:]+)", subject)
                name = (subject_name.group(1) if subject_name else name_match.group(1)).strip()
                bindings = "; ".join(
                    f"{name}'s {attribute} are defined by {tag}"
                    for attribute, tag in sources
                )
                retention[index] = (f"{label.group(1)} ({appearances.group(1)}): "
                                    f"fully_preserved - {bindings}.")
            else:
                mapping = remaps[label.group(1)]
                retention[index] = re.sub(r"<Picture \d+>", lambda m: mapping.get(m.group(0), m.group(0)), line)
    sections["retention_analysis"] = "\n".join(retention)
    return render_ir(sections)


def complete_subject_sources(text: str, skeleton: Skeleton) -> str:
    """Add omitted, unambiguous source tags to their existing Subject lines.

    A user-authored official prompt may describe the right hero and costume but omit the face
    tag in subject_definitions. Roles/owners from the job manifest are authoritative. Never
    repair a wrong owner, a foreign source tag, or a missing Subject line.
    """
    sections = parse_ir(text)
    lines = sections.get("subject_definitions", "").splitlines()
    changed = False
    # Pasted single-hero prompts often say "ANNA's face ..." without the official
    # <Subject 1> label. Repair only when the name and *all* source tags already
    # match the manifest; ambiguous or foreign bindings must still be rejected.
    expected_heroes = [re.match(r"(<Subject \d+>) is ([^,.:]+)", line)
                       for line in skeleton.subject_lines]
    expected_heroes = [match for match in expected_heroes if match]
    if expected_heroes and not any(re.match(r"<Subject \d+>\s+is\b", line) for line in lines):
        # Studio accepts a pasted brief such as "NOVA is my character" plus
        # explicit "<Picture N> ... for NOVA" lines. Materialize only verified
        # bindings; the original authored lines remain intact below them.
        added = []
        for expected in skeleton.subject_lines:
            match = re.match(r"(<Subject \d+>) is ([^,.:]+)", expected)
            if not match:
                continue
            name = match.group(2).split(" in <", 1)[0].strip()
            tags = {f"<Picture {number}>" for kind, number in _LABEL_RE.findall(expected) if kind == "Picture"}
            # "<Picture 1> NOVA face identity, <Picture 2> NOVA full costume" names the owner right at the tag.
            direct = lambda tag: any(re.search(rf"{re.escape(tag)}\s+{re.escape(name)}\b", line, re.I) for line in lines)
            named = (any(re.match(rf"{re.escape(name)}\s+is\b", line, re.I) for line in lines)
                     or bool(tags) and all(direct(tag) for tag in tags))
            bound = all(any(re.match(rf"{re.escape(tag)}[^\n]*\bfor\s+{re.escape(name)}(?:\b|$)", line, re.I)
                            for line in lines) or direct(tag) for tag in tags)
            if named and tags and bound:
                added.append(expected)
        if added:
            lines = added + lines
            changed = True
    if (len(expected_heroes) == 1 and lines and "<Subject " not in sections.get("subject_definitions", "")
            and all(line.startswith(("<Video ", "<Picture ", "<Audio ")) for line in lines[1:])):
        label, raw_name = expected_heroes[0].groups()
        name = raw_name.split(" in <", 1)[0].strip()
        expected = next(line for line in skeleton.subject_lines if line.startswith(label + " "))
        expected_tags = {f"<{kind} {number}>" for kind, number in _LABEL_RE.findall(expected)
                         if kind != "Subject"}
        actual_tags = {f"<{kind} {number}>" for kind, number in _LABEL_RE.findall(lines[0])
                       if kind != "Subject"}
        possessive = re.match(rf"{re.escape(name)}[’']s\s+", lines[0], flags=re.I)
        retention = sections.get("retention_analysis", "")
        retention_match = re.search(rf"(?m)^{re.escape(name)}:\s*fully_preserved\b", retention, flags=re.I)
        if possessive and retention_match and actual_tags == expected_tags:
            lines[0] = f"{label} is {name}, whose " + lines[0][possessive.end():]
            sections["retention_analysis"] = (retention[:retention_match.start()] +
                f"{label} (appears in [Shot 1]): fully_preserved" + retention[retention_match.end():])
            changed = True
    for expected in skeleton.subject_lines:
        match = re.match(r"(<Subject \d+>) is ([^,.:]+)", expected)
        if not match:
            continue
        label, raw_name = match.groups()
        name = raw_name.split(" in <", 1)[0].strip()
        indices = [index for index, line in enumerate(lines) if line.startswith(label + " ")]
        if len(indices) != 1:
            continue
        index = indices[0]
        actual = lines[index]
        if not re.search(rf"\bis\s+{re.escape(name)}(?:\b|(?=\s))", actual, flags=re.I):
            continue
        expected_tags = {f"<{kind} {number}>" for kind, number in _LABEL_RE.findall(expected)
                         if kind != "Subject"}
        actual_tags = {f"<{kind} {number}>" for kind, number in _LABEL_RE.findall(actual)
                       if kind != "Subject"}
        if actual_tags - expected_tags:
            continue
        missing = expected_tags - actual_tags
        if not missing:
            continue
        additions = []
        for tag in sorted(missing):
            position = expected.index(tag)
            previous_tag_end = expected.rfind(">", 0, position)
            before = expected[max(previous_tag_end + 1, position - 105):position].lower()
            if "face and identity" in before:
                role = "face and identity"
            elif "full-body appearance" in before:
                role = "full-body appearance and silhouette"
            elif "costume" in before:
                role = "costume and carried props"
            elif "motion" in before or "performance" in before:
                role = "body motion and action timing"
            elif "environment" in before:
                role = "environment and layout"
            elif "object" in before:
                role = "object shape and material"
            elif "lighting" in before:
                role = "lighting"
            elif "color" in before:
                role = "color grade"
            else:
                role = "visual reference"
            additions.append(f"The {role} come from {tag}.")
        lines[index] = actual.rstrip() + " " + " ".join(additions)
        changed = True
    if not changed:
        return text
    sections["subject_definitions"] = "\n".join(lines)
    return render_ir(sections)


SOFT = "совет: "  # problems with this prefix are shown, never block the job


def is_blocking(problem: str) -> bool:
    return not problem.startswith(SOFT) and "слов" not in problem and "отрицаний" not in problem


def _subject_binding_problems(definitions: str, retention: str, skeleton: Skeleton) -> list[str]:
    """Hero pictures vs the prompt's <Subject N> lines, matched by pictures, not by number or name.

    Blocking: a hero's pictures missing from every Subject line, split across two lines, or one line
    mixing pictures of two heroes (that would give one hero the other's face). Advice only: the
    prompt's name differs from the Studio owner (owners are often Russian, the format is English),
    a costume tag is missing, or the Subject is absent from retention_analysis.
    """
    problems: list[str] = []
    heroes: list[tuple[str, set[str]]] = []
    for line in skeleton.subject_lines:
        match = re.match(r"<Subject \d+> is ([^,.:]+), whose", line)
        if match:
            tags = {f"<{kind} {num}>" for kind, num in _LABEL_RE.findall(line) if kind in {"Picture", "Video"}}
            heroes.append((match.group(1).split(" in <", 1)[0].strip(), tags))
    owner_of = {tag: name for name, tags in heroes for tag in tags if tag.startswith("<Picture")}
    actual: dict[str, tuple[str, set[str]]] = {}
    for line in definitions.splitlines():
        match = re.match(r"(<Subject \d+>)\s+is\s+([^,.:]+)", line)
        if match:
            tags = {f"<{kind} {num}>" for kind, num in _LABEL_RE.findall(line) if kind in {"Picture", "Video"}}
            actual[match.group(1)] = (match.group(2).strip(), tags)
    for label, (_, tags) in actual.items():
        mixed = sorted({owner_of[tag] for tag in tags if tag in owner_of})
        if len(mixed) > 1:
            problems.append(f"{label} смешивает картинки разных героев ({', '.join(mixed)}) — у героя будет чужое лицо")
    for name, tags in heroes:
        pictures = {tag for tag in tags if tag.startswith("<Picture")}
        lines = [label for label, (_, actual_tags) in actual.items() if actual_tags & pictures]
        if not lines:
            problems.append(f"картинки героя {name} ({', '.join(sorted(pictures))}) не упомянуты ни в одном <Subject N> — "
                            "проверь выбранное имя героя и явные ссылки на его фото лица и костюма в определении героя")
            continue
        if len(lines) > 1:
            problems.append(f"картинки героя {name} разнесены по {', '.join(lines)} — один герой = одна строка <Subject N>")
            continue
        label = lines[0]
        prompt_name, actual_tags = actual[label]
        if not pictures <= actual_tags:
            problems.append(SOFT + f"{label} ({name}): добавь в строку {', '.join(sorted(pictures - actual_tags))}")
        if prompt_name.casefold() != name.casefold():
            problems.append(SOFT + f"{label} в промпте «{prompt_name}», в Studio герой «{name}» — это нормально, "
                                   "если это один и тот же человек")
        if label not in retention:
            problems.append(SOFT + f"{label}: добавь строку в retention_analysis")
    return problems


_CYRILLIC_RE = re.compile("[А-Яа-яЁё]")
_I2VA_RE = re.compile(r"(?i)integrated_multimodal_description:|\bis fully referenced\b|^For the target video, at ")


def language_problems(text: str) -> list[str]:
    """Rule of 04.11.2026 (user): every section of the IR is English. Cyrillic is allowed only in dialogue
    (<d>…</d>) and in quoted on-screen text «…»; anything else blocks the job before the GPU queue."""
    problems = []
    for name, body in parse_ir(text).items():
        rest = re.sub(r"«[^»]*»", " ", re.sub(r"<d>.*?</d>", " ", body, flags=re.S))
        found = _CYRILLIC_RE.search(rest)
        if found:
            sample = rest[max(0, found.start() - 10):found.start() + 30].strip()
            problems.append(f"{name}: кириллица вне реплик («{sample}») — описания для H3 только на английском")
    return problems


def foreign_format_problem(text: str, declared: str | None) -> str | None:
    """A prompt in another MiniMax mode (I2VA keyframe syntax) sent to a Ref2VA job, or a job that declares the
    six-section IR (prompt_format="h3_ir") but has no sections. None when the prompt may go on."""
    text = str(text or "")
    if _I2VA_RE.search(text.lstrip()):
        return ("промпт в формате I2VA («fully referenced», integrated_multimodal_description): задание Ref2VA "
                "ждёт шесть секций или обычный текст")
    if declared == "h3_ir" and not is_ir_prompt(text):
        return "задание объявлено как H3 IR, но промпт не начинается с subject_definitions:"
    return None


def validate_ir(text: str, skeleton: Skeleton, shot_headers: list[str]) -> list[str]:
    """Format problems in Russian; empty list means the IR may go to H3."""
    problems: list[str] = []
    order = [m.group(1) for m in _SECTION_RE.finditer(text)]
    if order != list(SECTIONS):
        problems.append(f"секции не по формату: {order or 'нет'}; нужно {list(SECTIONS)}")
    sections = parse_ir(text)
    definitions = sections.get("subject_definitions", "")
    retention = sections.get("retention_analysis", "")
    problems += _subject_binding_problems(definitions, retention, skeleton)
    defined = {match.group(1) for match in re.finditer(r"(?m)^(<Subject \d+>)", definitions)}
    used = {f"<{kind} {num}>" for kind, num in _LABEL_RE.findall(text)}
    unknown = sorted(used - skeleton.labels - defined - {f"<Subject {n}>" for n in range(1, 1 + sum(
        1 for line in skeleton.subject_lines if line.startswith("<Subject")))})
    if unknown:
        problems.append("метки, которых нет в задании: " + ", ".join(unknown))
    body = sections.get("detailed_description", "")
    outside_dialogue = re.sub(r"<d>.*?</d>", " ", body, flags=re.S)
    words = len(re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", outside_dialogue))
    low, high = DESCRIPTION_WORDS
    if not low * 0.85 <= words <= high * 1.2:
        problems.append(f"detailed_description: {words} слов, по гайду {low}–{high}")
    for header in shot_headers:
        if header not in body:
            problems.append(f"нет заголовка шота «{header}»")
    if re.search(r"\[Shot 1\]\s*At\s+\d", body):
        problems.append("у [Shot 1] не должно быть времени")
    if re.search(r"<d>(?!\[[A-Za-z ]+\])", body):
        problems.append("реплика без языка: нужно <d>[Russian] ...</d>")
    negations = len(_NEGATION_RE.findall(outside_dialogue))
    if negations > 3:
        problems.append(f"{negations} отрицаний в описании: модель их не слушает, запреты пишутся утвердительно")
    problems += language_problems(text)
    return problems
