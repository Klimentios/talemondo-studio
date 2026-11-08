"""Монтажный лист (EDL): JSON plan of the cut → timeline → one FFmpeg assembly with transitions.

The editor's half of the pipeline: shot cards (shot_lint) carry `transition_in/out` from
app/director_schema.json; `edl_from_shots` turns them into a sheet, `plan` resolves every clip
against the real MP4s (in/out, speed, overlaps, J-cut audio leads, music, SFX) and `build_command`
assembles it. The shot itself must sell a whip pan, pass-through or morph (H3 renders the blur or
the transformation); the edit only bridges the seam with a short matching xfade.

Clips may overlap: an xfade of d seconds pulls the next clip d seconds earlier, so the film is d shorter than the
sum of its clips. The «Монтаж» page (montage.py) stores and edits exactly this sheet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contract import ASPECT_PATTERN, ASPECTS

from app.director_config import SCHEMA
from app.shot_lint import lint_shots
from . import grade as colour, motion, nle_export, sfx_bank
from .grade import Grade
from .retention import prune
from .assembly_receipt import assembly_snapshot, verify_sources, file_sha256
from .production_review import output_digest
from .storyboard import _tools, probe

# director_schema shot.transition_in value → (xfade effect or "cut", default seconds, audio lead seconds)
TRANSITIONS = {
    "cut": ("cut", 0, 0),
    "cut_on_action": ("cut", 0, 0),
    "match_cut": ("cut", 0, 0),
    "jump_cut": ("cut", 0, 0),
    "smash_cut": ("cut", 0, 0),
    "whip_pan": ("hblur", 0.25, 0),
    "pass_through": ("fadeblack", 0.3, 0),
    "morph": ("fade", 0.6, 0),
    "dissolve": ("fade", 0.75, 0),
    "fade": ("fadeblack", 1.0, 0),
    "sound_bridge": ("cut", 0, 0.6),
}
# Every look FFmpeg's xfade can draw: a seam may pick one instead of its default (transition_effect).
XFADE = {"fade", "wipeleft", "wiperight", "wipeup", "wipedown", "slideleft", "slideright", "slideup", "slidedown",
         "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial", "smoothleft", "smoothright",
         "smoothup", "smoothdown", "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
         "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr", "hlslice", "hrslice", "vuslice", "vdslice",
         "hblur", "fadegrays", "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin", "fadefast",
         "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind", "coverleft", "coverright", "coverup", "coverdown",
         "revealleft", "revealright", "revealup", "revealdown"}
# Picture accents on the film timeline: a camera shake with a punch-in, a punch-in alone, a white flash frame, a digital
# glitch, a chromatic split, a VHS tape fault, a strobe, a blur hit, a black-and-white hit, a negative flash. Each lands
# on a seam or a moment; strength 0..1 scales it.
ACCENTS = {"shake", "zoom", "flash", "glitch", "chroma", "vhs", "strobe", "blur", "bw", "invert"}
ACCENT_NAMES = {"shake": "тряска с наездом", "zoom": "резкий наезд", "flash": "вспышка", "glitch": "глитч",
                "chroma": "RGB-сдвиг", "vhs": "VHS-сбой", "strobe": "стробоскоп", "blur": "удар размытием",
                "bw": "ч/б удар", "invert": "негатив"}
DRAFT_SCALE = 0.5   # preview render: half size, fast x264, no loudness pass
# Sound design by seam (auto_sfx): bank effect, volume, where its anchor lands.
SEAM_SFX = {"whip_pan": ("whoosh", 0.55), "pass_through": ("whoosh_long", 0.5), "morph": ("whoosh_long", 0.45),
            "smash_cut": ("impact", 0.8), "cut_on_action": ("swish", 0.35), "jump_cut": ("swish", 0.25)}
# Delivery size per GPU aspect: short side 1080, long side from the ratio (16:9 → 1920×1080, 4:3 → 1440×1080).
SIZES = {a: (1080, round(1080 / r / 2) * 2) if r < 1 else (round(1080 * r / 2) * 2, 1080) for a, r in ASPECTS.items()}
MAX_CLIPS = 60


def _tempo(speed: float) -> str:
    # FFmpeg atempo needs factors >= 0.5; chaining also preserves quality above 2x.
    factors = []
    while speed < .5:
        factors.append(.5)
        speed /= .5
    while speed > 2:
        factors.append(2)
        speed /= 2
    factors.append(speed)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def schema_transitions() -> set[str]:
    field = next(f for f in SCHEMA["categories"]["shot"] if f["key"] == "transition_in")
    return {o["value"] for o in field["options"]}


class Source(BaseModel):
    job_id: str = Field(default="", max_length=120)
    asset_id: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def one_source(self):
        bank = getattr(self, "bank", "")
        if (bool(self.job_id) + bool(self.asset_id) + bool(bank)) != 1:
            raise ValueError("Укажи ровно одно: job_id (готовый шот H3), asset_id (файл Studio)"
                             + (" или bank (звук из встроенной библиотеки)" if hasattr(self, "bank") else ""))
        return self


class Accent(BaseModel):
    type: str = Field(pattern=f"^({'|'.join(sorted(ACCENTS))})$")
    offset: float = Field(default=0, ge=-3, le=60)   # seconds from the clip's start on the film (its seam)
    duration: float = Field(default=0.35, ge=0.04, le=2)
    strength: float = Field(default=0.7, ge=0.05, le=1)


class EdlClip(Source):
    model_config = ConfigDict(populate_by_name=True)
    label: str = Field(default="", max_length=120)
    in_s: float = Field(default=0, ge=0, le=3600, alias="in")
    out_s: float | None = Field(default=None, gt=0, le=3600, alias="out")  # None = to the end of the file
    speed: float = Field(default=1, ge=0.25, le=4)
    transition_in: str = "cut"
    transition_duration: float | None = Field(default=None, ge=0, le=3)
    transition_effect: str = ""   # any XFADE look instead of the transition's default
    freeze_end: float = Field(default=0, ge=0, le=4)  # hold the last frame (freeze-frame character intro)
    accents: list[Accent] = Field(default_factory=list, max_length=6)
    grade: Grade | None = None   # this shot's own correction (exposure, white balance…), before the film look
    audio: str = Field(default="source", pattern="^(source|mute)$")
    volume: float = Field(default=1, ge=0, le=2)
    shot: dict | None = None  # the shot card (shot_lint fields) this clip realises

    @model_validator(mode="after")
    def known_transition(self):
        if self.transition_in not in TRANSITIONS:
            raise ValueError(f"Неизвестный переход «{self.transition_in}». Возможные: {', '.join(TRANSITIONS)}")
        if self.out_s is not None and self.out_s <= self.in_s:
            raise ValueError("out должен быть больше in")
        if self.transition_effect and self.transition_effect not in XFADE:
            raise ValueError(f"Неизвестный вид перехода «{self.transition_effect}». Возможные: {', '.join(sorted(XFADE))}")
        return self


class Music(Source):
    volume: float = Field(default=0.35, ge=0, le=2)
    start: float = Field(default=0, ge=0, le=3600)      # place on the film timeline
    source_in: float = Field(default=0, ge=0, le=3600)
    fade_in: float = Field(default=0.5, ge=0, le=10)
    fade_out: float = Field(default=1.5, ge=0, le=10)
    duck: bool = True  # lower the music under dialogue and source sound


class Sfx(Source):
    bank: str = Field(default="", max_length=40)  # built-in effect (sfx_bank.BANK), placed by its anchor
    at: float = Field(ge=0, le=3600)
    source_in: float = Field(default=0, ge=0, le=3600)
    duration: float | None = Field(default=None, gt=0, le=60)
    volume: float = Field(default=1, ge=0, le=2)


class Graphic(BaseModel):
    template: str = Field(max_length=40)          # motion.TEMPLATES
    props: dict = Field(default_factory=dict)
    clip: int = Field(default=1, ge=1, le=MAX_CLIPS)   # 1-based clip it rides on, so it follows re-timing
    anchor: str = Field(default="start", pattern="^(start|freeze|end)$")  # clip start, its freeze frame, or its end
    offset: float = Field(default=0, ge=-3, le=60)     # seconds from the anchor
    duration: float = Field(default=2.5, ge=0.3, le=15)

    @model_validator(mode="after")
    def known_template(self):
        if self.template not in motion.TEMPLATES:
            raise ValueError(f"Нет шаблона графики «{self.template}». Есть: {', '.join(motion.TEMPLATES)}")
        if len(json.dumps(self.props, ensure_ascii=False)) > 8000:
            raise ValueError("Слишком большие параметры графики")
        return self


class Edl(BaseModel):
    version: int = Field(default=1, ge=1, le=1)
    title: str = Field(default="", max_length=200)
    aspect_ratio: str = Field(default="9:16", pattern=ASPECT_PATTERN)
    fps: int = Field(default=24, ge=12, le=60)
    fit: str = Field(default="pad", pattern="^(pad|crop)$")
    clips: list[EdlClip] = Field(min_length=1, max_length=MAX_CLIPS)
    music: Music | None = None
    sfx: list[Sfx] = Field(default_factory=list, max_length=40)
    fade_in: float = Field(default=0, ge=0, le=3)
    fade_out: float = Field(default=0, ge=0, le=5)
    target_seconds: float | None = Field(default=None, gt=0, le=3600)
    auto_sfx: bool = False     # sound design from the seams (SEAM_SFX), freeze frames and accents
    loudness: float | None = Field(default=-14, ge=-24, le=-9)  # integrated LUFS of the final mix; None = off
    grain: float = Field(default=0, ge=0, le=1)
    vignette: bool = False
    graphics: list[Graphic] = Field(default_factory=list, max_length=30)
    look: str = ""                 # grade.LOOKS, on the whole film
    look_intensity: float = Field(default=0.6, ge=0, le=1)
    grade: Grade | None = None     # film-wide primaries before the look
    match_color: bool = False      # pull every shot's colour toward the reference shot
    match_reference: int = Field(default=1, ge=1, le=MAX_CLIPS)
    match_strength: float = Field(default=0.7, ge=0, le=1)

    @model_validator(mode="after")
    def known_look(self):
        if self.look and self.look not in colour.LOOKS:
            raise ValueError(f"Нет лука «{self.look}». Есть: {', '.join(colour.LOOKS)}")
        if self.match_color and self.match_reference > len(self.clips):
            raise ValueError(f"Эталон цвета — клип {self.match_reference}, а в монтаже {len(self.clips)}")
        return self


class ShotsToEdl(BaseModel):
    """Shot cards (with the job_id or asset_id of their finished MP4) → a first sheet."""
    model_config = ConfigDict(extra="ignore")
    shots: list[dict] = Field(min_length=1, max_length=MAX_CLIPS)
    aspect_ratio: str = Field(default="9:16", pattern=ASPECT_PATTERN)
    title: str = Field(default="", max_length=200)
    music_asset_id: str = ""
    target_seconds: float | None = Field(default=None, gt=0, le=3600)
    trim_to_plan: bool = True  # cut each clip to its planned duration


def edl_from_shots(request: ShotsToEdl) -> dict:
    """Transition of a seam = incoming `transition_in`, else outgoing `transition_out`, else cut."""
    clips = []
    for index, shot in enumerate(request.shots):
        previous = request.shots[index - 1] if index else {}
        transition = shot.get("transition_in") or previous.get("transition_out") or "cut"
        if transition not in TRANSITIONS:
            transition = "cut"
        clip = {"job_id": shot.get("job_id", ""), "asset_id": shot.get("asset_id", ""),
                "label": shot.get("title") or f"Шот {index + 1}", "in": float(shot.get("source_in") or 0),
                "transition_in": transition if index else ("fade" if transition == "fade" else "cut"),
                "shot": {k: v for k, v in shot.items() if k not in {"job_id", "asset_id", "source_in"}}}
        if request.trim_to_plan and shot.get("duration"):
            clip["out"] = clip["in"] + float(shot["duration"])
        clips.append(clip)
    sheet = {"version": 1, "title": request.title, "aspect_ratio": request.aspect_ratio, "clips": clips,
             "fade_out": 0.5}
    if request.music_asset_id:
        sheet["music"] = {"asset_id": request.music_asset_id}
    if request.target_seconds:
        sheet["target_seconds"] = request.target_seconds
    return sheet


def plan(edl: Edl, media: dict[str, dict]) -> dict:
    """Timeline from the sheet and probed media ({source key: probe()}); raises ValueError on a bad cut."""
    clips, warnings = [], []
    end = 0.0
    for index, clip in enumerate(edl.clips):
        info = media[key(clip)]
        out = clip.out_s if clip.out_s is not None else info["duration"]
        if out > info["duration"] + 0.05:
            if clip.out_s is not None and clip.in_s < info["duration"] - 0.5:
                warnings.append(f"Клип {index + 1}: out {out:.2f} с длиннее файла ({info['duration']:.2f} с), обрезан")
                out = info["duration"]
            else:
                raise ValueError(f"Клип {index + 1}: in/out за пределами файла ({info['duration']:.2f} с)")
        length = (out - clip.in_s) / clip.speed + clip.freeze_end
        if length < 0.2:
            raise ValueError(f"Клип {index + 1}: короче 0,2 с")
        effect, default, lead = TRANSITIONS[clip.transition_in]
        if clip.transition_effect:  # the seam's own look; a cut that asks for one becomes a short xfade
            effect, default = clip.transition_effect, default or 0.25
        duration = 0.0
        if index and effect != "cut":
            duration = default if clip.transition_duration is None else clip.transition_duration
            limit = min(length, clips[-1]["length"]) / 2
            if duration > limit:
                raise ValueError(f"Клип {index + 1}: переход {duration:.2f} с длиннее половины соседнего клипа "
                                 f"({limit:.2f} с) — удлини клипы или сократи переход")
        if effect == "cut" and clip.transition_duration and lead:
            lead = clip.transition_duration
        start = end - duration
        audio_lead = 0.0
        if index and lead:
            audio_lead = min(lead, clip.in_s / clip.speed, clips[-1]["length"])
            if audio_lead < lead - 0.01:
                warnings.append(f"Клип {index + 1}: звуковой мост {audio_lead:.2f} с вместо {lead:.2f} с — "
                                f"до in мало звука в исходнике")
        if length < 0.8 and clip.transition_in != "jump_cut":
            warnings.append(f"Клип {index + 1}: {length:.2f} с — зритель может не успеть прочитать кадр")
        clips.append({"index": index + 1, "label": clip.label, "source": key(clip), "in": clip.in_s, "out": out,
                      "speed": clip.speed, "length": round(length, 4), "start": round(start, 4),
                      "end": round(start + length, 4),
                      "transition": {"type": clip.transition_in if index else "start", "effect": effect if index else "cut",
                                     "duration": duration},
                      "audio_lead": round(audio_lead, 4),
                      "audio": clip.audio == "source" and clip.volume > 0 and bool(info.get("audio"))})
        end = start + length
    total = round(end, 4)
    if edl.clips[0].transition_in == "fade" and not edl.fade_in:
        edl = edl.model_copy(update={"fade_in": TRANSITIONS["fade"][1]})
    if edl.fade_in + edl.fade_out > total:
        raise ValueError("Затемнения в начале и конце длиннее фильма")
    if edl.target_seconds and abs(total - edl.target_seconds) > max(1.0, edl.target_seconds * 0.1):
        warnings.append(f"Хронометраж {total:.1f} с, цель {edl.target_seconds:.0f} с")
    for item in edl.sfx:
        if item.at >= total:
            raise ValueError(f"Звук на {item.at:.2f} с — после конца фильма ({total:.2f} с)")
    accents = [{"type": accent.type, "start": round(max(0.0, item["start"] + accent.offset), 4),
                "end": round(min(total, item["start"] + accent.offset + accent.duration), 4), "strength": accent.strength}
               for clip, item in zip(edl.clips, clips) for accent in clip.accents
               if item["start"] + accent.offset < total]
    graphics = []
    for number, graphic in enumerate(edl.graphics, 1):
        if graphic.clip > len(clips):
            raise ValueError(f"Графика {number}: в монтаже нет клипа {graphic.clip}")
        item, source = clips[graphic.clip - 1], edl.clips[graphic.clip - 1]
        if graphic.anchor == "freeze" and not source.freeze_end:
            raise ValueError(f"Графика {number}: у клипа {graphic.clip} нет стоп-кадра (freeze_end)")
        base = {"start": item["start"], "freeze": item["end"] - source.freeze_end,
                "end": item["end"] - graphic.duration}[graphic.anchor]
        start = max(0.0, base + graphic.offset)
        if start >= total - 0.1:
            raise ValueError(f"Графика {number}: начинается после конца фильма")
        graphics.append({"template": graphic.template, "props": graphic.props, "start": round(start, 4),
                         "seconds": round(min(graphic.duration, total - start), 4)})
    sounds = [placed(item, media, total) for item in edl.sfx]
    if edl.auto_sfx:
        sounds += design_sfx(edl, clips, accents, total, graphics)
    if edl.music and edl.music.start >= total:
        raise ValueError("Музыка начинается после конца фильма")
    shots = [{**(clip.shot or {}), "duration": max(c["length"], 0.1), "transition_in": clip.transition_in}
             for clip, c in zip(edl.clips, clips) if clip.shot]
    review = lint_shots({"shots": shots}) if len(shots) == len(clips) else None
    return {"total": total, "clips": clips, "fade_in": edl.fade_in, "fade_out": edl.fade_out,
            "accents": accents, "graphics": graphics, "sfx": [sound for sound in sounds if sound], "loudness": edl.loudness,
            "warnings": warnings, "shot_review": review}


def placed(item: Sfx, media: dict, total: float, reason: str = "") -> dict | None:
    """A sound on the film: `at` is where a bank effect's anchor lands, or where a file starts."""
    if item.bank:
        if item.bank not in sfx_bank.BANK:
            raise ValueError(f"Нет звука «{item.bank}» во встроенной библиотеке. Есть: {', '.join(sfx_bank.BANK)}")
        start, length = item.at - sfx_bank.anchor(item.bank), sfx_bank.seconds(item.bank)
    else:
        start, length = item.at, media[key(item)]["duration"]
    source_in = item.source_in + max(0.0, -start)
    start = max(0.0, start)
    seconds = min(item.duration or length, length - source_in, total - start)
    if seconds <= 0.02:
        if item.bank:
            return None
        raise ValueError(f"Звук на {item.at:.2f} с: source_in за пределами файла")
    return {"source": key(item), "bank": item.bank, "start": round(start, 4), "source_in": round(source_in, 4),
            "seconds": round(seconds, 4), "volume": item.volume, "reason": reason}


