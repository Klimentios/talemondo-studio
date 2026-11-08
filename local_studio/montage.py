"""«Монтаж»: saved edits, each one edit sheet (EDL, edl.py) — the same document the page, the agent and the export use.

The page (preview/h3-director/editor) edits the sheet on a timeline; a Studio agent (MCP montage_*) reads and
writes the very same file, and the open page picks the change up from the revision it polls. What the timeline
shows is what /api/edl/renders assembles: there is no second, preview-only format any more.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .edl import MAX_CLIPS, Edl
from .montage_ops import TimelineEdit, apply_ops

MAX_BYTES = 2 * 1024 * 1024


class Save(BaseModel):
    project: dict
    revision: str | None = Field(default=None, max_length=64)   # None: overwrite (agent), else optimistic lock
    by: str = Field(default="user", pattern="^(user|agent)$")
    note: str = Field(default="", max_length=600)               # what the agent changed, shown on the page


class Create(BaseModel):
    name: str = Field(default="Новый монтаж", max_length=120)
    edl: dict | None = None


class Shot(BaseModel):
    job_id: str = Field(default="", max_length=120)
    asset_id: str = Field(default="", max_length=120)
    label: str = Field(default="", max_length=120)
    duration: float | None = Field(default=None, gt=0, le=3600)
    transition_in: str = Field(default="cut", max_length=40)


class Append(BaseModel):
    shots: list[Shot] = Field(min_length=1, max_length=MAX_CLIPS)


def blank_edl() -> dict:
    return {"version": 1, "title": "", "aspect_ratio": "9:16", "fps": 24, "fit": "crop", "clips": [], "sfx": [],
            "graphics": [], "fade_in": 0, "fade_out": 0.5, "auto_sfx": True, "loudness": -14}


def check_edl(edl: dict) -> dict:
    """Validate a sheet that may still be empty (a new montage); return it normalised, aliases kept (in/out)."""
    if not isinstance(edl, dict):
        raise ValueError("Монтаж: ожидался объект монтажного листа")
    clips = edl.get("clips") or []
    probe = {**edl, "clips": clips or [{"job_id": "empty"}]}
    try:
        sheet = Edl.model_validate(probe)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ValueError(f"{where}: {first['msg']}") from exc
    out = sheet.model_dump(by_alias=True, exclude_none=False)
    if not clips:
        out["clips"] = []
    return out


def from_legacy(old: dict) -> dict:
    """The pre-2026-10-28 manual timeline (editor_projects/current.json) as a sheet: «Видео 1» in order, its sound,
    the first music clip, SFX and speech clips as sounds. Overlays, transforms and per-clip filters are dropped."""
    tracks = {track.get("id"): track for track in old.get("tracks", []) if isinstance(track, dict)}
    detached = {clip.get("assetId"): clip for clip in tracks.get("sourceAudio", {}).get("clips", [])}
    clips = []
    for clip in sorted(tracks.get("video1", {}).get("clips", []), key=lambda c: c.get("timelineStart", 0)):
        if clip.get("type") != "video":
            continue
        is_job = bool((clip.get("aiMetadata") or {}).get("generationId"))
        volume = clip.get("volume", 1) or (detached.get(clip.get("assetId"), {}).get("volume", 0))
        clips.append({"job_id" if is_job else "asset_id": clip["assetId"], "label": str(clip.get("name", ""))[:120],
                      "in": round(float(clip.get("sourceStart", 0)), 3), "out": round(float(clip.get("sourceEnd", 1)), 3),
                      "speed": min(max(float(clip.get("speed", 1)), 0.25), 4), "volume": min(max(float(volume), 0), 2),
                      "audio": "source" if volume else "mute",
                      "transition_in": "dissolve" if clip.get("transition") in {"fade", "fade_both"} and clips else "cut"})
    edl = {**blank_edl(), "title": str(old.get("name", ""))[:200], "clips": clips[:MAX_CLIPS],
           "aspect_ratio": "16:9" if old.get("aspectRatio") == "16:9" else "9:16", "auto_sfx": False}
    music = tracks.get("music", {}).get("clips", [])
    if music:
        edl["music"] = {"asset_id": music[0]["assetId"], "start": float(music[0].get("timelineStart", 0)),
                        "source_in": float(music[0].get("sourceStart", 0)),
                        "volume": min(max(float(music[0].get("volume", 0.35)), 0), 2)}
    for track in ("speech", "sfx"):
        for clip in tracks.get(track, {}).get("clips", [])[:40 - len(edl["sfx"])]:
            edl["sfx"].append({"asset_id": clip["assetId"], "at": float(clip.get("timelineStart", 0)),
                               "source_in": float(clip.get("sourceStart", 0)),
                               "duration": max(0.1, float(clip.get("sourceEnd", 1)) - float(clip.get("sourceStart", 0))),
                               "volume": min(max(float(clip.get("volume", 1)), 0), 2)})
    return edl


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/montages")
    folder = store.root / "montages"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    active_file = folder / "active.json"
    lock_file = folder / ".lock"

    def path(montage_id: str) -> Path:
        if len(montage_id) != 32 or not all(c in "0123456789abcdef" for c in montage_id):
            raise HTTPException(404, "Монтаж не найден")
        return folder / f"{montage_id}.json"

    def read(montage_id: str) -> tuple[dict, str]:
        file = path(montage_id)
        if not file.is_file():
            raise HTTPException(404, "Монтаж не найден")
        raw = file.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def write(project: dict) -> str:
        project["updated"] = time.time()
        raw = json.dumps(project, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, "Монтаж больше 2 МБ")
        file = path(project["id"])
        temp = folder / f".{project['id']}.{os.getpid()}.tmp"
        temp.write_bytes(raw)
        temp.chmod(0o600)
        os.replace(temp, file)
        return hashlib.sha256(raw).hexdigest()

    def new(name: str, edl: dict | None) -> dict:
        try:
            sheet = check_edl({**blank_edl(), **(edl or {})})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": uuid.uuid4().hex, "name": name or sheet.get("title") or "Новый монтаж", "created": time.time(),
                "edl": sheet, "by": "user", "note": ""}

    def set_active(montage_id: str) -> None:
        active_file.write_text(json.dumps({"id": montage_id}), encoding="utf-8")

    def active_id() -> str:
        try:
            montage_id = json.loads(active_file.read_text(encoding="utf-8"))["id"]
            return montage_id if path(montage_id).is_file() else ""
        except (OSError, ValueError, KeyError, HTTPException):
            return ""

    def migrate() -> None:
        legacy = store.root / "editor_projects" / "current.json"
        done = folder / ".legacy-imported"
        if done.exists() or not legacy.is_file():
            return
        try:
            old = json.loads(legacy.read_text(encoding="utf-8"))
            edl = from_legacy(old)
            if edl["clips"]:
                project = new(str(old.get("name") or "Монтаж до обновления"), edl)
                write(project)
                set_active(project["id"])
        except (OSError, ValueError, KeyError, TypeError, HTTPException):
            pass
        done.write_text("1", encoding="utf-8")

    migrate()

    @router.get("/media")
    async def media() -> dict:
        """The bin: every finished H3 shot (hidden from the queue too — the queue is not the archive) and the
        library's video and sound files."""
        shots = []
        with store.connect() as db:
            rows = db.execute("SELECT job_id, original_prompt, settings_json, output_path, created_at FROM jobs "
                              "WHERE state='completed' AND output_path IS NOT NULL ORDER BY created_at DESC").fetchall()
        for row in rows:
            row = dict(row)
            if not Path(row["output_path"]).is_file():
                continue
            try:
                settings = json.loads(row.get("settings_json") or "{}")
            except ValueError:
                settings = {}
            shots.append({"kind": "job", "id": row["job_id"], "original_prompt": (row["original_prompt"] or "")[:1200],
                          "seconds": settings.get("duration"), "aspect_ratio": settings.get("aspect_ratio", ""),
                          "created_at": row["created_at"], "url": f"/api/jobs/{row['job_id']}/video",
                          "poster": f"/api/jobs/{row['job_id']}/poster"})
        files = [{"kind": asset["kind"], "id": asset["asset_id"], "name": asset.get("original_name") or "Файл",
                  "role": asset.get("role") or "", "created_at": asset.get("created_at", ""),
                  "url": f"/api/assets/{asset['asset_id']}/file",
                  "poster": f"/api/assets/{asset['asset_id']}/thumbnail" if asset["kind"] == "video" else ""}
                 for asset in store.list_assets() if asset.get("kind") in {"video", "audio"}]
        return {"shots": shots, "files": files}

    @router.get("")
    async def listing() -> dict:
        items = []
        for file in folder.glob("*.json"):
            if file.name == "active.json":
                continue
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({"id": data["id"], "name": data.get("name", ""), "updated": data.get("updated", 0),
                          "clips": len(data.get("edl", {}).get("clips", [])),
                          "aspect_ratio": data.get("edl", {}).get("aspect_ratio", "9:16")})
        items.sort(key=lambda item: -item["updated"])
        return {"active": active_id() or (items[0]["id"] if items else ""), "montages": items}

    @router.post("", status_code=201)
    async def create(payload: Create) -> dict:
        project = new(payload.name, payload.edl)
        revision = write(project)
        set_active(project["id"])
        return {"project": project, "revision": revision}

    @router.get("/{montage_id}")
    async def get(montage_id: str) -> dict:
        project, revision = read(montage_id)
        return {"project": project, "revision": revision}

    @router.get("/{montage_id}/revision")
    async def revision(montage_id: str) -> dict:
        project, revision = read(montage_id)
        return {"revision": revision, "by": project.get("by", "user"), "note": project.get("note", ""),
                "updated": project.get("updated", 0)}

    @router.post("/{montage_id}/activate")
    async def activate(montage_id: str) -> dict:
        read(montage_id)
        set_active(montage_id)
        return {"active": montage_id}

    @router.put("/{montage_id}")
    async def save(montage_id: str, payload: Save) -> dict:
        project = payload.project
        if project.get("id") != montage_id:
            raise HTTPException(422, "id монтажа не совпадает с адресом")
        try:
            project["edl"] = check_edl(project.get("edl"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        project["name"] = str(project.get("name") or "Монтаж")[:120]
        with lock_file.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current, current_revision = read(montage_id)
            if payload.revision is not None and payload.revision != current_revision:
                raise HTTPException(409, "Монтаж изменился (агент или другая вкладка) — страница подтянет новую версию")
            project.update(created=current.get("created", time.time()), by=payload.by,
                           note=payload.note if payload.by == "agent" else "")
            revision = write(project)
        return {"revision": revision}

    @router.post("/{montage_id}/edit")
    async def edit(montage_id: str, payload: TimelineEdit) -> dict:
        project, revision = read(montage_id)
        if revision != payload.revision:
            raise HTTPException(409, "Монтаж изменился — прочитай текущую версию и пересмотри операции")
        try:
            project["edl"] = apply_ops(project["edl"], payload.ops)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        # save validates the entire EDL and rechecks the revision under the file lock.
        change = Save(project=project, revision=revision, by="agent", note=payload.note)
        saved = await save(montage_id, change)
        return {"project": change.project, "revision": saved["revision"], "applied": len(payload.ops)}

    @router.post("/{montage_id}/append")
    async def append(montage_id: str, payload: Append) -> dict:
        """«В монтаж» from the story: shots go to the end of the cut, each trimmed to its planned length."""
        with lock_file.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            project, _ = read(montage_id)
            clips = project["edl"]["clips"]
            for shot in payload.shots:
                clip = {"job_id": shot.job_id, "asset_id": shot.asset_id, "label": shot.label,
                        "transition_in": shot.transition_in if clips else "cut"}
                if shot.duration:
                    clip["out"] = shot.duration
                clips.append(clip)
            try:
                project["edl"] = check_edl(project["edl"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            revision = write(project)
        set_active(montage_id)
        return {"project": project, "revision": revision}

    @router.delete("/{montage_id}")
    async def delete(montage_id: str) -> dict:
        file = path(montage_id)
        if file.is_file():
            trash = folder / "deleted"
            trash.mkdir(exist_ok=True)
            os.replace(file, trash / file.name)   # kept for recovery, out of the list
        return {"deleted": montage_id}

    return router
