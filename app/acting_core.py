"""LivePortrait preparation contract. The runtime lives outside the H3 venv."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path


MODEL_FILES = {
    "human": (
        "liveportrait/base_models/appearance_feature_extractor.pth",
        "liveportrait/base_models/motion_extractor.pth",
        "liveportrait/base_models/spade_generator.pth",
        "liveportrait/base_models/warping_module.pth",
        "liveportrait/retargeting_models/stitching_retargeting_module.pth",
        "liveportrait/landmark.onnx",
    ),
    "animal": (
        "liveportrait_animals/base_models_v1.1/appearance_feature_extractor.pth",
        "liveportrait_animals/base_models_v1.1/motion_extractor.pth",
        "liveportrait_animals/base_models_v1.1/spade_generator.pth",
        "liveportrait_animals/base_models_v1.1/warping_module.pth",
        "liveportrait_animals/xpose.pth",
    ),
}


def runtime_paths() -> tuple[Path, Path]:
    return (Path(os.environ.get("H3_LIVEPORTRAIT_ROOT", "/workspace/h3/LivePortrait")),
            Path(os.environ.get("H3_LIVEPORTRAIT_PYTHON", "/workspace/h3/liveportrait-venv/bin/python")))


def capability() -> dict:
    root, python = runtime_paths()
    base = root / "pretrained_weights"
    common = root.is_dir() and python.is_file()
    # This checks installed files only. The installation smoke test writes this
    # marker after importing and constructing both pipelines in the isolated venv.
    marker = root / ".h3-acting-smoke-ok"
    ready = common and marker.is_file()
    return {kind: bool(ready and all((base / name).is_file() for name in files))
            for kind, files in MODEL_FILES.items()}


def cache_key(source: bytes, driving: bytes, mode: str, options: dict, version: str) -> str:
    if mode not in MODEL_FILES:
        raise ValueError("Режим актёрской игры должен быть human или animal")
    digest = hashlib.sha256()
    for chunk in (source, driving, mode.encode(),
                  json.dumps(options, sort_keys=True, separators=(",", ":")).encode(), version.encode()):
        digest.update(len(chunk).to_bytes(8, "big"))
        digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def gpu_admission():
    """Single host lock shared by H3 submission and LivePortrait inference."""
    path = Path(os.environ.get("H3_GPU_ADMISSION_LOCK", "/tmp/h3-gpu-admission.lock"))
    with path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def gpu_admission_dependency():
    with gpu_admission():
        yield
