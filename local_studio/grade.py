"""Colour for the edit sheet: looks, primary controls, shot matching and colour QC (FFmpeg filters).

Order on the film (edl.py): every clip gets its own correction first — shot matching to a reference clip,
then the clip's own primaries — and the whole film then gets one look, mixed in at `look_intensity`.
Limits after Ponder's grading skill: look intensity 0.4–0.7 reads as a grade, not a filter; contrast
1.05–1.2, saturation 0.95–1.1. QC flags highlights clipped over 1 % with p99 > 0.97, more than 20 % pure
black (H3 night scenes keep some on purpose) and shots darker than 0.12 mean luma.
"""
from __future__ import annotations

import statistics
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

# name → (Russian description, full-strength FFmpeg chain).
LOOKS: dict[str, tuple[str, str]] = {
    "teal_orange": ("Teal & orange: холодные тени, тёплая кожа",
                    "colorbalance=rs=-0.1:gs=0.02:bs=0.13:rh=0.09:gh=0.03:bh=-0.09,vibrance=intensity=0.12,eq=contrast=1.07"),
    "warm_film": ("Тёплая плёнка: приподнятые чёрные, мягкие света",
                  "colortemperature=temperature=5300,curves=all='0/0.04 0.5/0.52 1/0.96',eq=saturation=0.95"),
    "cool_matte": ("Холодный мат: синеватый, приглушённый, без глубоких чёрных",
                   "colortemperature=temperature=7600,curves=all='0/0.06 1/0.94',eq=saturation=0.85"),
    "high_contrast": ("Высокий контраст: плотные тени, яркие света", "curves=preset=medium_contrast,eq=saturation=1.06"),
    "bleach_bypass": ("Bleach bypass: серебристый, обесцвеченный, жёсткий",
                      "eq=saturation=0.45:contrast=1.22,curves=all='0/0 0.25/0.2 0.75/0.82 1/1'"),
    "golden_hour": ("Золотой час: тёплый низкий свет", "colortemperature=temperature=4400,colorbalance=rh=0.05:bh=-0.04,"
                    "vibrance=intensity=0.1"),
    "noir": ("Нуар: чёрно-белое с плотным контрастом", "monochrome,curves=preset=strong_contrast"),
    "clean_neutral": ("Чистый нейтральный: лёгкая полировка контраста и цвета", "eq=contrast=1.03:saturation=1.03"),
    "pastel": ("Пастель: мягкие светлые цвета", "eq=saturation=0.65:contrast=0.9:brightness=0.04"),
    "vivid": ("Яркие цвета: насыщенно и контрастно", "eq=saturation=1.4:contrast=1.1"),
    "vintage": ("Старая плёнка: сепия, выцветший цвет", "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,"
                "eq=saturation=0.75:contrast=1.08"),
}
# Creative LUTs (Striped Purple, licence in luts/LICENSE): name → .cube, drawn with lut3d like any other look.
LUTS = {"amber_haze": "Amber Haze", "arcade_dayz": "Arcade Dayz", "blow_me_down": "Blow Me Down",
        "caramel_plum_cake": "Caramel Plum Cake", "cherry_champagne": "Cherry Champagne", "philly_fog": "Philly Fog",
        "snowy_blues": "Snowy Blues", "spring_water": "Spring Water", "teal_supreme": "Teal Supreme", "lobster": "Lobster"}
LUT_DIR = Path(__file__).resolve().parent / "luts"
for _name, _title in LUTS.items():
    _file = str(LUT_DIR / f"{_name.replace('_', '-')}.cube").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    LOOKS[f"lut_{_name}"] = (f"LUT {_title}", f"lut3d=file='{_file}':interp=trilinear")


class Grade(BaseModel):
    """Primary controls; the defaults change nothing."""
    exposure: float = Field(default=0, ge=-2, le=2)          # stops
    temperature: int = Field(default=6500, ge=3000, le=10000)  # K of the light to neutralise; < 6500 warms
    tint: float = Field(default=0, ge=-1, le=1)              # + magenta, − green
    contrast: float = Field(default=1, ge=0.6, le=1.6)
    saturation: float = Field(default=1, ge=0, le=2)
    vibrance: float = Field(default=0, ge=-1, le=1)


