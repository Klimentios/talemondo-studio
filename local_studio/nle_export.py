"""Edit sheet → editable timelines for Premiere Pro (FCP7 XML, xmeml v4) and DaVinci Resolve / Final Cut
(FCPXML 1.10), with every source in one folder.

The film's timeline (edl.plan) overlaps clips under xfades; an NLE wants clips butted at a cut with handles.
Each clip therefore spans from the middle of its incoming seam to the middle of its outgoing one, and a
transition of the same length sits on the seam (its handles come from the overlap). Freeze frames become a
still of the last frame. Tracks: V1 shots, V2+ titles (ProRes 4444 with alpha), A1–A2 shot sound (alternating,
so J-cut leads can overlap), A3 music, A4+ sound effects. What an NLE cannot rebuild — shake, glitch, chroma,
xfade looks other than dissolve/dip, shot colour matching — is listed as markers; the film look goes along as a
.cube LUT for an adjustment layer (Lumetri «Creative Look» / a Resolve node).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image

from . import grade as colour

# xfade look → what the NLE draws on that seam
NLE_TRANSITION = {"fadeblack": "Dip to Black", "fadewhite": "Dip to White"}
DISSOLVE = "Cross Dissolve"


def url(path: Path) -> str:
    return "file://" + urllib.parse.quote(str(path))


def link(source: Path, folder: Path, name: str) -> Path:
    """Hard link into the export folder (same disk as the Studio data), copy otherwise."""
    target = folder / name
    if not target.exists():
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return target


def layout(edl, timeline: dict) -> list[dict]:
    """Butted segments of the picture track: (clip index, record start/end, source in, still or not)."""
    clips = timeline["clips"]
    seams = [c["start"] + c["transition"]["duration"] / 2 for c in clips] + [timeline["total"]]
    seams[0] = 0.0
    out = []
    for i, (clip, item) in enumerate(zip(edl.clips, clips)):
        rec_in, rec_out = seams[i], seams[i + 1]
        freeze_at = item["end"] - clip.freeze_end if clip.freeze_end else rec_out
        moving_out = min(rec_out, freeze_at)
        if moving_out > rec_in:
            out.append({"clip": i, "start": rec_in, "end": moving_out,
                        "source_in": item["in"] + (rec_in - item["start"]) * clip.speed, "still": False})
        if clip.freeze_end and rec_out > freeze_at:
            out.append({"clip": i, "start": max(rec_in, freeze_at), "end": rec_out, "source_in": 0.0, "still": True})
    return out


def markers(edl, timeline: dict) -> list[tuple[float, str]]:
    notes = []
    for i, (clip, item) in enumerate(zip(edl.clips, timeline["clips"])):
        effect = item["transition"]["effect"]
        if i and effect not in {"cut", "fade", "dissolve", *NLE_TRANSITION}:
            notes.append((item["start"], f"Studio: переход «{effect}» — в монтажке заменён растворением"))
        if clip.speed != 1:
            notes.append((item["start"], f"Studio: скорость шота ×{clip.speed:g} — задай Speed/Duration"))
        if clip.grade:
            notes.append((item["start"], "Studio: своя коррекция шота (экспозиция/баланс) — повтори в цвете"))
    for accent in timeline["accents"]:
        notes.append((accent["start"], f"Studio: акцент «{accent['type']}» {accent['end'] - accent['start']:.2f} с"))
    if timeline.get("match"):
        notes.append((0.0, "Studio: цвет шотов выровнен по эталону — в монтажке шоты исходные"))
    return sorted(notes)


def look_lut(edl, path: Path, ffmpeg: str, size: int = 33) -> Path | None:
    """The film look (and film-wide primaries) as a .cube LUT, already mixed at look_intensity."""
    chain = colour.primaries(edl.grade)
    if edl.look and edl.look_intensity > 0:
        chain.append(colour.LOOKS[edl.look][1])
    if not chain:
        return None
    level = 8  # hald level 8 = 64³ samples
    hald = path.with_suffix(".hald.png")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"haldclutsrc=level={level}",
                    "-frames:v", "1", "-vf", f"format=rgb24,{','.join(chain)},format=rgb24", str(hald)],
                   check=True, timeout=120)
    n = level * level
    graded = Image.open(hald).convert("RGB").getdata()
    hald.unlink(missing_ok=True)
    k = edl.look_intensity if edl.look else 1.0
    lines = [f'TITLE "STUDIO {edl.look or "grade"}"', f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                # nearest hald sample (hald index: r fastest, then g, then b)
                ri, gi, bi = (round(v * (n - 1) / (size - 1)) for v in (r, g, b))
                px = graded[ri + gi * n + bi * n * n]
                ident = (r / (size - 1), g / (size - 1), b / (size - 1))
                lines.append(" ".join(f"{ident[c] + k * (px[c] / 255 - ident[c]):.6f}" for c in range(3)))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def lanes(items: list[dict]) -> list[int]:
    """Track per item so overlapping items never share one (greedy)."""
    ends: list[float] = []
    out = []
    for item in items:
        lane = next((k for k, end in enumerate(ends) if end <= item["start"] + 1e-6), len(ends))
        if lane == len(ends):
            ends.append(0.0)
        ends[lane] = item["start"] + item["seconds"]
        out.append(lane)
    return out


def bundle(edl, timeline: dict, paths: dict, media: dict, folder: Path, ffmpeg: str, size: tuple[int, int]) -> dict:
    """Write media/, STUDIO.xml (Premiere), STUDIO.fcpxml (Resolve/FCP), look.cube; return what was written."""
    files = folder / "media"
    files.mkdir(parents=True, exist_ok=True)
    fps = edl.fps
    width, height = size
    title = edl.title or "STUDIO edit"
    sources = {}
    for i, item in enumerate(timeline["clips"]):
        src = Path(paths[item["source"]])
        sources[i] = link(src, files, f"shot{i + 1:02d}{src.suffix.lower()}")
    stills = {}
    for segment in layout(edl, timeline):
        if segment["still"] and segment["clip"] not in stills:
            item = timeline["clips"][segment["clip"]]
            still = files / f"shot{segment['clip'] + 1:02d}_freeze.png"
            # The last frame before `out`: decode a short tail and keep overwriting one PNG (a seek exactly at the
            # file's end yields no frame at all).
            tail = min(0.4, item["out"])
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{item['out'] - tail:.4f}", "-t",
                            f"{tail:.4f}", "-i", str(paths[item["source"]]), "-update", "1", str(still)],
                           check=True, timeout=60)
            if not still.is_file():
                raise ValueError(f"Не удалось взять стоп-кадр шота {segment['clip'] + 1}")
            stills[segment["clip"]] = still
    music = None
    if edl.music:
        source = Path(paths[f"{'job' if edl.music.job_id else 'asset'}:{edl.music.job_id or edl.music.asset_id}"])
        music = link(source, files, f"music{source.suffix.lower()}")
    sfx = []
    for k, sound in enumerate(timeline["sfx"]):
        source = Path(paths[sound["source"]])
        sfx.append({**sound, "file": link(source, files, f"sfx{k + 1:02d}_{sound['bank'] or 'file'}{source.suffix.lower()}")})
    graphics = []
    for k, graphic in enumerate(timeline["graphics"]):
        source = Path(paths[f"graphic:{k}"])
        graphics.append({**graphic, "file": link(source, files, f"title{k + 1:02d}_{graphic['template']}.mov")})
    lut = look_lut(edl, folder / "look.cube", ffmpeg)
    notes = markers(edl, timeline)
    if lut:
        notes.insert(0, (0.0, f"Studio: лук «{edl.look or 'grade'}» — look.cube на корректирующий слой"))
    plan = {"title": title, "fps": fps, "width": width, "height": height, "total": timeline["total"],
            "segments": layout(edl, timeline), "sources": sources, "stills": stills, "clips": timeline["clips"],
            "music": music, "music_spec": edl.music, "sfx": sfx, "graphics": graphics, "markers": notes,
            "has_audio": {i: bool(media[item["source"]].get("audio")) for i, item in enumerate(timeline["clips"])}}
    (folder / "STUDIO.xml").write_text(xmeml(plan), encoding="utf-8")
    (folder / "STUDIO.fcpxml").write_text(fcpxml(plan), encoding="utf-8")
    (folder / "README.txt").write_text(
        "Premiere Pro: Файл → Импорт → STUDIO.xml.\n"
        "DaVinci Resolve: File → Import → Timeline… → STUDIO.fcpxml (или STUDIO.xml).\n"
        "Final Cut Pro: File → Import → XML… → STUDIO.fcpxml.\n"
        "media/ — исходники; look.cube — лук фильма (Lumetri → Creative → Look, или узел LUT в Resolve).\n"
        "Маркеры на таймлайне — то, что Studio делает, а монтажка сама не повторит.\n", encoding="utf-8")
    return {"xml": str(folder / "STUDIO.xml"), "fcpxml": str(folder / "STUDIO.fcpxml"),
            "lut": str(lut) if lut else None, "media": len(list(files.iterdir())), "markers": len(notes)}


# ---------- Premiere Pro: FCP7 XML (xmeml v4) ----------

def _rate(parent, fps):
    rate = ET.SubElement(parent, "rate")
    ET.SubElement(rate, "timebase").text = str(fps)
    ET.SubElement(rate, "ntsc").text = "FALSE"


def _text(parent, tag, value):
    ET.SubElement(parent, tag).text = str(value)
    return parent


def xmeml(plan: dict) -> str:
    fps = plan["fps"]
    f = lambda seconds: round(seconds * fps)  # noqa: E731
    root = ET.Element("xmeml", version="4")
    seq = ET.SubElement(root, "sequence", id="sequence-1")
    _text(seq, "name", plan["title"])
    _text(seq, "duration", f(plan["total"]))
    _rate(seq, fps)
    media = ET.SubElement(seq, "media")
    video = ET.SubElement(media, "video")
    fmt = ET.SubElement(ET.SubElement(video, "format"), "samplecharacteristics")
    _rate(fmt, fps)
    _text(fmt, "width", plan["width"])
    _text(fmt, "height", plan["height"])
    _text(fmt, "pixelaspectratio", "square")
    declared: set[str] = set()

    def file_ref(parent, key: str, path: Path, seconds: float | None, audio: bool, video_: bool = True):
        node = ET.SubElement(parent, "file", id=key)
        if key in declared:
            return
        declared.add(key)
        _text(node, "name", path.name)
        _text(node, "pathurl", url(path))
        _rate(node, fps)
        if seconds is not None:
            _text(node, "duration", f(seconds))
        m = ET.SubElement(node, "media")
        if video_:
            ET.SubElement(m, "video")
        if audio:
            a = ET.SubElement(m, "audio")
            _text(ET.SubElement(a, "samplecharacteristics"), "samplerate", 48000)
            _text(a, "channelcount", 2)

    def clipitem(track, key, name, start, end, src_in, path, seconds, audio=False, video_=True, mediatype=None):
        item = ET.SubElement(track, "clipitem", id=key)
        _text(item, "name", name)
        _text(item, "enabled", "TRUE")
        _rate(item, fps)
        _text(item, "start", f(start))
        _text(item, "end", f(end))
        _text(item, "in", f(src_in))
        _text(item, "out", f(src_in) + f(end) - f(start))
        file_ref(item, f"file-{path.name}", path, seconds, audio, video_)
        if mediatype:
            source = ET.SubElement(item, "sourcetrack")
            _text(source, "mediatype", mediatype)
            _text(source, "trackindex", 1)
        return item

    v1 = ET.SubElement(video, "track")
    for k, seg in enumerate(plan["segments"]):
        i = seg["clip"]
        if seg["still"]:
            clipitem(v1, f"v1-{k}", f"Шот {i + 1} · стоп-кадр", seg["start"], seg["end"], 0, plan["stills"][i], None)
        else:
            clipitem(v1, f"v1-{k}", f"Шот {i + 1}", seg["start"], seg["end"], seg["source_in"], plan["sources"][i],
                     plan["clips"][i]["out"] + 10, audio=plan["has_audio"][i])
    for i, clip in enumerate(plan["clips"][1:], 1):
        d = clip["transition"]["duration"]
        if d <= 0:
            continue
        seam = clip["start"] + d / 2
        tr = ET.SubElement(v1, "transitionitem")
        _text(tr, "start", f(seam - d / 2))
        _text(tr, "end", f(seam + d / 2))
        _text(tr, "alignment", "center")
        _rate(tr, fps)
        dip = {"fadeblack": 0, "fadewhite": 255}.get(clip["transition"]["effect"])
        name, effect_id = ("Dip to Color Dissolve", "dipcolor") if dip is not None else (DISSOLVE, DISSOLVE)
        effect = ET.SubElement(tr, "effect")
        for tag, value in (("name", name), ("effectid", effect_id), ("effectcategory", "Dissolve"),
                           ("effecttype", "transition"), ("mediatype", "video")):
            _text(effect, tag, value)
        if dip is not None:  # FCP7's dip carries its colour as a parameter
            param = ET.SubElement(effect, "parameter")
            _text(param, "parameterid", "dipcolor")
            _text(param, "name", "Color")
            value = ET.SubElement(param, "value")
            for channel in ("alpha", "red", "green", "blue"):
                _text(value, channel, 255 if channel == "alpha" else dip)
    for lane, graphic in zip(lanes(plan["graphics"]), plan["graphics"]):
        track = video.findall("track")[lane + 1] if len(video.findall("track")) > lane + 1 else ET.SubElement(video, "track")
        clipitem(track, f"title-{graphic['file'].stem}", graphic["template"], graphic["start"],
                 graphic["start"] + graphic["seconds"], 0, graphic["file"], graphic["seconds"])
    audio = ET.SubElement(media, "audio")
    shot_tracks = [ET.SubElement(audio, "track"), ET.SubElement(audio, "track")]
    for i, clip in enumerate(plan["clips"]):
        if not clip["audio"]:
            continue
        lead = clip["audio_lead"]
        clipitem(shot_tracks[i % 2], f"a-{i}", f"Звук шота {i + 1}", clip["start"] - lead, clip["end"],
                 clip["in"] - lead, plan["sources"][i], clip["out"] + 10, audio=True, mediatype="audio")
    if plan["music"]:
        spec = plan["music_spec"]
        track = ET.SubElement(audio, "track")
        clipitem(track, "music", "Музыка", spec.start, plan["total"], spec.source_in, plan["music"], None,
                 audio=True, video_=False, mediatype="audio")
    sfx_tracks: list = []
    for lane, sound in zip(lanes(plan["sfx"]), plan["sfx"]):
        while len(sfx_tracks) <= lane:
            sfx_tracks.append(ET.SubElement(audio, "track"))
        clipitem(sfx_tracks[lane], f"sfx-{sound['file'].stem}", sound["bank"] or "SFX", sound["start"],
                 sound["start"] + sound["seconds"], sound["source_in"], sound["file"], None, audio=True, video_=False,
                 mediatype="audio")
    for at, comment in plan["markers"]:
        marker = ET.SubElement(seq, "marker")
        _text(marker, "name", comment[:60])
        _text(marker, "comment", comment)
        _text(marker, "in", f(at))
        _text(marker, "out", -1)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---------- DaVinci Resolve / Final Cut Pro: FCPXML 1.10 ----------

def fcpxml(plan: dict) -> str:
    fps = plan["fps"]

    def t(seconds: float) -> str:
        return f"{round(seconds * fps)}/{fps}s"

    root = ET.Element("fcpxml", version="1.10")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(resources, "format", id="r0", name=f"STUDIO {plan['width']}x{plan['height']} {fps}p",
                  frameDuration=f"1/{fps}s", width=str(plan["width"]), height=str(plan["height"]))
    assets: dict[str, str] = {}

    def asset(path: Path, video: bool, audio: bool, seconds: float | None) -> str:
        key = str(path)
        if key not in assets:
            assets[key] = f"a{len(assets) + 1}"
            node = ET.SubElement(resources, "asset", id=assets[key], name=path.name, start="0s",
                                 duration=t(seconds) if seconds else "0s", hasVideo="1" if video else "0",
                                 hasAudio="1" if audio else "0", format="r0")
            if audio:
                node.set("audioSources", "1")
                node.set("audioChannels", "2")
                node.set("audioRate", "48000")
            ET.SubElement(node, "media-rep", kind="original-media", src=url(path))
        return assets[key]

    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="STUDIO")
    project = ET.SubElement(event, "project", name=plan["title"])
    sequence = ET.SubElement(project, "sequence", format="r0", duration=t(plan["total"]), tcStart="0s",
                             tcFormat="NDF", audioLayout="stereo", audioRate="48k")
    spine = ET.SubElement(sequence, "spine")
    placed = []  # (record start, record end, element, source start)
    seams = {round(c["start"] + c["transition"]["duration"] / 2, 4): c for c in plan["clips"][1:]
             if c["transition"]["duration"] > 0}
    for seg in plan["segments"]:
        i = seg["clip"]
        boundary = round(seg["start"], 4)
        if boundary in seams and not seg["still"]:
            clip = seams.pop(boundary)
            d = clip["transition"]["duration"]
            ET.SubElement(spine, "transition", name=NLE_TRANSITION.get(clip["transition"]["effect"], DISSOLVE),
                          offset=t(boundary - d / 2), duration=t(d))
        if seg["still"]:
            ref = asset(plan["stills"][i], True, False, None)
            el = ET.SubElement(spine, "video", ref=ref, name=f"Шот {i + 1} · стоп-кадр", offset=t(seg["start"]),
                               duration=t(seg["end"] - seg["start"]), start="0s")
            placed.append((seg["start"], seg["end"], el, 0.0))
        else:
            ref = asset(plan["sources"][i], True, plan["has_audio"][i], plan["clips"][i]["out"] + 10)
            el = ET.SubElement(spine, "asset-clip", ref=ref, name=f"Шот {i + 1}", offset=t(seg["start"]),
                               duration=t(seg["end"] - seg["start"]), start=t(seg["source_in"]), format="r0",
                               tcFormat="NDF")
            placed.append((seg["start"], seg["end"], el, seg["source_in"]))

    def connect(start: float, seconds: float, path: Path, name: str, lane: int, source_in: float, video: bool):
        parent = next((p for p in placed if p[0] <= start + 1e-6 < p[1]), placed[-1])
        offset = parent[3] + (start - parent[0])  # in the parent's own (source) time
        ref = asset(path, video, not video, None if not video else seconds)
        ET.SubElement(parent[2], "asset-clip", ref=ref, name=name, lane=str(lane), offset=t(offset),
                      duration=t(seconds), start=t(source_in))

    for lane, graphic in zip(lanes(plan["graphics"]), plan["graphics"]):
        connect(graphic["start"], graphic["seconds"], graphic["file"], graphic["template"], lane + 1, 0, True)
    if plan["music"]:
        spec = plan["music_spec"]
        connect(spec.start, plan["total"] - spec.start, plan["music"], "Музыка", -1, spec.source_in, False)
    for lane, sound in zip(lanes(plan["sfx"]), plan["sfx"]):
        connect(sound["start"], sound["seconds"], sound["file"], sound["bank"] or "SFX", -2 - lane, sound["source_in"], False)
    for at, comment in plan["markers"]:
        parent = next((p for p in placed if p[0] <= at + 1e-6 < p[1]), placed[-1])
        ET.SubElement(parent[2], "marker", start=t(parent[3] + at - parent[0]), duration=f"1/{fps}s", value=comment[:120])
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(root, encoding="unicode") + "\n"


RESOLVE_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
RESOLVE_MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
# Runs in the system python3 (Resolve's scripting module is a plain-Python wrapper around fusionscript.so).
RESOLVE_IMPORT = r"""
import json, sys
sys.path.append(sys.argv[1])
import DaVinciResolveScript as dvr
folder, name = sys.argv[2], sys.argv[3]
resolve = dvr.scriptapp("Resolve")
if not resolve:
    print(json.dumps({"error": "Resolve не отвечает: открой DaVinci Resolve и включи Preferences → System → General → "
                               "External scripting using: Local"})); sys.exit(0)