GRAPHIC_SFX = {"KineticTitle": ("hit", 0.55), "LowerThird": ("swish", 0.35), "TitleCard": ("boom", 0.5),
               "NameCard": ("swish", 0.4)}


def design_sfx(edl: Edl, clips: list[dict], accents: list[dict], total: float, graphics: list[dict] = ()) -> list[dict]:
    """Sound design from the cut: every seam, freeze frame and accent gets its effect, anchored on the frame."""
    wanted: list[tuple[str, float, float, str]] = []
    for index, (clip, item) in enumerate(zip(edl.clips, clips)):
        seam = item["start"] + item["transition"]["duration"] / 2
        if index and clip.transition_in in SEAM_SFX:
            name, volume = SEAM_SFX[clip.transition_in]
            wanted.append((name, seam, volume, f"шов {index}→{index + 1}: {clip.transition_in}"))
            if clip.transition_in == "smash_cut" and clips[index - 1]["length"] >= 2:
                wanted.append(("riser_short", seam, 0.3, f"нарастание к удару шва {index}→{index + 1}"))
        if clip.freeze_end:
            frozen = item["end"] - clip.freeze_end
            wanted += [("shutter", frozen, 0.6, f"стоп-кадр шота {index + 1}"), ("hit", frozen, 0.45, f"стоп-кадр шота {index + 1}")]
    for graphic in graphics:
        if graphic["template"] in GRAPHIC_SFX:
            name, volume = GRAPHIC_SFX[graphic["template"]]
            wanted.append((name, graphic["start"] + 0.04, volume, f"графика {graphic['template']}"))
    for accent in accents:
        if accent["type"] == "glitch":
            wanted.append(("glitch", accent["start"], 0.45, "глитч"))
        elif accent["type"] == "shake":
            wanted.append(("hit", accent["start"], 0.5 * accent["strength"] + 0.2, "тряска камеры"))
    out, seen = [], []
    for name, at, volume, reason in sorted(wanted, key=lambda w: w[1]):
        if any(n == name and abs(t - at) < 0.25 for n, t in seen) or at >= total:
            continue
        seen.append((name, at))
        out.append(placed(Sfx(bank=name, at=at, volume=volume), {}, total, reason))
    return out


