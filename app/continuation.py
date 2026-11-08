"""«Продолжение» (continuous take): the new clip picks up exactly where a previous clip ended.

Official usage (Comfy-Org workflow template video_minimax_h3_multiframe_reference, MiniMaxH3AddGuide
at ComfyUI b5cc8830): feed 22 frames of an existing video plus its audio into one Add Guide at
frame_idx 0 to continue both streams. The node crops a clip guide to the model's 17k+5 grid, so 22
is the shortest multi-frame clip (5 frames would be a still-like anchor).

The continuation video is NOT a Ref2VA reference (no <Video N> label). Graph:

    LoadVideo → GetVideoComponents → ImageFromBatch(batch_index=-22, length=22)   (last 22 frames)
                                   → TrimAudioDuration(start=(frames-22)/24, 22/24 s)   (same span)
    → MiniMaxH3AddGuide(frame_idx=0, image, audio, vae, audio_vae, latent of Ref2VA)

ImageFromBatch at b5cc8830 adds the batch size to a negative batch_index, so -22 is the last 22
frames. The audio start is computed from the probed frame count of the 24 fps tail we upload,
because the audio track may end a little before or after the last frame.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from .contract import GenerationRequest

ROLE = "Продолжение"
DIRECTOR_ROLE = "continuation"
CLIP_FRAMES = 22  # 17*1 + 5: the shortest clip guide MiniMaxH3AddGuide keeps as a clip
FPS = 24
TAIL_SECONDS = 3.0  # only the end of the previous clip is decoded and uploaded
NODES = frozenset({"LoadVideo", "GetVideoComponents", "ImageFromBatch", "TrimAudioDuration", "MiniMaxH3AddGuide"})

# Legacy (non six-section) prompts get one sentence in the wording of the official guide
# (task type "video continuation"; <Video N> "continuation starting point"). It names no label:
# the clip is anchored as a guide, not as a Ref2VA <Video N>.
LEGACY_SENTENCE = ("The shot is a direct video continuation of the previous clip: its opening frames and sound "
                   "pick up exactly where that clip ended, with the same subjects, positions, lighting and camera, "
                   "and the action carries on from there.")


def split_continuation(manifest: dict, video_items: list) -> tuple[dict, list, tuple | None]:
    """(manifest without the continuation video, Ref2VA video uploads, continuation upload or None).

    Raises ValueError for combinations H3 cannot render: more than one continuation video, or a
    continuation video that is not the last video (the Ref2VA <Video N> numbers would shift).
    """
    videos = list(manifest.get("videos") or [])
    marked = [index for index, entry in enumerate(videos) if entry.get("role") == ROLE]
    if not marked:
        return manifest, list(video_items), None
    if len(marked) > 1:
        raise ValueError("«Продолжение» может быть только у одного видео задания")
    index = marked[0]
    if index != len(videos) - 1:
        raise ValueError("Видео «Продолжение» должно идти последним среди видео задания")
    rest = {**manifest, "videos": videos[:index]}
    return rest, list(video_items[:index]), video_items[index]


def _run(command: list[str], filename: str, timeout: int = 180) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Не удалось подготовить видео «Продолжение» {filename}: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(f"Не удалось подготовить видео «Продолжение» {filename}: {result.stderr[-300:]}")
    return result


def _probe(path: Path, filename: str) -> dict:
    result = _run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                   "stream=codec_type,nb_read_frames,duration", "-of", "json", str(path)], filename, timeout=60)
    streams = json.loads(result.stdout or "{}").get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise ValueError(f"В видео «Продолжение» {filename} нет видеопотока")
    return {"frames": int(video.get("nb_read_frames") or 0), "audio": audio is not None,
            "audio_seconds": float(audio.get("duration") or 0) if audio else 0.0}


def prepare_continuation_video(item: tuple[str, bytes, str], request: GenerationRequest) -> dict:
    """The last TAIL_SECONDS of the clip at 24 fps, covering the target canvas, audio padded to the video.

    Returns {"upload": (name, bytes, mime), "frames": n, "audio": bool}. ValueError (422) when the clip
    has fewer than 22 frames. The user's original upload is not changed.
    """
    filename, data, _mime = item
    with tempfile.TemporaryDirectory(prefix="h3-continuation-") as directory:
        source, tail, final = (Path(directory) / name for name in ("source", "tail.mp4", "final.mp4"))
        source.write_bytes(data)
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-sseof", f"-{TAIL_SECONDS}",
              "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
              # Cover the generation canvas; MiniMaxH3AddGuide centre-crops to it, never upscaled twice.
              "-vf", f"fps={FPS},scale={request.width}:{request.height}:force_original_aspect_ratio=increase"
                     ":flags=lanczos",
              "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tail)], filename)
        info = _probe(tail, filename)
        if info["frames"] < CLIP_FRAMES:
            raise ValueError(f"Видео «Продолжение» {filename}: {info['frames']} кадров при 24 к/с, нужно не меньше "
                             f"{CLIP_FRAMES} (≈0,92 с) — H3 продолжает дубль по последним {CLIP_FRAMES} кадрам")
        output = tail
        if info["audio"]:
            # One time base for both streams: the audio is padded/trimmed to the last video frame.
            seconds = info["frames"] / FPS
            _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(tail),
                  "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-af", f"apad,atrim=0:{seconds:.6f}",
                  "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(final)], filename)
            output = final
        digest = hashlib.sha256(data).hexdigest()[:16]
        return {"upload": (f"continuation-{digest}-24fps.mp4", output.read_bytes(), "video/mp4"),
                "frames": info["frames"], "audio": info["audio"]}


CONDITIONING_NODE = "354"  # the AddGuide that carries the previous clip; scene guides chain after it


def continuation_nodes(file: str, frames: int, has_audio: bool, positive: list, latent: list) -> dict:
    """Graph nodes 350–354; the guide (354) chains like scene guides (positive in → positive out)."""
    def node(class_type: str, title: str, **inputs: object) -> dict:
        return {"inputs": inputs, "class_type": class_type, "_meta": {"title": title}}

    if frames < CLIP_FRAMES:
        raise ValueError(f"Видео «Продолжение»: нужно не меньше {CLIP_FRAMES} кадров")
    nodes = {
        "350": node("LoadVideo", "@continuation", file=file),
        "351": node("GetVideoComponents", "@continuation components", video=["350", 0]),
        "352": node("ImageFromBatch", f"Последние {CLIP_FRAMES} кадров", image=["351", 0],
                    batch_index=-CLIP_FRAMES, length=CLIP_FRAMES),
    }
    guide = {"positive": positive, "latent": latent, "vae": ["119", 0], "image": ["352", 0], "frame_idx": 0}
    if has_audio:
        nodes["353"] = node("TrimAudioDuration", f"Звук последних {CLIP_FRAMES} кадров", audio=["351", 1],
                            start_index=round((frames - CLIP_FRAMES) / FPS, 4),
                            duration=round(CLIP_FRAMES / FPS, 4))
        guide.update(audio=["353", 0], audio_vae=["120", 0])
    nodes[CONDITIONING_NODE] = node("MiniMaxH3AddGuide", "Продолжение дубля (кадр 0)", **guide)
    return nodes