manager = resolve.GetProjectManager()
project = manager.GetCurrentProject()
if not project or project.GetName() == "Untitled Project" and not project.GetTimelineCount():
    project = manager.CreateProject("STUDIO") or manager.LoadProject("STUDIO") or project
timeline = project.GetMediaPool().ImportTimelineFromFile(folder + "/STUDIO.fcpxml", {
    "timelineName": name, "importSourceClips": True, "sourceClipsPath": folder + "/media"})
if not timeline:
    print(json.dumps({"error": "Resolve не импортировал таймлайн (см. Import Log в DaVinci Resolve)"})); sys.exit(0)
project.SetCurrentTimeline(timeline)
resolve.OpenPage("edit")
print(json.dumps({"project": project.GetName(), "timeline": timeline.GetName(),
                  "frames": timeline.GetEndFrame() - timeline.GetStartFrame()}))
"""


def import_to_resolve(folder: Path, name: str) -> dict:
    """Open the exported FCPXML as a new timeline in the running DaVinci Resolve (current project)."""
    import json
    if not Path(RESOLVE_LIB).is_file():
        raise ValueError("DaVinci Resolve не установлен")
    result = subprocess.run(["/usr/bin/python3", "-c", RESOLVE_IMPORT, RESOLVE_MODULES, str(folder), name],
                            env={"RESOLVE_SCRIPT_LIB": RESOLVE_LIB, "PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
                            capture_output=True, text=True, timeout=180, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise ValueError(f"Resolve: {(result.stderr or 'нет ответа').strip()[-300:]}")
    data = json.loads(lines[-1])
    if "error" in data:
        raise ValueError(data["error"])
    return data