def key(source: Source) -> str:
    if getattr(source, "bank", ""):
        return f"bank:{source.bank}"
    return f"job:{source.job_id}" if source.job_id else f"asset:{source.asset_id}"


def resolve(store, source: Source) -> Path:
    if source.job_id:
        job = store.get_job(source.job_id)
        if not job or job.get("state") != "completed" or not job.get("output_path"):
            raise ValueError(f"Шот {source.job_id[:8]} не готов или не найден")
        _, checked_path, _ = output_digest(store, source.job_id)
        path, root = checked_path.resolve(), store.results_dir.resolve()
    else:
        asset = store.get_asset(source.asset_id)
        if not asset:
            raise ValueError(f"Файл {source.asset_id[:8]} не найден")
        path, root = Path(asset["path"]).resolve(), store.assets_dir.resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError(f"Файл {path.name} недоступен")
    return path


def probe_media(store, edl: Edl, ffprobe: str) -> tuple[dict[str, Path], dict[str, dict]]:
    paths, media = {}, {}
    for source in [*edl.clips, *([edl.music] if edl.music else []), *edl.sfx]:
        name = key(source)
        if name in paths or name.startswith("bank:"):
            continue
        paths[name] = resolve(store, source)
        if isinstance(source, EdlClip):
            media[name] = probe(paths[name], ffprobe)
        else:
            media[name] = audio_probe(paths[name], ffprobe)
    return paths, media


