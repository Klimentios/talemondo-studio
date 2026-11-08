"""Isolated model process adapters for post-production audio tasks."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from .audio_mix import has_audio, probe


ROOT = Path(__file__).resolve().parent


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    # Keep a venv's python symlink: resolving it launches the base interpreter without
    # that environment's packages (uv-created CosyVoice/ACE-Step venvs use such links).
    return Path(value).expanduser().absolute() if value else None


def _smoke_ready(path: Path | None) -> bool:
    try:
        return bool(path and path.is_file() and has_audio(path))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return False


_IMPORT_CHECKS: dict[tuple[str, float, str], bool] = {}


def _python_imports(python: Path | None, module: str) -> bool:
    """True when this interpreter really imports `module` (a venv copied without its packages
    passed the file checks on 2026-10-07 and every music job failed with «No module named
    'torch'»). Cached per interpreter path and mtime, so capabilities() stays cheap."""
    if not python or not python.is_file():
        return False
    key = (str(python), python.stat().st_mtime, module)
    if key not in _IMPORT_CHECKS:
        try:
            result = subprocess.run([str(python), "-c", f"import {module}"], capture_output=True, timeout=120)
            _IMPORT_CHECKS[key] = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return _IMPORT_CHECKS[key]


def capabilities() -> dict:
    cosy_repo = _configured_path("H3_COSYVOICE_REPO")
    cosy_model = _configured_path("H3_COSYVOICE_MODEL")
    cosy_python = _configured_path("H3_COSYVOICE_PYTHON")
    default_voice = _configured_path("H3_COSYVOICE_DEFAULT_VOICE")
    smoke_wav = _configured_path("H3_COSYVOICE_SMOKE_WAV")
    # Exact sizes are from the pinned upstream HF revision
    # 29e01c4e8d000f4bcd70751be16fa94bf3d85a18. A directory by itself is
    # not proof that a resumable download completed.
    expected = {"llm.pt": 2024669519, "flow.pt": 1329116148,
                "hift.pt": 83202622, "speech_tokenizer_v3.onnx": 969451503,
                "speech_tokenizer_v3.batch.onnx": 969451579,
                "campplus.onnx": 28303423,
                "flow.decoder.estimator.fp32.onnx": 1326216933,
                "CosyVoice-BlankEN/model.safetensors": 988097824}
    weights_ready = bool(cosy_model and all((cosy_model / name).is_file()
                   and (cosy_model / name).stat().st_size == size for name, size in expected.items()))
    tts = bool(cosy_repo and cosy_repo.is_dir() and cosy_model and cosy_model.is_dir()
               and weights_ready and cosy_python and cosy_python.is_file()
               and default_voice and default_voice.is_file() and _smoke_ready(smoke_wav)
               and _python_imports(cosy_python, "torch"))
    # LatentSync selects a detected face; this Studio contract asks for a named
    # character. Keep the capability closed until a verified target selector exists.
    lip = False
    music_repo = _configured_path("H3_ACESTEP_REPO")
    music_python = _configured_path("H3_ACESTEP_PYTHON")
    music_checkpoints = _configured_path("H3_ACESTEP_CHECKPOINTS")
    music_smoke = _configured_path("H3_ACESTEP_SMOKE_WAV")
    music = bool(music_repo and (music_repo / "acestep/inference.py").is_file()
                 and music_python and music_python.is_file()
                 and music_checkpoints and (music_checkpoints / "acestep-v15-sft/model.safetensors").is_file()
                 and (music_checkpoints / "vae").is_dir()
                 and (music_checkpoints / "Qwen3-Embedding-0.6B").is_dir()
                 and (music_checkpoints / "acestep-5Hz-lm-1.7B").is_dir()
                 and _smoke_ready(music_smoke) and _python_imports(music_python, "torch"))
    return {"tts": tts, "lip_sync": lip, "music_generation": music,
            "music_controls": music,
            "effects_generation": False, "audio_mix": bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))}


def synthesize_line(*, text: str, direction: str, reference: Path | None, output: Path,
                    timeout: int = 300) -> Path:
    if not capabilities()["tts"]:
        raise RuntimeError("CosyVoice3 не установлена")
    voice = reference or _configured_path("H3_COSYVOICE_DEFAULT_VOICE")
    if not voice or not voice.is_file():
        raise ValueError("Образец голоса недоступен")
    command = [str(_configured_path("H3_COSYVOICE_PYTHON")), str(ROOT / "audio_cosyvoice_line.py"),
               "--repo", str(_configured_path("H3_COSYVOICE_REPO")),
               "--model", str(_configured_path("H3_COSYVOICE_MODEL")),
               "--reference", str(voice), "--text", text, "--direction", direction,
               "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"CosyVoice3 не создала реплику: {result.stderr[-600:]}")
    if not output.is_file() or not has_audio(output):
        raise RuntimeError("CosyVoice3 вернула пустую реплику")
    return output


def generate_music(*, prompt: str, duration: float, output: Path, lyrics: str = "",
                   bpm: int | None = None, keyscale: str = "", timesignature: str = "",
                   seed: int = -1, timeout: int = 1800) -> Path:
    if not capabilities()["music_generation"]:
        raise RuntimeError("ACE-Step 1.5 SFT не установлена или не прошла проверку")
    command = [str(_configured_path("H3_ACESTEP_PYTHON")), str(ROOT / "audio_acestep_music.py"),
               "--repo", str(_configured_path("H3_ACESTEP_REPO")),
               "--checkpoints", str(_configured_path("H3_ACESTEP_CHECKPOINTS")),
               "--prompt", prompt, "--lyrics", lyrics, "--duration", str(duration), "--output", str(output),
               "--bpm", str(bpm or 0), "--keyscale", keyscale, "--timesignature", timesignature,
               "--seed", str(seed)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"ACE-Step не создала музыку: {result.stderr[-600:]}")
    if not output.is_file() or not has_audio(output):
        raise RuntimeError("ACE-Step вернула пустую дорожку")
    return output


def synchronize_lips(*, video: Path, audio: Path, output: Path, timeout: int = 1800) -> Path:
    if not capabilities()["lip_sync"]:
        raise RuntimeError("LatentSync не установлена")
    repo = _configured_path("H3_LATENTSYNC_REPO")
    command = [str(_configured_path("H3_LATENTSYNC_PYTHON")), "-m", "scripts.inference",
               "--unet_config_path", "configs/unet/stage2_512.yaml",
               "--inference_ckpt_path", "checkpoints/latentsync_unet.pt",
               "--inference_steps", "20", "--guidance_scale", "1.5", "--enable_deepcache",
               "--video_path", str(video), "--audio_path", str(audio),
               "--video_out_path", str(output)]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"LatentSync не обработала видео: {result.stderr[-600:]}")
    if not output.is_file() or not any(s.get("codec_type") == "video" for s in probe(output).get("streams", [])):
        raise RuntimeError("LatentSync не вернула видео")
    return output
