"""Deterministic FFmpeg assembly for an audio project; no model inference here."""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess


def _run(command: list[str], timeout: int = 600) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"FFmpeg завершился с ошибкой: {result.stderr[-900:]}")


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError(f"Медиафайл не читается: {path.name}")
    return json.loads(result.stdout)


def has_audio(path: Path) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in probe(path).get("streams", []))


def extract_original_audio(video: Path, destination: Path) -> Path | None:
    """Keep the original soundtrack as its own file before changing any video."""
    if not has_audio(video):
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
          "-map", "0:a:0", "-vn", "-c:a", "pcm_s16le", str(destination)])
    if not destination.is_file() or not has_audio(destination):
        raise ValueError("Не удалось сохранить исходную аудиодорожку")
    return destination


def _level(value: object, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Неверный уровень {name}") from exc
    if not math.isfinite(number) or not 0 <= number <= 2:
        raise ValueError(f"Уровень {name} должен быть от 0 до 2")
    return number


def mix_project(*, destination: Path, duration: float, output_format: str,
                video: Path | None = None, original_audio: Path | None = None,
                speech: list[tuple[Path, float]] | None = None,
                music: Path | None = None, effects: Path | None = None,
                source_level: float = 1, speech_level: float = 1,
                music_level: float = .55, effects_level: float = .7,
                ducking: bool = True) -> Path:
    """Mix separate tracks and optionally remux the untouched video stream.

    Inputs are trusted, already validated local paths. Output is always a new file.
    Speech starts are in seconds. Music/effects loop to cover the project length.
    """
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise ValueError("Длительность должна быть от 0 до 3600 секунд")
    if video and output_format != "mp4":
        raise ValueError("С видео нужен выходной MP4")
    if not video and output_format not in {"wav", "mp3"}:
        raise ValueError("Для отдельного звука выбери WAV или MP3")
    if video and (not video.is_file() or not any(s.get("codec_type") == "video" for s in probe(video).get("streams", []))):
        raise ValueError("Исходный ролик недоступен")
    speech = speech or []
    levels = {"source": _level(source_level, "оригинала"),
              "speech": _level(speech_level, "речи"),
              "music": _level(music_level, "музыки"),
              "effects": _level(effects_level, "эффектов")}
    for path, start in speech:
        if not path.is_file() or not has_audio(path):
            raise ValueError("Синтезированная реплика отсутствует")
        if not math.isfinite(start) or not 0 <= start < duration:
            raise ValueError("Реплика выходит за пределы проекта")
        spoken_duration = float(probe(path).get("format", {}).get("duration") or 0)
        if not 0 < spoken_duration or start + spoken_duration > duration + .05:
            raise ValueError("Синтезированная реплика не помещается в длительность проекта")
    for path in (original_audio, music, effects):
        if path and (not path.is_file() or not has_audio(path)):
            raise ValueError(f"Аудиодорожка недоступна: {path.name}")
    if not any((original_audio, speech, music, effects)):
        raise ValueError("Добавь хотя бы одну звуковую дорожку")

    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if video:
        command += ["-i", str(video)]
    input_index = 1 if video else 0
    tracks: list[tuple[str, int, Path, float]] = []
    if original_audio and levels["source"] > 0:
        tracks.append(("source", input_index, original_audio, 0))
        command += ["-i", str(original_audio)]
        input_index += 1
    for path, start in speech:
        if levels["speech"] > 0:
            tracks.append(("speech", input_index, path, start))
            command += ["-i", str(path)]
            input_index += 1
    for kind, path in (("music", music), ("effects", effects)):
        if path and levels[kind] > 0:
            tracks.append((kind, input_index, path, 0))
            if kind == "music":
                command += ["-stream_loop", "-1"]
            command += ["-i", str(path)]
            input_index += 1
    if not tracks:
        raise ValueError("У всех звуковых дорожек нулевой уровень")

    filters: list[str] = []
    groups: dict[str, list[str]] = {kind: [] for kind in levels}
    for number, (kind, index, _path, start) in enumerate(tracks):
        label = f"t{number}"
        delay = f",adelay={round(start * 1000)}:all=1" if start else ""
        filters.append(f"[{index}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"volume={levels[kind]}{delay},atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[{label}]")
        groups[kind].append(f"[{label}]")

    def combine(inputs: list[str], name: str) -> str | None:
        if not inputs:
            return None
        if len(inputs) == 1:
            return inputs[0]
        filters.append(f"{''.join(inputs)}amix=inputs={len(inputs)}:duration=longest:normalize=0[{name}]")
        return f"[{name}]"

    voice = combine(groups["speech"], "voices")
    music_bus = combine(groups["music"], "musicbus")
    if ducking and voice and music_bus:
        filters.append(f"{music_bus}{voice}sidechaincompress=threshold=0.025:ratio=8:attack=40:release=350[ducked]")
        music_bus = "[ducked]"
    buses = [*groups["source"], *groups["effects"]]
    if voice:
        buses.append(voice)
    if music_bus:
        buses.append(music_bus)
    combined = combine(buses, "premix")
    filters.append(f"{combined}alimiter=limit=0.95,apad,atrim=duration={duration:.3f}[final]")
    command += ["-filter_complex", ";".join(filters)]
    if video:
        command += ["-map", "0:v:0", "-map", "[final]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k"]
    else:
        command += ["-map", "[final]", "-c:a", "pcm_s16le" if output_format == "wav" else "libmp3lame"]
    command += ["-t", f"{duration:.3f}", str(destination)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run(command)
    info = probe(destination)
    if not any(s.get("codec_type") == "audio" for s in info.get("streams", [])):
        raise ValueError("В результате нет звуковой дорожки")
    if video and not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise ValueError("В результате нет видеопотока")
    return destination