def audio_probe(path: Path, ffprobe: str) -> dict:
    import subprocess
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                             "format=duration:stream=index", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=30, check=False)
    data = json.loads(result.stdout or "{}")
    if result.returncode or not data.get("streams"):
        raise ValueError(f"В {path.name} нет звука")
    return {"duration": float(data.get("format", {}).get("duration") or 0), "audio": True}


def frame_size(edl: Edl, media: dict) -> tuple[int, int]:
    """First clip's own size when it already has the sheet's orientation, else 1080p of that orientation."""
    video = media[key(edl.clips[0])]["video"]
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    want = SIZES[edl.aspect_ratio]
    if width and height and abs(width / height - want[0] / want[1]) < 0.02:
        return width - width % 2, height - height % 2
    return want


def picture_finish(edl: Edl, timeline: dict, width: int, height: int, fps: int) -> list[str]:
    """Accents on the film timeline (shake with punch-in, flash, glitch, chroma) and the film look."""
    out = []

    def windows(kind, weight=None):
        items = [a for a in timeline["accents"] if a["type"] == kind]
        if weight:
            return "+".join(f"between(in_time,{a['start']:.3f},{a['end']:.3f})*{a['strength']:.2f}"
                            f"*exp(-4*(in_time-{a['start']:.3f}))" for a in items)
        return "+".join(f"between(t,{a['start']:.3f},{a['end']:.3f})" for a in items)

    shake = windows("shake", weight=True)
    punch = "+".join(f"between(in_time,{a['start']:.3f},{a['end']:.3f})*{a['strength']:.2f}"
                     for a in timeline["accents"] if a["type"] == "zoom")
    if shake or punch:
        # zoompan per frame: zoom 1 (identity) outside the windows; shake = a decaying punch-in with jitter,
        # zoom = a held punch-in (up to +25 %).
        amp = round(width * 0.03)
        jitter = shake or "0"
        out.append(f"zoompan=z='1+0.07*({jitter})+0.25*({punch or '0'})'"
                   f":x='iw/2-(iw/zoom/2)+{amp}*({jitter})*sin(2*PI*17*in_time)'"
                   f":y='ih/2-(ih/zoom/2)+{amp}*({jitter})*cos(2*PI*13*in_time)':d=1:s={width}x{height}:fps={fps}")
    for accent in timeline["accents"]:
        span = f"enable='between(t,{accent['start']:.3f},{accent['end']:.3f})'"
        k = accent["strength"]
        if accent["type"] == "flash":
            out.append(f"eq=brightness={0.5 * k:.3f}:{span}")
        elif accent["type"] == "glitch":
            out.append(f"rgbashift=rh={-round(18 * k)}:bh={round(18 * k)}:gv={round(6 * k)}:{span}")
            out.append(f"noise=alls={round(40 * k)}:allf=t:{span}")
        elif accent["type"] == "chroma":
            out.append(f"rgbashift=rh={-round(8 * k)}:bh={round(8 * k)}:{span}")
        elif accent["type"] == "vhs":
            out.append(f"rgbashift=rh={-round(10 * k)}:rv={round(3 * k)}:bh={round(6 * k)}:{span}")
            out.append(f"noise=alls={round(28 * k)}:allf=t+u:{span}")
            out.append(f"eq=saturation={1 - 0.45 * k:.3f}:contrast={1 + 0.15 * k:.3f}:{span}")
        elif accent["type"] == "strobe":
            out.append(f"eq=brightness='{0.55 * k:.3f}*mod(floor(t*16),2)':eval=frame:{span}")
        elif accent["type"] == "blur":
            out.append(f"gblur=sigma={2 + 14 * k:.1f}:{span}")
        elif accent["type"] == "bw":
            out.append(f"hue=s=0:{span}")
            out.append(f"eq=contrast={1 + 0.35 * k:.3f}:{span}")
        elif accent["type"] == "invert":
            out.append(f"negate=enable={span.removeprefix('enable=')}")
    if edl.vignette:
        out.append("vignette=angle=PI/5")
    if edl.grain:
        out.append(f"noise=alls={round(4 + 10 * edl.grain)}:allf=t")
    return out


