"""Every job, not only scene runs, keeps the medium of the franchise passport its hero pictures belong to.

Without this check a photoreal live-action franchise can be rendered as a 3D cartoon: direct /api/jobs and MCP
render_enqueue would carry no passport check. The franchise is inferred from the job's hero
pictures; a picture shared by franchises of different media, or owned by none, is not judged.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

HERO_ROLES = {"Персонаж целиком", "Лицо героя", "Костюм"}
# Hero replacement over a source video keeps the source medium; it is valid in either passport.
PASSPORT_MODES = {"photoreal": {"realistic", "replace"}, "animation3d": {"animation3d", "replace"}}
PASSPORT_ADAPTERS = {"photoreal": {"style3d", "cartoon2d"}, "animation3d": {"realism_people", "cartoon2d"}}


def asset_media(franchise_db: Path) -> dict[str, set[tuple[str, str]]]:
    """asset_id → {(franchise name, passport rendering)} from passports and entity cards."""
    if not franchise_db.is_file():
        return {}
    db = sqlite3.connect(f"file:{franchise_db}?mode=ro", uri=True)
    try:
        names = dict(db.execute("SELECT id, name FROM franchises"))
        rendering, owners = {}, {}
        for fid, content in db.execute("SELECT franchise_id, content FROM profiles"):
            profile = json.loads(content or "{}")
            rendering[fid] = str(((profile.get("director_defaults") or {}).get("style") or {}).get("rendering") or "")
            for role in profile.get("roles") or []:
                for asset in role.get("assets") or []:
                    owners.setdefault(asset.get("asset_id"), set()).add(fid)
        for fid, content in db.execute("SELECT franchise_id, content FROM entities"):
            for asset_id in (json.loads(content or "{}").get("asset_ids") or []):
                owners.setdefault(asset_id, set()).add(fid)
    finally:
        db.close()
    return {asset_id: {(names.get(fid, fid), rendering.get(fid, "")) for fid in fids} for asset_id, fids in owners.items()}


def medium_problem(franchise_db: Path, hero_asset_ids: list[str], mode: str, style_adapter: str) -> str:
    media = asset_media(franchise_db)
    renderings, franchises = set(), set()
    for asset_id in hero_asset_ids:
        owned = media.get(asset_id)
        if not owned:
            continue
        renderings |= {r for _, r in owned}
        franchises |= {n for n, _ in owned}
    renderings.discard("")
    if len(renderings) != 1:
        return ""
    rendering = renderings.pop()
    allowed = PASSPORT_MODES.get(rendering)
    if not allowed:
        return ""
    who = ", ".join(sorted(franchises))
    if mode not in allowed:
        return (f"Фото героев из франшизы {who}: паспорт — {rendering}, а режим задания {mode}. Манера меняется "
                "только словами пользователя в паспорте франшизы, не в задании")
    if style_adapter in PASSPORT_ADAPTERS.get(rendering, set()):
        return f"Фото героев из франшизы {who}: паспорт — {rendering}, LoRA {style_adapter} даёт другую манеру"
    return ""
