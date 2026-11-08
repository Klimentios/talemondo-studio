"""Motion graphics for the edit sheet: Remotion templates (tools/motion) rendered locally with alpha.

A graphic is a template name plus its props; it renders once to ProRes 4444 (.mov with alpha), cached under
<Studio data>/motion/<hash>.mov, and edl.py overlays it on the film. Rendering needs Node and the packages in
tools/motion (npm install there); Chrome is the installed Google Chrome (see tools/motion/render.mjs).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .agent_brain import PATH, REPO, clean_env

TOOL = REPO / "tools" / "motion"
VERSION = 1

# template → (Russian description, props it reads). Fields shared by all: accent, ink, paper (CSS colours).
TEMPLATES = {
    "NameCard": ("Титр героя на стоп-кадре в стиле Ричи: имя влетает на плашке, строка печатается",
                 {"name": "имя", "line": "строка-характеристика", "tag": "метка («No. 1»)", "side": "left|right",
                  "tilt": "наклон, градусы"}),
    "KineticTitle": ("Кинетический заголовок: слова врезаются по одному", {"text": "текст", "highlight": "слово акцентом",
                                                                         "position": "center|top|bottom"}),
    "LowerThird": ("Плашка с именем снизу", {"name": "имя", "title": "кто это"}),
    "TitleCard": ("Титульная карточка: название и подзаголовок", {"title": "название", "subtitle": "подзаголовок",
                                                                 "backdrop": "затемнить фон"}),
    "Captions": ("Субтитры по словам с подсветкой", {"words": "[{text, start, end}] от начала графики",
                                                    "perPage": "слов на экране"}),
}


def node() -> str | None:
    return shutil.which("node", path=PATH)


def ready() -> bool:
    return bool(node()) and (TOOL / "node_modules" / "remotion").is_dir()


def describe() -> list[dict]:
    return [{"template": name, "description": text, "props": props} for name, (text, props) in TEMPLATES.items()]


def render(template: str, props: dict, width: int, height: int, fps: int, seconds: float, root: Path) -> Path:
    if template not in TEMPLATES:
        raise ValueError(f"Нет шаблона графики «{template}». Есть: {', '.join(TEMPLATES)}")
    if not ready():
        raise ValueError("Моушен-графика не установлена: нужен Node и `npm install` в tools/motion")
    frames = max(1, round(seconds * fps))
    request = {"template": template, "props": props, "width": width, "height": height, "fps": fps,
               "durationInFrames": frames}
    sources = sorted((TOOL / "src").rglob("*.ts*"))
    digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode())
    for file in sources:
        digest.update(file.read_bytes())
    folder = root / "motion"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{digest.hexdigest()[:24]}.mov"
    if target.is_file():
        return target
    job = folder / f".{target.stem}.json"
    job.write_text(json.dumps({**request, "output": str(target)}, ensure_ascii=False), encoding="utf-8")
    try:
        result = subprocess.run([node(), str(TOOL / "render.mjs"), str(job)], cwd=str(TOOL), env=clean_env(),
                                capture_output=True, text=True, timeout=600, check=False)
    finally:
        job.unlink(missing_ok=True)
    if result.returncode or not target.is_file():
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
        raise ValueError(f"Графика «{template}» не отрендерилась: {' '.join(tail)[:400]}")
    return target


def prune(root: Path, keep: int = 200) -> None:
    """Old renders go first; graphics are cheap to render again."""
    files = sorted((root / "motion").glob("*.mov"), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        try:
            os.unlink(path)
        except OSError:
            pass