def normalize_loudness(path: Path, target: float, ffmpeg: str) -> dict:
    """Two-pass EBU R128 loudnorm of the finished MP4's audio (true peak −1 dBTP), video copied."""
    import subprocess
    measure = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af",
                              f"loudnorm=I={target}:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=600, check=True)
    data = json.loads(measure.stderr[measure.stderr.rindex("{"):measure.stderr.rindex("}") + 1])
    temp = path.with_name(path.stem + ".loud.mp4")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-map", "0", "-c:v", "copy",
                    "-af", f"loudnorm=I={target}:TP=-1:LRA=11:measured_I={data['input_i']}:measured_TP={data['input_tp']}"
                           f":measured_LRA={data['input_lra']}:measured_thresh={data['input_thresh']}"
                           f":offset={data['target_offset']}:linear=true,aresample=48000",
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(temp)],
                   check=True, timeout=600, capture_output=True)
    temp.replace(path)
    return {"input_lufs": float(data["input_i"]), "target_lufs": target}


def build_command(edl: Edl, timeline: dict, paths: dict, media: dict, output: Path, ffmpeg: str,
                  draft: bool = False) -> list[str]:
    fps, (width, height) = edl.fps, frame_size(edl, media)
    total = timeline["total"]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    filters, audio = [], []
    inputs = 0

    def add_input(path, start, seconds):
        nonlocal inputs
        cmd.extend(["-ss", f"{start:.4f}", "-t", f"{seconds:.4f}", "-i", str(path)])
        inputs += 1
        return inputs - 1

    fit = (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if edl.fit == "crop"
           else f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2")
    current = ""
    for i, (clip, item) in enumerate(zip(edl.clips, timeline["clips"])):
        n = add_input(paths[item["source"]], item["in"], item["out"] - item["in"])
        hold = f"tpad=stop_mode=clone:stop_duration={clip.freeze_end:.4f}," if clip.freeze_end else ""
        fix = "".join(f"{f}," for f in [*([timeline["match"][str(i)]] if str(i) in timeline.get("match", {}) else []),
                                         *colour.primaries(clip.grade)])
        filters.append(f"[{n}:v]setpts=(PTS-STARTPTS)/{clip.speed:.6f},fps={fps},{fit},setsar=1,{fix}format=yuv420p,{hold}"
                       f"trim=duration={item['length']:.4f},setpts=PTS-STARTPTS,settb=1/{fps}[v{i}]")
        if i == 0:
            current = "v0"
        elif item["transition"]["effect"] == "cut":
            filters.append(f"[{current}][v{i}]concat=n=2:v=1:a=0,settb=1/{fps}[c{i}]")
            current = f"c{i}"
        else:
            filters.append(f"[{current}][v{i}]xfade=transition={item['transition']['effect']}:"
                           f"duration={item['transition']['duration']:.4f}:offset={item['start']:.4f}[c{i}]")
            current = f"c{i}"
        if not item["audio"]:
            continue
        lead = item["audio_lead"]
        a_start = item["start"] - lead
        a_len = item["length"] + lead
        n = add_input(paths[item["source"]], item["in"] - lead * clip.speed, item["out"] - item["in"] + lead * clip.speed)
        nxt = timeline["clips"][i + 1] if i + 1 < len(timeline["clips"]) else None
        fade_in = lead or item["transition"]["duration"] or (0 if clip.transition_in == "smash_cut" else 0.01)
        fade_out = (nxt["audio_lead"] or nxt["transition"]["duration"] or 0.01) if nxt else 0
        delay = round(a_start * 1000)
        filters.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,{_tempo(clip.speed)},"
                       f"volume={clip.volume:.3f},atrim=duration={a_len:.4f},asetpts=PTS-STARTPTS,"
                       f"afade=t=in:d={fade_in:.4f}"
                       + (f",afade=t=out:st={max(a_len - fade_out, 0):.4f}:d={fade_out:.4f}" if fade_out else "")
                       + f",adelay={delay}:all=1[a{i}]")
        audio.append(f"a{i}")
    for j, sound in enumerate(timeline["sfx"]):
        n = add_input(paths[sound["source"]], sound["source_in"], sound["seconds"])
        filters.append(f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={sound['volume']:.3f},"
                       f"asetpts=PTS-STARTPTS,adelay={round(sound['start'] * 1000)}:all=1[s{j}]")
        audio.append(f"s{j}")
    film = colour.primaries(edl.grade)
    if film:
        filters.append(f"[{current}]{','.join(film)}[graded]")
        current = "graded"
    if edl.look and edl.look_intensity > 0:
        filters += colour.look_graph(current, "looked", edl.look, edl.look_intensity)
        current = "looked"
    fades = picture_finish(edl, timeline, width, height, fps)
    if timeline["graphics"]:
        # Titles sit above the accents and the film look (they must not shake or grain); fades cover them too.
        filters.append(f"[{current}]" + ",".join(fades or ["null"]) + "[look]")
        current, fades = "look", []
        for k, graphic in enumerate(timeline["graphics"]):
            n = add_input(paths[f"graphic:{k}"], 0, graphic["seconds"])
            end = graphic["start"] + graphic["seconds"]
            filters.append(f"[{n}:v]scale={width}:{height},format=yuva420p,"
                           f"setpts=PTS-STARTPTS+{graphic['start']:.4f}/TB[g{k}]")
            filters.append(f"[{current}][g{k}]overlay=eof_action=pass:format=auto:"
                           f"enable='between(t,{graphic['start']:.4f},{end:.4f})'[o{k}]")
            current = f"o{k}"
    if timeline["fade_in"]:
        fades.append(f"fade=t=in:st=0:d={timeline['fade_in']:.4f}")
    if timeline["fade_out"]:
        fades.append(f"fade=t=out:st={total - timeline['fade_out']:.4f}:d={timeline['fade_out']:.4f}")
    if draft:
        fades.append(f"scale={round(width * DRAFT_SCALE / 2) * 2}:{round(height * DRAFT_SCALE / 2) * 2}")
    filters.append(f"[{current}]" + ",".join(fades or ["null"]) + f",trim=duration={total:.4f}[vout]")
    mix = "srcmix"
    if audio:
        filters.append("".join(f"[{a}]" for a in audio) + f"amix=inputs={len(audio)}:duration=longest:normalize=0,"
                       f"apad,atrim=duration={total:.4f}[srcmix]")
    else:
        n = inputs
        cmd.extend(["-f", "lavfi", "-t", f"{total:.4f}", "-i", "anullsrc=r=48000:cl=stereo"])
        inputs += 1
        filters.append(f"[{n}:a]atrim=duration={total:.4f}[srcmix]")
    if edl.music:
        music = edl.music
        info = media[key(music)]
        seconds = min(info["duration"] - music.source_in, total - music.start)
        if seconds <= 0:
            raise ValueError("Музыка: source_in за пределами файла")
        n = add_input(paths[key(music)], music.source_in, seconds)
        chain = (f"[{n}:a]aresample=48000,aformat=channel_layouts=stereo,volume={music.volume:.3f},asetpts=PTS-STARTPTS,"
                 f"afade=t=in:d={min(music.fade_in, seconds / 2):.4f},"
                 f"afade=t=out:st={max(seconds - music.fade_out, 0):.4f}:d={min(music.fade_out, seconds):.4f},"
                 f"adelay={round(music.start * 1000)}:all=1,apad,atrim=duration={total:.4f}")
        if music.duck and audio:
            filters.append(f"{chain}[mus]")
            filters.append("[srcmix]asplit=2[src1][src2]")
            filters.append("[mus][src2]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=400[mduck]")
            filters.append("[src1][mduck]amix=inputs=2:duration=first:normalize=0[premix]")
        else:
            filters.append(f"{chain}[mus]")
            filters.append("[srcmix][mus]amix=inputs=2:duration=first:normalize=0[premix]")
        mix = "premix"
    final_fades = []
    if timeline["fade_in"]:
        final_fades.append(f"afade=t=in:d={timeline['fade_in']:.4f}")
    if timeline["fade_out"]:
        final_fades.append(f"afade=t=out:st={total - timeline['fade_out']:.4f}:d={timeline['fade_out']:.4f}")
    filters.append(f"[{mix}]" + ",".join([*final_fades, "alimiter=limit=0.95"]) + "[aout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast" if draft else "medium", "-crf", "26" if draft else "14",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "160k" if draft else "256k", "-ar", "48000", "-movflags", "+faststart", "-t", f"{total:.4f}", str(output)]
    return cmd


