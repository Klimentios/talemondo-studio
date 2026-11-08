"""Bounded disk use for scene concats (storyboards/) and edit assemblies (edl_renders/).

Every «Склеить»/«Собрать фильм»/«Экспорт» writes a new MP4 (often gigabytes) and nothing deleted them.
After each new file, keep the newest KEEP per directory; never delete one the user's saved Studio draft
(lastConcat / filmAssembly, as concat_id or url) or a saved montage (montages/*.json) still points to."""
from __future__ import annotations

import json
import logging
from pathlib import Path

KEEP = 20
_log = logging.getLogger("h3.studio")


def referenced_text(store) -> str:
    """Serialized draft + saved montages: an id that appears anywhere in them is in use."""
    parts = []
    try:
        parts.append(json.dumps(store.load_draft() or {}, ensure_ascii=False))
    except Exception:
        _log.exception("Хранение склеек: черновик не прочитан, ничего не удаляю")
        raise
    for project in (Path(store.root) / "montages").glob("*.json"):
        parts.append(project.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def prune(directory: Path, store, *, keep: int = KEEP, busy: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """Delete <id>.mp4 (+ its <id>.json) beyond the newest `keep`; returns the removed ids."""
    groups: dict[str, list[Path]] = {}
    for path in Path(directory).iterdir():
        if path.name.startswith(".") or path.suffix not in {".mp4", ".json"} or not path.is_file():
            continue
        groups.setdefault(path.stem, []).append(path)
    ids = [stem for stem, files in groups.items() if any(f.suffix == ".mp4" for f in files)]
    if len(ids) <= keep:
        return []
    ids.sort(key=lambda stem: max(f.stat().st_mtime for f in groups[stem]), reverse=True)
    in_use = referenced_text(store)
    removed = []
    for stem in ids[keep:]:
        if stem in busy or stem in in_use:
            continue
        for path in groups[stem]:
            path.unlink(missing_ok=True)
        removed.append(stem)
    return removed
