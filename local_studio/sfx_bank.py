"""Built-in sound effects for the edit: synthesised by FFmpeg, so they need no library, licence or download.

Each effect is a short WAV (48 kHz stereo, peak −1 dBFS) rendered once and cached under
<Studio data>/sfx_bank/v<VERSION>/. `anchor` is the moment inside the sound that must land on the cut:
a whoosh peaks in the middle, an impact hits at its start, a riser ends on the cut. The edit sheet
places a bank sound by that anchor (edl.py `auto_sfx`), the agent can also place them by name.
A real SFX library (Studio assets with the role «Звуковой эффект») always works alongside the bank.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

VERSION = 1
RATE = 48000
PEAK_DB = -1.0

# name → (seconds, anchor seconds, source, filter chain, Russian description)
BANK: dict[str, tuple[float, float, str, str, str]] = {
    "whoosh": (0.9, 0.55,
               "anoisesrc=c=pink:r=48000:d=0.9:a=0.9",
               "highpass=f=250,lowpass=f=5000,"
               "volume='pow(sin(PI*min(t/0.9,1)),3)*(1+1.5*exp(-pow((t-0.55)/0.12,2)))':eval=frame,"
               "aphaser=in_gain=0.6:out_gain=0.9:delay=2:decay=0.5:speed=1.2,apulsator=mode=sine:hz=0.9:amount=0.9",
               "Свист пролёта камеры: whip pan, быстрый проход"),
    "whoosh_long": (1.6, 1.1,
                    "anoisesrc=c=pink:r=48000:d=1.6:a=0.9",
                    "highpass=f=180,lowpass=f=4200,"
                    "volume='pow(sin(PI*min(t/1.6,1)),2)*(1+2*exp(-pow((t-1.1)/0.2,2)))':eval=frame,"
                    "aphaser=speed=0.7,apulsator=mode=sine:hz=0.5:amount=1",
                    "Длинный пролёт: переход сквозь объект, вход в фантазию"),
    "swish": (0.35, 0.18,
              "anoisesrc=c=white:r=48000:d=0.35:a=0.8",
              "highpass=f=1800,lowpass=f=9000,volume='pow(sin(PI*min(t/0.35,1)),4)':eval=frame,apulsator=hz=3",
              "Короткий взмах: склейка на действии, рывок, жест"),
    "impact": (1.6, 0.0,
               "aevalsrc='0.95*sin(2*PI*(38*t+90*(1-exp(-9*t))/9))*exp(-3.2*t)+0.5*(random(0)*2-1)*exp(-38*t)':s=48000:d=1.6",
               "lowpass=f=6000,aecho=0.8:0.5:60|110:0.25|0.15",
               "Удар: smash cut, приземление, столкновение"),
    "boom": (3.0, 0.0,
             "aevalsrc='0.95*sin(2*PI*(30*t+70*(1-exp(-5*t))/5))*exp(-1.4*t)+0.45*(random(0)*2-1)*exp(-14*t)':s=48000:d=3",
             "lowpass=f=3500,aecho=0.8:0.6:90|180|260:0.35|0.25|0.15",
             "Большой кинематографичный удар с хвостом: титул, кульминация"),
    "hit": (0.45, 0.0,
            "aevalsrc='0.9*sin(2*PI*(70*t+160*(1-exp(-25*t))/25))*exp(-11*t)+0.6*(random(0)*2-1)*exp(-60*t)':s=48000:d=0.45",
            "highpass=f=40,lowpass=f=8000",
            "Короткий акцент: удар в драке, стоп-кадр, ритм"),
    "riser": (2.4, 2.4,
              "aevalsrc='(0.45*sin(2*PI*(180*t+(2400-180)/(2*2.4)*t*t))+0.35*(random(0)*2-1)*pow(t/2.4,2))*pow(t/2.4,1.6)':s=48000:d=2.4",
              "highpass=f=120,aphaser=speed=2",
              "Нарастание напряжения, обрывается ровно на склейке"),
    "riser_short": (1.2, 1.2,
                    "aevalsrc='(0.45*sin(2*PI*(300*t+(2600-300)/(2*1.2)*t*t))+0.4*(random(0)*2-1)*pow(t/1.2,2))*pow(t/1.2,1.4)':s=48000:d=1.2",
                    "highpass=f=200,aphaser=speed=2",
                    "Короткое нарастание перед ударом"),
    "subdrop": (1.4, 0.0,
                "aevalsrc='0.95*sin(2*PI*(95*t-30*t*t))*exp(-1.6*t)':s=48000:d=1.4",
                "highpass=f=20",
                "Саб-дроп: падение в тишину, раскрытие, новая сцена"),
    "reverse_swell": (1.5, 1.5,
                      "anoisesrc=c=pink:r=48000:d=1.5:a=0.9",
                      "highpass=f=400,lowpass=f=7000,volume='exp(-3.5*t)':eval=frame,aecho=0.8:0.7:80|160:0.4|0.3,areverse",
                      "Обратный «вдох»: втягивает в склейку, вход во флешбэк"),
    "glitch": (0.5, 0.05,
               "aevalsrc='(random(0)*2-1)*gt(sin(2*PI*23*t),0.2)*exp(-4*t)+0.4*sin(2*PI*1200*t)*gt(sin(2*PI*9*t),0.6)':s=48000:d=0.5",
               "acrusher=bits=6:samples=8:mix=0.8,highpass=f=300",
               "Цифровой сбой: глитч-переход, хакерский экран"),
    "shutter": (0.3, 0.0,
                "aevalsrc='(random(0)*2-1)*(exp(-90*t)+0.7*exp(-90*max(t-0.07,0))*gte(t,0.07))':s=48000:d=0.3",
                "highpass=f=1500,lowpass=f=9000",
                "Щелчок затвора: стоп-кадр с титром героя"),
    "pop": (0.18, 0.0,
            "aevalsrc='0.9*sin(2*PI*(900*t-1800*t*t))*exp(-30*t)':s=48000:d=0.18",
            "highpass=f=200",
            "Поп: появление титра, иконки, стикера"),
}


def describe() -> list[dict]:
    return [{"name": name, "seconds": spec[0], "anchor": spec[1], "description": spec[4]} for name, spec in BANK.items()]


def anchor(name: str) -> float:
    return BANK[name][1]


def seconds(name: str) -> float:
    return BANK[name][0]


def render(name: str, root: Path, ffmpeg: str) -> Path:
    """The cached WAV of a bank effect, rendered and peak-normalised on first use."""
    if name not in BANK:
        raise ValueError(f"Нет звука «{name}» в библиотеке. Есть: {', '.join(BANK)}")
    folder = root / "sfx_bank" / f"v{VERSION}"
    target = folder / f"{name}.wav"
    if target.is_file():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    length, _, source, chain, _ = BANK[name]
    raw = folder / f".{name}.raw.wav"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source,
                    "-af", f"{chain},aformat=channel_layouts=stereo,atrim=duration={length}",
                    "-ar", str(RATE), str(raw)], check=True, timeout=60)
    probe = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(raw), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60, check=True)
    match = re.search(r"max_volume:\s*(-?[\d.]+) dB", probe.stderr)
    gain = PEAK_DB - float(match.group(1)) if match else 0.0
    temp = folder / f".{name}.tmp.wav"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw), "-af", f"volume={gain:.2f}dB",
                    "-ar", str(RATE), str(temp)], check=True, timeout=60)
    temp.replace(target)
    raw.unlink(missing_ok=True)
    return target