def make_router(store) -> APIRouter:
    router = APIRouter()
    root = store.root / "edl_renders"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    tasks: dict[str, asyncio.Task] = {}
    log = logging.getLogger("h3.studio")

    def prepare(edl: Edl):
        ffmpeg, ffprobe = _tools()
        paths, media = probe_media(store, edl, ffprobe)
        timeline = plan(edl, media)
        for sound in timeline["sfx"]:
            if sound["bank"]:
                paths[sound["source"]] = sfx_bank.render(sound["bank"], store.root, ffmpeg)
        if edl.match_color:
            def frames(item):
                return colour.sample(paths[item["source"]], item["in"], item["out"] - item["in"], ffmpeg)
            reference = frames(timeline["clips"][edl.match_reference - 1])
            timeline["match"] = {str(i): colour.match_filter(frames(item), reference, edl.match_strength)
                                 for i, item in enumerate(timeline["clips"]) if i != edl.match_reference - 1}
        return ffmpeg, ffprobe, paths, media, timeline

    def draw_graphics(edl: Edl, timeline: dict, paths: dict, media: dict) -> None:
        width, height = frame_size(edl, media)
        for k, graphic in enumerate(timeline["graphics"]):
            paths[f"graphic:{k}"] = motion.render(graphic["template"], graphic["props"], width, height, edl.fps,
                                                  graphic["seconds"], store.root)

    def write_status(render_id: str, state: dict) -> None:
        temp = root / f".{render_id}.state.tmp"
        temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, root / f"{render_id}.state")

    @router.get("/api/edl/transitions")
    async def transitions() -> dict:
        return {name: {"effect": effect, "seconds": seconds, "audio_lead": lead,
                       **({"sfx": SEAM_SFX[name][0]} if name in SEAM_SFX else {})}
                for name, (effect, seconds, lead) in TRANSITIONS.items()}

    @router.get("/api/edl/toolkit")
    async def toolkit() -> dict:
        """Everything the edit can do, for the agent and the edit sheet UI."""
        return {"transitions": await transitions(), "effects": sorted(XFADE), "accents": sorted(ACCENTS),
                "accent_names": ACCENT_NAMES,
                "sfx_bank": sfx_bank.describe(), "graphics": motion.describe(), "graphics_ready": motion.ready(),
                "looks": colour.describe(), "grade_controls": Grade.model_json_schema()["properties"],
                "loudness_default": -14}

    @router.post("/api/edl/from-shots")
    async def from_shots(payload: ShotsToEdl) -> dict:
        return edl_from_shots(payload)

    @router.post("/api/edl/plan")
    async def make_plan(payload: Edl) -> dict:
        try:
            *_, timeline = await asyncio.to_thread(prepare, payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return timeline

    @router.post("/api/edl/renders", status_code=202)
    async def render(payload: Edl, draft: bool = Query(False)) -> dict:
        """draft=1: the Montage preview — half size, fast encode, no loudness pass or colour QC."""
        render_id = uuid.uuid4().hex
        output = root / f"{render_id}.mp4"
        try:
            ffmpeg, ffprobe, paths, media, timeline = await asyncio.to_thread(prepare, payload)
            await asyncio.to_thread(draw_graphics, payload, timeline, paths, media)
            cmd = build_command(payload, timeline, paths, media, output, ffmpeg, draft=draft)
            snapshot = await asyncio.to_thread(assembly_snapshot, paths,
                {"edl": payload.model_dump(by_alias=True), "plan": timeline, "draft": draft})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        (root / f"{render_id}.json").write_text(json.dumps({"edl": payload.model_dump(by_alias=True), "plan": timeline,
                                                            "snapshot": snapshot, "command": cmd},
                                                           ensure_ascii=False), encoding="utf-8")
        write_status(render_id, {"state": "running"})

        async def run():
            try:
                await asyncio.to_thread(verify_sources, paths, snapshot)
                process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.PIPE)
                try:
                    _, stderr = await asyncio.wait_for(process.communicate(), timeout=3600)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise RuntimeError("Сборка дольше часа — остановлена")
                except asyncio.CancelledError:
                    process.kill()
                    await process.wait()
                    output.unlink(missing_ok=True)
                    write_status(render_id, {"state": "cancelled"})
                    raise
                if process.returncode or not output.is_file():
                    raise RuntimeError((stderr or b"").decode("utf-8", "replace")[-600:] or "FFmpeg завершился с ошибкой")
                loud = (await asyncio.to_thread(normalize_loudness, output, payload.loudness, ffmpeg)
                        if payload.loudness is not None and not draft else None)
                info = await asyncio.to_thread(probe, output, ffprobe)
                color_qc = [] if draft else await asyncio.to_thread(colour.qc, output, timeline["clips"], ffmpeg)
                if abs(info["duration"] - timeline["total"]) > 0.25 or not info["audio"]:
                    raise RuntimeError(f"Проверка MP4 не прошла: {info['duration']:.2f} с вместо {timeline['total']:.2f} с"
                                       + ("" if info["audio"] else ", нет звука"))
                await asyncio.to_thread(verify_sources, paths, snapshot)
                output_hash = await asyncio.to_thread(file_sha256, output)
                write_status(render_id, {"state": "completed", "output_sha256": output_hash,
                                         "input_fingerprint_sha256": snapshot["input_fingerprint_sha256"], "draft": draft, "duration": info["duration"], "loudness": loud,
                                         "color_qc": color_qc,
                                         "url": f"/api/edl/renders/{render_id}/file", "path": str(output)})
                try:
                    await asyncio.to_thread(prune, root, store, busy=set(tasks))
                    await asyncio.to_thread(motion.prune, store.root)
                    for state in root.glob("*.state"):
                        if not (root / f"{state.stem}.mp4").exists() and state.stem not in tasks:
                            state.unlink(missing_ok=True)
                except Exception:
                    log.exception("Монтажный лист: старые сборки не удалены")
            except Exception as exc:
                output.unlink(missing_ok=True)
                write_status(render_id, {"state": "failed", "detail": str(exc)})

        task = asyncio.create_task(run())
        tasks[render_id] = task
        task.add_done_callback(lambda _: tasks.pop(render_id, None))
        return {"render_id": render_id, "state": "running", "total": timeline["total"], "warnings": timeline["warnings"],
                "plan": timeline}

    @router.delete("/api/edl/renders/{render_id}")
    async def cancel_render(render_id: str) -> dict:
        """Stop a running assembly (the Montage drops a stale preview when the cut changes again)."""
        task = tasks.get(check_id(render_id))
        if task:
            task.cancel()
        return {"render_id": render_id, "cancelled": bool(task)}

    exports = store.root / "edl_exports"

    @router.post("/api/edl/exports")
    async def export(payload: Edl) -> dict:
        """The sheet as editable timelines: Premiere (STUDIO.xml), Resolve/FCP (STUDIO.fcpxml), media, look.cube."""
        export_id = uuid.uuid4().hex
        folder = exports / export_id

        def build():
            ffmpeg, ffprobe, paths, media, timeline = prepare(payload)
            draw_graphics(payload, timeline, paths, media)
            folder.mkdir(parents=True)
            return nle_export.bundle(payload, timeline, paths, media, folder, ffmpeg, frame_size(payload, media))

        try:
            written = await asyncio.to_thread(build)
        except ValueError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(422, str(exc)) from exc
        for old in sorted((p for p in exports.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)[:-10]:
            shutil.rmtree(old, ignore_errors=True)
            (exports / f"{old.name}.zip").unlink(missing_ok=True)
        return {"export_id": export_id, "folder": str(folder), **written,
                "zip": f"/api/edl/exports/{export_id}/zip"}

    def export_folder(export_id: str) -> Path:
        folder = exports / check_id(export_id)
        if not folder.is_dir():
            raise HTTPException(404, "Экспорт не найден")
        return folder

    @router.get("/api/edl/exports/{export_id}/zip")
    async def export_zip(export_id: str) -> FileResponse:
        folder = export_folder(export_id)
        archive = exports / f"{export_id}.zip"
        if not archive.is_file():
            await asyncio.to_thread(shutil.make_archive, str(archive.with_suffix("")), "zip", folder)
        return FileResponse(archive, media_type="application/zip", filename=f"STUDIO-edit-{export_id[:8]}.zip")

    @router.post("/api/edl/exports/{export_id}/reveal")
    async def export_reveal(export_id: str) -> dict:
        folder = export_folder(export_id)
        await asyncio.to_thread(subprocess.run, ["open", str(folder)], check=False, timeout=10)
        return {"opened": str(folder)}

    @router.post("/api/edl/exports/{export_id}/resolve")
    async def export_resolve(export_id: str) -> dict:
        folder = export_folder(export_id)
        try:
            return await asyncio.to_thread(nle_export.import_to_resolve, folder, f"STUDIO {export_id[:6]}")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def check_id(render_id: str) -> str:
        if len(render_id) != 32 or not all(c in "0123456789abcdef" for c in render_id):
            raise HTTPException(404, "Сборка не найдена")
        return render_id

    @router.get("/api/edl/renders/{render_id}")
    async def render_status(render_id: str) -> dict:
        path = root / f"{check_id(render_id)}.state"
        if not path.is_file():
            raise HTTPException(404, "Сборка не найдена")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state["state"] == "running" and render_id not in tasks:
            state = {"state": "failed", "detail": "Сборка прервана перезапуском Studio"}
            write_status(render_id, state)
        return state

    @router.get("/api/edl/renders/{render_id}/file")
    async def render_file(render_id: str) -> FileResponse:
        path = root / f"{check_id(render_id)}.mp4"
        if not path.is_file():
            raise HTTPException(404, "Готовый MP4 не найден")
        return FileResponse(path, media_type="video/mp4", filename=f"studio-edl-{render_id[:8]}.mp4")

    return router
