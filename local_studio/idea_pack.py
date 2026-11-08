"""«Пакет идеи»: one reference set and one look for every scene of an idea.

An agent told «franchise X, idea Y» builds the pack before drafting: the idea card of THAT franchise, the heroes,
costumes, props and places its text names (the passport/bible resolver of franchise_profile.py), the franchise
style plates and look, its approved rules and the idea's own pictures. Two-panel concept sheets stay for reading
only: H3 copies their split. The saved pack is the allow-list of the idea's scenes:
scene_draft with idea_id rejects a reference, a hero, a look or a render mode outside it, so two ideas or two
franchises never mix. Same idea in a new revision of its card → the pack is stale until rebuilt. No GPU.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import scene_frames
from .franchise_profile import (HERO_ROLES, MAX_IMAGES, bible_roles, entity_cards, load_profile, resolve_shot,
                                translit)
from .franchises import FranchiseStore
from .store import REFERENCE_ROLE_PROMPTS

MODES = ("realistic", "animation3d", "animation2d", "replace")
IDEA_ROLES = {"Стиль", "Композиция", "Окружение", "Свет", "Цвет", "Предмет"}  # never a hero identity


class PackAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    asset_id: str = Field(min_length=1, max_length=120)
    role: str
    reason: str = Field(default="", max_length=300)

    @field_validator("role")
    @classmethod
    def known_role(cls, value: str) -> str:
        if value not in IDEA_ROLES:
            raise ValueError(f"Роль «{value}» не для картинки идеи: только {', '.join(sorted(IDEA_ROLES))}. "
                             "Лица и костюмы героев берутся из паспорта франшизы.")
        return value


class PackBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    mode: str = Field(pattern=f"^({'|'.join(MODES)})$")
    idea_assets: list[PackAsset] = Field(default_factory=list, max_length=4)   # the idea's own pictures to use
    extra_assets: list[PackAsset] = Field(default_factory=list, max_length=4)  # library pictures, reason required
    # Heroes and places the idea implies without naming them («дома»): names of THIS franchise's passport/bible only.
    add_names: list[str] = Field(default_factory=list, max_length=8)


def named(name: str, text: str) -> bool:
    """A bible name in idea prose, whatever its Russian case: «Кухня» in «на кухню», «Нова» in «у Новы»."""
    words = name.split()
    if not words:
        return False
    stems = [re.escape(w[:-1] if len(w) > 3 and w[-1].lower() in "аяоеиыуюьй" else w) for w in words]
    pattern = r"\s+".join(f"{stem}[а-яё]{{0,3}}" for stem in stems)
    return re.search(rf"(?<!\w){pattern}(?!\w)", text, re.IGNORECASE) is not None


def fingerprint(pack: dict) -> str:
    keys = ("franchise_id", "idea_id", "idea_revision", "mode", "cast", "references", "style_lock")
    return hashlib.sha256(json.dumps({k: pack[k] for k in keys}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def alias_keys(cast: list[dict]) -> set[str]:
    return {translit(name) for hero in cast for name in [hero["name"], *hero["aliases"]]}


def scene_findings(pack: dict, scene: dict) -> list[str]:
    """Why a scene draft leaves its idea pack; empty = the scene stays inside it."""
    problems = []
    if scene.get("franchise_id") != pack["franchise_id"]:
        problems.append("сцена другой франшизы, чем пакет идеи")
    if pack.get("stale"):
        problems.append("карточка идеи изменилась после сборки пакета — пересобери idea_pack_build")
    allowed = {ref["asset_id"]: ref for ref in pack["references"]}
    for ref in scene.get("references", []):
        own = allowed.get(ref.get("asset_id"))
        if not own:
            problems.append(f"референс {str(ref.get('asset_id'))[:8]} не из пакета идеи «{pack['idea_title']}»")
        elif ref.get("role") != own["role"]:
            problems.append(f"референс {own['asset_id'][:8]}: роль «{ref.get('role')}», в пакете — «{own['role']}»")
        elif own.get("owner") and str(ref.get("owner", "")).casefold() != own["owner"].casefold():
            problems.append(f"референс {own['asset_id'][:8]} принадлежит {own['owner']}, а не «{ref.get('owner', '')}»")
    names = alias_keys(pack["cast"])
    for index, shot in enumerate(scene.get("shots", []), 1):
        for hero in shot.get("characters") or []:
            if isinstance(hero, str) and translit(hero.strip()) not in names:
                problems.append(f"шот {index}: «{hero}» не в составе идеи ({', '.join(h['name'] for h in pack['cast']) or 'состав пуст'})")
    style = pack["style_lock"]["style"]
    if style and (scene.get("style") or "").strip() != style:
        problems.append("стиль сцены расходится с пакетом идеи — поле style должно быть ровно style_lock.style")
    if scene.get("mode") and scene["mode"] != pack["mode"]:
        problems.append(f"режим сцены «{scene['mode']}», в пакете идеи — «{pack['mode']}»")
    return problems


def make_router(store) -> APIRouter:
    data = FranchiseStore(store.root)
    root = Path(store.root) / "idea-packs"
    router = APIRouter(prefix="/api/franchises/{fid}/ideas/{eid}")

    def idea_card(fid: str, eid: str) -> tuple[dict, list[dict]]:
        cards = entity_cards(data, fid)
        card = next((c for c in cards if c["id"] == eid), None)
        if not card or card.get("type") not in {"idea", "scene"}:
            raise HTTPException(404, "Идея не найдена в этой франшизе")
        return card, cards

    def path(fid: str, eid: str) -> Path:
        return root / f"{fid}__{eid}.json"

    def read(fid: str, eid: str) -> dict | None:
        file = path(fid, eid)
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None

    def picture(asset_id: str) -> dict | None:
        asset = store.get_asset(asset_id)
        return asset if asset and asset.get("kind") == "image" else None

    def is_split(asset: dict) -> bool:
        try:
            return bool(asset.get("path")) and scene_frames.split_panel(Path(asset["path"]).read_bytes())
        except OSError:
            return False

    @router.get("/pack")
    def get_pack(fid: str, eid: str) -> dict:
        card, _ = idea_card(fid, eid)
        pack = read(fid, eid)
        if not pack:
            raise HTTPException(404, "Пакет идеи ещё не собран: idea_pack_build с expected_revision=0")
        return {**pack, "stale": pack["idea_revision"] != card["revision"]}

    @router.post("/pack")
    def build_pack(fid: str, eid: str, payload: PackBuild) -> dict:
        card, cards = idea_card(fid, eid)
        profile, _ = load_profile(data, fid)
        ids = {a.asset_id for r in profile.roles for a in r.assets} | {a for c in cards for a in c.get("asset_ids", [])}
        assets = {i: a for i in ids if (a := store.get_asset(i))}
        roles = bible_roles(profile, cards, assets)
        text = f"{card.get('title', '')}\n{card.get('body', '')}"
        present = [role for role in roles if any(named(n, text) for n in [role.name, *role.aliases])]
        for name in payload.add_names:
            role = next((r for r in roles if translit(name.strip()) in {translit(n) for n in [r.name, *r.aliases]}), None)
            if not role:
                raise HTTPException(422, f"«{name}» нет в паспорте и библии этой франшизы")
            if role not in present:
                present.append(role)
        # The resolver sees the idea as one shot that names every hero, costume, prop and place of its text.
        resolved = resolve_shot(profile, roles, assets, {"characters": [role.name for role in present]})
        cast = [{"name": r.name, "aliases": list(r.aliases)} for r in present if r.kind == "character"]
        places = [r.name for r in present if r.kind in {"location", "fantasy"}]
        notes = list(resolved["notes"])
        references = [{"asset_id": i, "role": resolved["reference_roles"][i],
                       "owner": resolved["reference_owners"].get(i, ""), "source": "franchise"}
                      for i in resolved["asset_ids"]]
        visuals = []
        for asset_id in card.get("asset_ids", []):
            asset = picture(asset_id)
            if asset:
                visuals.append({"asset_id": asset_id, "split": is_split(asset), "used": False})
        by_visual = {v["asset_id"]: v for v in visuals}
        images = sum(1 for ref in references if REFERENCE_ROLE_PROMPTS[ref["role"]][0] == "image")
        taken = {ref["asset_id"] for ref in references}
        for source, items in (("idea", payload.idea_assets), ("library", payload.extra_assets)):
            for item in items:
                if item.asset_id in taken:
                    raise HTTPException(422, f"{item.asset_id[:8]} уже в пакете")
                if source == "idea":
                    visual = by_visual.get(item.asset_id)
                    if not visual:
                        raise HTTPException(422, f"{item.asset_id[:8]} не картинка этой идеи")
                    if visual["split"]:
                        raise HTTPException(422, f"{item.asset_id[:8]} — лист из двух половин: H3 копирует раздел, "
                                                 "только для чтения")
                    visual["used"] = True
                else:
                    if not item.reason:
                        raise HTTPException(422, "Картинке из библиотеки нужна причина (reason)")
                    asset = picture(item.asset_id)
                    if not asset:
                        raise HTTPException(422, f"{item.asset_id[:8]} не картинка библиотеки")
                    if asset.get("role") in HERO_ROLES:
                        raise HTTPException(422, f"{item.asset_id[:8]} — фото героя: героев даёт только паспорт франшизы")
                    if is_split(asset):
                        raise HTTPException(422, f"{item.asset_id[:8]} — лист из двух половин, не референс")
                if images >= MAX_IMAGES:
                    raise HTTPException(422, f"больше {MAX_IMAGES} картинок в пакете")
                images += 1
                taken.add(item.asset_id)
                references.append({"asset_id": item.asset_id, "role": item.role, "owner": "", "source": source,
                                   **({"reason": item.reason} if item.reason else {})})
        if not cast:
            notes.append("В тексте идеи не найдено ни одного героя паспорта/библии этой франшизы")
        if not any(ref["role"] in {"Окружение", "Кадр сцены", "Композиция"} for ref in references):
            notes.append("В пакете нет места действия: добавь локацию в библию франшизы или картинку роли «Окружение»")
        look = profile.look
        rules = [{"id": c["id"], "title": c.get("title", ""), "rule": str(c.get("body", ""))[:500]}
                 for c in cards if c.get("type") == "rule" and c.get("state") == "approved"][:12]
        previous = read(fid, eid)
        revision = previous["revision"] if previous else 0
        if payload.expected_revision != revision:
            # Agents pass the idea card's revision here; name the number to send instead of a re-read loop.
            raise HTTPException(409, f"expected_revision — версия пакета, а не карточки идеи: сейчас пакет в версии "
                                     f"{revision}, передай expected_revision={revision}")
        pack = {"franchise_id": fid, "idea_id": eid, "idea_title": card.get("title", ""),
                "idea_state": card.get("state", "draft"), "idea_revision": card["revision"], "revision": revision + 1,
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": payload.mode,
                "cast": cast, "places": places, "references": references, "idea_visuals": visuals,
                "style_lock": {"style": look.style, "look": resolved["look"], "director_config": resolved["director_config"],
                               "aspect_ratio": look.aspect_ratio, "language": look.language},
                "rules": rules, "notes": notes}
        pack["fingerprint"] = fingerprint(pack)
        root.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=root, suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(pack, out, ensure_ascii=False, indent=1)
        os.replace(temp, path(fid, eid))
        return {**pack, "stale": False}

    @router.post("/pack/check")
    def check_scene(fid: str, eid: str, scene: dict) -> dict:
        problems = scene_findings(get_pack(fid, eid), scene)
        return {"ok": not problems, "problems": problems}

    return router