def primaries(grade: Grade | None) -> list[str]:
    if not grade:
        return []
    out = []
    if grade.exposure:
        out.append(f"exposure=exposure={grade.exposure:.3f}")
    if grade.temperature != 6500:
        out.append(f"colortemperature=temperature={grade.temperature}")
    if grade.tint:
        t = grade.tint * 0.15
        out.append(f"colorbalance=rm={t:.3f}:gm={-t:.3f}:bm={t:.3f}")
    if grade.contrast != 1 or grade.saturation != 1:
        out.append(f"eq=contrast={grade.contrast:.3f}:saturation={grade.saturation:.3f}")
    if grade.vibrance:
        out.append(f"vibrance=intensity={grade.vibrance:.3f}")
    return out


def look_graph(source: str, target: str, look: str, intensity: float, tag: str = "lk") -> list[str]:
    """The film through a look, mixed with the ungraded picture at `intensity` (0..1)."""
    chain = LOOKS[look][1]
    if intensity >= 0.999:
        return [f"[{source}]{chain}[{target}]"]
    return [f"[{source}]split[{tag}a][{tag}b]", f"[{tag}b]{chain}[{tag}g]",
            f"[{tag}a][{tag}g]blend=all_mode=normal:all_opacity={intensity:.3f}[{target}]"]


# Pure Python on purpose: the Studio service runs in its own venv without numpy (a few small frames are cheap).
def sample(path: Path, start: float, seconds: float, ffmpeg: str, frames: int = 6, gray: bool = False) -> list[list[float]]:
    """Pixel values 0..1 per channel ([luma] or [r, g, b]) of a few small frames spread over [start, start+seconds]."""
    width, height = 96, 96
    depth = 1 if gray else 3
    channels: list[list[float]] = [[] for _ in range(depth)]
    for k in range(frames):
        at = start + seconds * (k + 0.5) / frames
        out = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{at:.3f}", "-i", str(path),
                              "-frames:v", "1", "-vf", f"scale={width}:{height}", "-f", "rawvideo",
                              "-pix_fmt", "gray" if gray else "rgb24", "-"], capture_output=True, timeout=60, check=False)
        data = out.stdout[:width * height * depth]
        if len(data) < width * height * depth:
            continue
        for channel in range(depth):
            channels[channel].extend(value / 255 for value in data[channel::depth])
    if not channels[0]:
        raise ValueError(f"Не удалось прочитать кадры {path.name} для цвета")
    return channels


def match_filter(source: list[list[float]], reference: list[list[float]], strength: float) -> str:
    """Per-channel mean/std transfer (Reinhard in RGB) toward the reference, softened by `strength`."""
    exprs = []
    for s, r in zip(source, reference):
        mu_s, mu_r = statistics.fmean(s), statistics.fmean(r)
        gain = min(max(statistics.pstdev(r) / max(statistics.pstdev(s), 1e-3), 0.75), 1.33)
        # c' = c + k·((c − μs)·g + μr − c) in 0..255 values
        a = 1 + strength * (gain - 1)
        b = strength * (mu_r - mu_s * gain) * 255
        exprs.append(f"clip(val*{a:.4f}{b:+.2f},0,255)")
    return f"lutrgb=r='{exprs[0]}':g='{exprs[1]}':b='{exprs[2]}'"


def qc(path: Path, clips: list[dict], ffmpeg: str) -> list[dict]:
    """Exposure check of every clip of the finished film (luma 0..1)."""
    report = []
    for clip in clips:
        luma = sorted(sample(path, clip["start"], max(clip["end"] - clip["start"], 0.2), ffmpeg, frames=5, gray=True)[0])
        count = len(luma)
        p1, p99 = luma[int(count * 0.01)], luma[min(count - 1, int(count * 0.99))]
        mean = statistics.fmean(luma)
        white = sum(1 for v in luma if v >= 0.985) / count
        black = sum(1 for v in luma if v <= 0.008) / count
        notes = []
        # Animated night scenes keep pure blacks on purpose (dark clothes, night sky): only a large share is a crush.
        if p99 > 0.97 and white > 0.01:
            notes.append(f"пересвет: {white:.0%} кадра в чистом белом")
        if black > 0.2:
            notes.append(f"провал в чёрное: {black:.0%} кадра без деталей")
        if mean < 0.12:
            notes.append("очень тёмный шот")
        report.append({"clip": clip["index"], "mean": round(mean, 3), "p1": round(p1, 3), "p99": round(p99, 3),
                       "clipped": round(white + black, 4), "notes": notes})
    return report


def describe() -> list[dict]:
    return [{"look": name, "description": text} for name, (text, _) in LOOKS.items()]
