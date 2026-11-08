"""Patch only LivePortrait route bindings into the running GPU API checkout.

Run on the GPU host after copying app/acting*.py. Preserves a timestamped backup
and refuses to edit an unexpected version of either shared source file.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import urllib.request


BASE = Path("/workspace/h3/api/app")


def replace_once(source: str, old: str, new: str) -> str:
    if old == new:
        return source
    if new in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError(f"Unexpected shared API source around {old[:60]!r}")
    return source.replace(old, new, 1)


def main() -> None:
    with urllib.request.urlopen("http://127.0.0.1:18188/queue", timeout=5) as response:
        queue = json.load(response)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("ComfyUI queue is not empty; no API files changed")
    for name in ("acting.py", "acting_core.py", "acting_runner.py"):
        if not (BASE / name).is_file():
            raise RuntimeError(f"Missing {name}")
    main_path = BASE / "main.py"
    director_path = BASE / "director.py"
    main_source = main_path.read_text()
    director_source = director_path.read_text()
    main_updated = replace_once(main_source,
        "from fastapi import FastAPI, File, Form, HTTPException, UploadFile",
        "from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile")
    main_updated = replace_once(main_updated,
        "from .video_reference import prepare_video_reference",
        "from .video_reference import prepare_video_reference\nfrom .acting import router as acting_router\nfrom .acting_core import gpu_admission_dependency")
    main_updated = replace_once(main_updated,
        '@app.post("/api/jobs", status_code=202)',
        '@app.post("/api/jobs", status_code=202, dependencies=[Depends(gpu_admission_dependency)])')
    main_updated = replace_once(main_updated,
        'app.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="studio")',
        'app.include_router(acting_router)\napp.mount("/", StaticFiles(directory=ROOT / "static", html=True), name="studio")')
    director_updated = replace_once(director_source,
        '"Исходное видео": "source", "Движение": "motion", "Камера": "camera",',
        '"Исходное видео": "source", "Движение": "motion", "Актёрская игра": "motion", "Камера": "camera",')
    # Validate both files before modifying either. Backups are outside the API
    # package, and user data/model paths are untouched.
    compile(main_updated, str(main_path), "exec")
    compile(director_updated, str(director_path), "exec")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BASE.parent / "backup" / f"acting-api-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    (backup_dir / "main.py").write_text(main_source)
    (backup_dir / "director.py").write_text(director_source)
    for path, content in ((main_path, main_updated), (director_path, director_updated)):
        temporary = path.with_suffix(path.suffix + ".acting-new")
        temporary.write_text(content)
        os.replace(temporary, path)
    print(f"ACTING_API_PATCHED backup={backup_dir}")


if __name__ == "__main__":
    main()
