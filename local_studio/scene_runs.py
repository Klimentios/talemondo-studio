"""Scene runs: «Собрать сцену» runs on the Mac, not in the browser tab.

Once a scene is started, its whole build goes on here without an open tab or an agent: keyframes, a stop until the
user accepts each frame («✓ Принять кадр»), the shot renders in order, last frames for continuations and colour,
and the glued scene at the end. Every scene has its own run (scene-runs/<scene_id>.json), so scenes never mix.

The decisions are the tab's own: each step is chainStep of preview/h3-director/storyboard-model.mjs, run by
scripts/scene_run_step.mjs. The page compiles every shot's job once at start (h3Studio.enqueueCut with compile),
with stand-ins for pictures that exist only later (storyboard-model.mjs runSources); this loop puts the real
asset ids in and sends the jobs through the same Studio API the tab uses. The tab only shows the run and has
Stop / Continue.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .take_acceptance import take_is_accepted
from .scene_versions import scene_definition, context_definition
from . import scene_frames
from .motion_director import SavedPlanInput
from .config import ROOT
from app.continuation import TAIL_SECONDS, CLIP_FRAMES, FPS
from app.h3_ir import SECTIONS, is_ir_prompt, parse_ir
from . import scene_kit, shot_contract

log = logging.getLogger(__name__)
POLL_S = 3.0
STEP_SCRIPT = ROOT / "scripts" / "scene_run_step.mjs"
SCENE_ID = re.compile(r"[\w-]{1,80}")
TOKEN = re.compile(r'"(studio-run:(start|last|tail):([\w-]+))"')
IR_SECTION = re.compile(r"(?m)^([a-z_]+):[ \t]*$")
FINISHED = {"completed", "failed", "cancelled", "interrupted"}
VIDEO_ROLES = {"Исходное видео", "Движение", "Движение и камера", "Актёрская игра", "Камера", "Ритм сцены", "Продолжение",
               "Голос", "Музыка", "Звуковой эффект", "Атмосфера"}
# Fields of a shot that the run owns while it goes; the page mirrors them back into its cuts.
PIPELINE_FIELDS = ("frameAcceptance", "frameReview", "shotJobId", "selectedTakeId", "takes", "startFrame", "keyframeJobId", "keyframeJobIds",
                   "keyframeVariants", "keyframePick", "keyframeSpecSnapshot", "productionFrameChanged",
                   "productionSpecChanged", "shotQc", "shotQcPending", "shotQcError")
# What the user may still change on a shot of a running scene: accept or replace its frame, review its take.
USER_FIELDS = {"startFrame", "frameAcceptance", "shotQc", "shotQcReview", "productionFrameChanged", "frameTries"}
OPTION_FIELDS = {"keyframeEngine", "autoAccept", "autoAcceptTakes", "order", "claudeLimitedUntil"}
HERO_ROLES = {"Лицо героя", "Персонаж целиком"}
INTERRUPTED = "прервано перезапуском Studio"
CODEX_LIMIT_WAIT_S = 900  # 15 min between draws while the Codex subscription limit is exhausted
CLAUDE_LIMIT_WAIT_S = 3 * 3600  # Claude's check on its limit: shots render from pictures, look again after three hours
MAX_TAKE_TRIES = 2  # takes per shot when Claude rejects one (a take costs ~30 min of GPU)
MAX_FRAME_TRIES = 3  # Codex attempts per start frame (failed drawings and Claude's rejections together)


class RunInput(BaseModel):
    title: str = Field(default="", max_length=200)
    source_context: dict | None = None
    cuts: list[dict] = Field(min_length=1, max_length=60)
    board: dict
    references: list[dict] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)
    templates: dict[str, dict] = Field(default_factory=dict)  # cut id → {mode, body}: the compiled job


class StepError(RuntimeError):
    pass


SHOT_TIME = re.compile(r"\[Shot \d+:\s*([\d.]+)\s*-\s*([\d.]+)\s*s\]")
PICTURE_PROSE = re.compile(r"Pictures? (\d+)(?:\s*-\s*(\d+))?")


def remap_picture_mentions(prompt: str, moved: Callable[[int], int]) -> str:
    """Renumber tags and prose in one pass after a duplicate picture is removed."""
    pattern = re.compile(r"<Picture (\d+)>|\bPictures (\d+)\s*-\s*(\d+)\b|\bPicture (\d+)\b")

    def replace(match: re.Match) -> str:
        tag, first, last, single = match.groups()
        if tag:
            return f"<Picture {moved(int(tag))}>"
        if first:
            start, end = moved(int(first)), moved(int(last))
            return f"Pictures {start}-{end}" if start < end else f"Picture {start}"
        return f"Picture {moved(int(single))}"

    return pattern.sub(replace, prompt)


def drop_duplicate_pictures(body: dict, resolved: dict[str, str]) -> None:
    """Two tokens that resolve to one picture (a picture-only shot's start and last frame) would send it twice;
    keep the first, point guide_frames / color_match at it and renumber the pictures after it."""
    ids = body.get("asset_ids") or []
    while True:
        assets = [resolved.get(item, item) for item in ids]
        number = next((n for n, asset in enumerate(assets, 1) if asset in assets[:n - 1]), None)
        if number is None:
            break
        keep = assets.index(assets[number - 1]) + 1  # always before the duplicate
        item = ids.pop(number - 1)
        for field in ("reference_numbers", "reference_roles", "reference_owners", "reference_performers"):
            (body.get(field) or {}).pop(item, None)
        settings = body.setdefault("settings", {})
        moved = lambda n: keep if n == number else n - 1 if n > number else n
        settings["guide_frames"] = [[moved(n), *rest] for n, *rest in settings.get("guide_frames") or []]
        if isinstance(settings.get("color_match"), dict) and settings["color_match"].get("picture"):
            settings["color_match"]["picture"] = moved(settings["color_match"]["picture"])
        if body.get("prompt"):
            body["prompt"] = remap_picture_mentions(str(body["prompt"]), moved)
    body["reference_numbers"] = {item: n for n, item in enumerate(ids, 1)}


MEDIUM_WORDS = {"realistic": "live-action photoreal: real people, real skin, real fabric, a cinema camera; any cartoon, "
                                "3D-animated or illustrated look is a reject",
                "animation3d": "a 3D animated feature: stylized 3D characters; photoreal live action or flat 2D is a reject",
                "animation2d": "2D animation; photoreal live action or 3D render is a reject"}


def shot_pictures(references: list[dict], shot: dict | None) -> list[str]:
    """The pictures a reviewer compares a frame of this shot with: the place and props, and only this shot's heroes.
    All scene pictures went to every check, and a scene of three heroes plus its place was over the eight-picture
    limit, so the frame check gave up and stopped the scene."""
    from .franchise_profile import translit
    declared = shot_contract.declared_cast(shot)
    cast = None if declared is None else {translit(n) for n in declared}
    return [ref.get("id") or ref.get("asset_id") for ref in references if ref.get("kind") == "image"
            and (cast is None or ref.get("role") not in CAST_ROLES or translit(str(ref.get("owner") or "")) in cast)]


MEDIUM_FRAME = {"realistic": "a photoreal live-action film still: real people, real skin, hair and fabric, cinema camera "
                              "and practical light, no illustration or 3D render",
                "animation3d": "a still from a premium 3D animated feature film, not a photograph and not flat 2D",
                "animation2d": "a frame of 2D animation, not a photograph and not a 3D render"}


def take_review_rules(kit: dict | None, shot: dict | None) -> list[str]:
    """What the reviewer must hold the take to beyond the shot text: the passport medium (a photoreal franchise
    must not pass as a cartoon) and who is in the shot and who must not be (extra heroes are a reject)."""
    if not kit:
        return []
    from .scene_kit import _hero
    rules = [f"Medium of the franchise: {MEDIUM_WORDS[kit['mode']]}."] if kit.get("mode") in MEDIUM_WORDS else []
    characters = shot_contract.declared_cast(shot)
    if characters is not None:
        present = [h for h in map(lambda n: _hero(kit, n), characters) if h]
        absent = [str(h.get("name")) for h in kit.get("cast") or [] if h not in present and h.get("name")]
        rules.append(("Heroes in this shot: " + ", ".join(str(h.get("name")) for h in present) + "." if present
                      else "No franchise hero is in this shot.")
                     + (f" {', '.join(absent)} must not appear; any of them in the frame is a reject." if absent else ""))
    return rules


CAST_ROLES = {"Лицо героя", "Персонаж целиком", "Костюм", "Голос", "Актёрская игра"}


def drop_absent_heroes(body: dict, shot: dict | None) -> list[str]:
    """A hero's pictures, costume and voice ride only in the shots that hero is in (sceneShot.characters).
    When the scene's references go to every shot, a shot with only MIRA reads «<Subject 1> IRIS (appears in
    [Shot 1]): fully_preserved» and H3 draws IRIS too, or doubles a hero."""
    from .franchise_profile import translit
    declared = shot_contract.declared_cast(shot)
    if declared is None:
        return []
    cast = {translit(name) for name in declared}
    ids, roles, owners = body.get("asset_ids") or [], body.get("reference_roles") or {}, body.get("reference_owners") or {}
    gone = [a for a in ids if roles.get(a) in CAST_ROLES and owners.get(a) and translit(owners[a]) not in cast]
    if not gone:
        return []
    names = sorted({str(owners[a]) for a in gone})
    from .shot_contract import _references
    numbers = {asset: ref.number for asset, ref in zip(ids, _references(body)) if ref.kind == "image"}
    removed = sorted((numbers[a] for a in gone if a in numbers), reverse=True)
    settings = body.setdefault("settings", {})
    for number in removed:  # highest first, so every lower number stays where it is
        moved = lambda n, number=number: n - 1 if n > number else n
        settings["guide_frames"] = [[moved(n), *rest] for n, *rest in settings.get("guide_frames") or [] if n != number]
        if isinstance(settings.get("color_match"), dict) and settings["color_match"].get("picture"):
            if settings["color_match"]["picture"] == number:
                settings.pop("color_match")
            else:
                settings["color_match"]["picture"] = moved(settings["color_match"]["picture"])
        if body.get("prompt"):
            body["prompt"] = remap_picture_mentions(str(body["prompt"]), moved)
    for asset in gone:
        ids.remove(asset)
        for field in ("reference_numbers", "reference_roles", "reference_owners", "reference_performers"):
            (body.get(field) or {}).pop(asset, None)
    body["reference_numbers"] = {item: n for n, item in enumerate(ids, 1)}
    return names


def ir_binding_problems(body: dict) -> list[str]:
    """The /api/jobs official-format check, run at launch so a scene does not stop on it mid-run."""
    from app.h3_ir import build_skeleton, complete_subject_sources, is_blocking, rebind_subject_pictures, validate_ir
    prompt = str(body.get("prompt") or "")
    if not is_ir_prompt(prompt):
        return []
    refs = shot_contract._references(body)
    skeleton = build_skeleton(refs)
    for ref in refs:  # every number of its kind, as store._check_official_prompt labels them
        skeleton.labels.update(f"<{ {'image': 'Picture', 'video': 'Video', 'audio': 'Audio'}[ref.kind] } {n}>"
                               for n in range(1, ref.number + 1))
    completed = complete_subject_sources(rebind_subject_pictures(prompt, skeleton), skeleton)
    return [problem for problem in validate_ir(completed, skeleton, []) if is_blocking(problem)]


def prompt_integrity_problems(body: dict) -> list[str]:
    """One prompt gate for scene admission and the final, resolved GPU request."""
    prompt = str(body.get("prompt") or "")
    found = prompt_defects(body) + ir_binding_problems(body)
    if is_ir_prompt(prompt):
        sections = [match.group(1) for match in IR_SECTION.finditer(prompt)]
        parsed = parse_ir(prompt)
        if sections != list(SECTIONS) or not parsed.get("summary", "").startswith("["):
            found.append("нарушена структура промпта H3 или нет типа задачи в summary")
        if "Shot-specific action direction:" in parsed.get("non_diegetic_music", ""):
            found.append("действие шота вне detailed_description")
    return found


PASSPORT_MODES = {"photoreal": {"realistic"}, "animation3d": {"animation3d"}}


def medium_mismatch(profile: dict, templates: dict) -> str:
    """The franchise passport sets the medium: an agent that misreads a remark like «это мультик» must not
    render a photoreal live-action franchise as a 3D cartoon. A scene in another medium needs the passport changed first."""
    rendering = str(((profile.get("director_defaults") or {}).get("style") or {}).get("rendering") or "")
    allowed = PASSPORT_MODES.get(rendering)
    if not allowed:
        return ""
    wrong = sorted(cut_id for cut_id, template in templates.items()
                   if ((template.get("body") or {}).get("settings") or {}).get("mode") not in allowed)
    return (f"Шоты {', '.join(wrong)}: режим не совпадает с паспортом франшизы ({rendering}). "
            "Сменить манеру можно только словами пользователя в паспорте, не в сцене") if wrong else ""


def prompt_defects(body: dict) -> list[str]:
    """Leftovers of cast pruning and re-timing that reach H3 as nonsense."""
    prompt = str(body.get("prompt") or "")
    count = len(body.get("asset_ids") or [])
    duration = (body.get("settings") or {}).get("duration")
    found = []
    if re.search(r";\s*;", prompt) or re.search(r"(?m)^\s*;", prompt):
        found.append("пустые «; ;» на месте убранных референсов")
    if re.search(r"\bfrom\s*[.;,]", prompt) or re.search(r"\band\s+(?:feline\s+)?body\s+from\s*[.;,]", prompt):
        found.append("оборванная ссылка на картинку («from .»)")
    for match in PICTURE_PROSE.finditer(prompt):
        if max(int(n) for n in match.groups() if n) > count:
            found.append(f"«{match.group(0)}» при {count} картинках задания")
            break
    for start, end in SHOT_TIME.findall(prompt):
        if float(start) != 0 or (duration and abs(float(end) - float(duration)) > 0.01):
            found.append(f"таймкод [{start}-{end}s] не совпадает с клипом 0-{duration}s")
            break
    return found


def node_executable() -> str | None:
    for candidate in (shutil.which("node"), str(Path.home() / ".local/bin/node"), "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def spec_snapshot(spec: Any) -> str:
    """JSON.stringify of the page: the staging a keyframe was drawn for."""
    return json.dumps(spec, ensure_ascii=False, separators=(",", ":"))


def link_mode(cut: dict) -> str:
    source = (cut.get("startFrame") or {}).get("source")
    return source if source in {"library", "previous", "keyframe"} else "none"


class SceneRuns:
    def __init__(self, root: Callable[[], Path]) -> None:
        self._root = root
        self._lock = asyncio.Lock()
        self.client: httpx.AsyncClient | None = None
        self.tasks: dict[str, asyncio.Task] = {}  # agent work of the runs: Codex drawing a frame, Claude checking one
        self.claude_limited_until = 0.0  # Claude's subscription limit: frames are not drawn for a check that cannot run

    # --- storage ---------------------------------------------------------------------------------------------
    def path(self, scene_id: str) -> Path:
        if not SCENE_ID.fullmatch(scene_id or ""):
            raise HTTPException(404, "Нет такой сцены")
        return self._root() / f"{scene_id}.json"

    def load(self, scene_id: str) -> dict | None:
        try:
            return json.loads(self.path(scene_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, run: dict) -> dict:
        run["updated_at"] = time.time()
        target = self.path(run["scene_id"])
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
        return run

    def all(self) -> list[dict]:
        root = self._root()
        # Only <scene_id>.json: a copy someone left beside them (x.backup.json) is not a run.
        runs = [self.load(path.stem) for path in sorted(root.glob("*.json")) if SCENE_ID.fullmatch(path.stem)] if root.is_dir() else []
        return [run for run in runs if run]

    # --- agent tasks (local_studio/scene_frames.py) -----------------------------------------------------------
    def _task_file(self, key: str) -> Path:
        return self._root() / "tasks" / f"{key}.json"

    def task_state(self, key: str) -> dict:
        try:
            record = json.loads(self._task_file(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {"state": "failed", "error": "задание агента потеряно"}
        if record.get("state") == "running" and key not in self.tasks:
            record = {"state": "failed", "error": INTERRUPTED}
        return record

    def _spawn(self, scene_id: str, key: str, work, apply=None) -> None:
        """Runs `work` beside the loop; `apply(run, record)` puts its result into the run, then the run goes on."""
        target = self._task_file(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"state": "running", "started": time.time()}), encoding="utf-8")

        async def runner() -> None:
            try:
                record = {"state": "completed", **(await work)}
            except Exception as exc:
                log.exception("scene run %s: %s", scene_id, key)
                record = {"state": "failed", "error": str(exc)[:500]}
            target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            self.tasks.pop(key, None)
            if apply:
                async with self._lock:
                    run = self.load(scene_id)
                    if run:
                        apply(run, record)
                        self.save(run)
            await self.advance(scene_id)

        self.tasks[key] = asyncio.create_task(runner())

    async def _pictures(self, ids: list[str], folder: Path) -> list[Path]:
        assert self.client is not None
        paths = []
        for at, asset_id in enumerate(ids, 1):
            response = await self.client.get(f"/api/assets/{asset_id}/file")
            if response.status_code >= 400:
                raise StepError(f"референс {asset_id} не найден в библиотеке")
            path = folder / f"image-{at:02d}.png"
            path.write_bytes(response.content)
            paths.append(path)
        return paths

    def _refs(self, run: dict, ids: list[str], roles: list[str] | None = None) -> list[dict]:
        known = {ref.get("id") or ref.get("asset_id"): ref for ref in run["references"]}
        return [{"role": (roles or [])[at] if roles and at < len(roles) else known.get(asset_id, {}).get("role", ""),
                 "owner": known.get(asset_id, {}).get("owner", "")} for at, asset_id in enumerate(ids)]

    async def _hero_looks(self, run: dict, cut: dict) -> str:
        franchise = str(run["options"].get("franchise_id") or "")
        cast = {str(name).casefold() for name in (cut.get("sceneShot") or {}).get("characters") or []}
        if run.get("kit") and cast:  # passport line + performance sheet (scale, gait, poses, makeup…)
            return scene_kit.hero_brief(run["kit"], sorted(cast))
        if not franchise or not cast or not SCENE_ID.fullmatch(franchise):
            return ""
        try:
            profile = await self._api("GET", f"/api/franchises/{franchise}/profile")
        except StepError:
            return ""
        return " ".join(role["prompt"].strip() for role in profile.get("roles", [])
                        if role.get("kind") == "character" and role.get("prompt")
                        and ({str(role.get("name", "")).casefold()} | {str(a).casefold() for a in role.get("aliases", [])}) & cast)

    async def _codex_frame(self, request: dict, references: list[dict], feedback: str) -> dict:
        assert self.client is not None
        with tempfile.TemporaryDirectory(prefix="h3-scene-frame-") as tmp:
            pictures = await self._pictures(request["asset_ids"], Path(tmp))
            data = await scene_frames.draw(request, references, pictures, feedback)
        response = await self.client.post("/api/assets", params={"role": "Кадр сцены"},
                                          files={"file": ("keyframe-codex.png", data, "image/png")})
        if response.status_code >= 400:
            raise StepError(f"кадр не сохранён в библиотеку: HTTP {response.status_code}")
        return {"asset_id": response.json()["asset_id"]}

    async def _claude_review(self, frame_id: str, text: str, ids: list[str], references: list[dict],
                             previous: dict | None = None) -> dict:
        """previous = {"asset_id", "text"}: the accepted start frame of the shot before it on screen, so the cut
        is checked too (JOURNAL 31.10: scene_frames.review(previous=…) existed but nothing passed it)."""
        with tempfile.TemporaryDirectory(prefix="h3-scene-review-") as tmp:
            before = [previous["asset_id"]] if previous and previous.get("asset_id") else []
            frame, *pictures = await self._pictures([frame_id, *ids, *before], Path(tmp))
            if before:
                return await scene_frames.review(frame, text, references, pictures[:-1],
                                                 previous={"frame": pictures[-1], "text": previous.get("text", "")})
            return await scene_frames.review(frame, text, references, pictures)

    @staticmethod
    def _previous_accepted(run: dict, number: int) -> dict | None:
        """The start frame of the shot before `number` when it is accepted for its current picture."""
        if number < 2:
            return None
        cut = run["cuts"][number - 2]
        asset = (cut.get("startFrame") or {}).get("asset_id")
        accepted = (cut.get("frameAcceptance") or {}).get("asset_id") == asset or (
            (cut.get("frameReview") or {}).get("asset_id") == asset and (cut.get("frameReview") or {}).get("state") == "accepted")
        if not asset or not accepted:
            return None
        return {"asset_id": asset, "text": str(cut.get("prompt") or "")}

    def _start_reviews(self, run: dict, awaiting: list[int]) -> bool:
        """Claude checks every start frame waiting for acceptance; true while a check runs."""
        if self._claude_off(run):  # nobody to check: the frames wait, an unchecked frame never goes to the GPU
            return True
        busy = False
        for number in awaiting:
            cut = run["cuts"][number - 1]
            asset = (cut.get("startFrame") or {}).get("asset_id")
            check = cut.get("frameReview") or {}
            if not asset or check.get("state") == "gave_up" and check.get("asset_id") == asset:
                continue
            if check.get("asset_id") == asset and check.get("state") == "running" and check.get("key") in self.tasks:
                busy = True
                continue
            key = f"review-{secrets.token_hex(6)}"
            ids = shot_pictures(run["references"], cut.get("sceneShot"))
            if len(ids) > 8 or any(not asset_id for asset_id in ids):
                cut["frameReview"] = {"asset_id": asset, "state": "gave_up", "problems": "Нельзя проверить полный состав референсов: исправь список изображений"}
                continue
            cut["frameReview"] = {"asset_id": asset, "state": "running", "key": key}
            snapshot = self._review_snapshot(run, cut)
            shot = cut.get("sceneShot") or {}
            brief = "\n".join(filter(None, [*take_review_rules(run.get("kit"), shot), str(cut.get("keyframeBrief") or ""),
                                                "Full shot purpose: " + str(shot.get("purpose") or ""),
                                                "Full shot action: " + str(cut.get("prompt") or "")]))
            self._spawn(run["scene_id"], key, self._claude_review(asset, brief, ids, self._refs(run, ids),
                                                                 self._previous_accepted(run, number)),
                        lambda run, record, cut_id=cut["id"], asset=asset, snapshot=snapshot:
                            self._apply_review(run, cut_id, asset, record, snapshot))
            busy = True
        return busy

    def _claude_off(self, run: dict) -> bool:
        """Claude's checks cannot run (its subscription limit): seen by a failed check or set in the run's options."""
        return bool(run["options"].get("autoAcceptTakes")) and time.time() < max(
            self.claude_limited_until, float(run["options"].get("claudeLimitedUntil") or 0))

    @staticmethod
    def _unaccepted_frames(run: dict) -> list[int]:
        """Shots whose drawn start frame has no acceptance for the current picture and reference bindings."""
        bindings = [[ref.get("id") or ref.get("asset_id") or "", ref.get("role") or "", ref.get("owner") or ""]
                    for ref in run["references"]]
        found = []
        for number, cut in enumerate(run["cuts"], 1):
            asset = (cut.get("startFrame") or {}).get("asset_id")
            accepted = cut.get("frameAcceptance") or {}
            check = cut.get("frameReview") or {}
            if check.get("asset_id") == asset and check.get("state") in {"gave_up", "failed", "stale", "stopped"}:
                continue  # a verdict that needs the user (or a new frame), not another automatic check
            if (cut.get("startFrame") or {}).get("source") == "keyframe" and asset and not cut.get("shotJobId") and (
                    accepted.get("asset_id") != asset or accepted.get("reference_bindings") != bindings):
                found.append(number)
        return found

    @staticmethod
    def _picture_only(run: dict, cut: dict) -> bool:
        """Turn a keyframe shot's compiled job into a picture-only one: no start frame, no guide frame."""
        template = run["templates"].get(cut["id"])
        token = f"studio-run:start:{cut['id']}"
        if not template or cut.get("shotJobId") or token not in template["body"].get("asset_ids", []):
            return False
        body = template["body"]
        removed = body["asset_ids"].index(token) + 1
        color = (body.get("settings") or {}).get("color_match")
        if isinstance(color, dict) and color.get("picture"):  # picture numbers after the start frame move down by one
            if color["picture"] == removed:
                body["settings"].pop("color_match")
            elif color["picture"] > removed:
                color["picture"] -= 1
        body["asset_ids"].remove(token)
        for field in ("reference_numbers", "reference_roles", "reference_owners"):
            (body.get(field) or {}).pop(token, None)
        body["reference_numbers"] = {asset: at for at, asset in enumerate(body["asset_ids"], 1)}
        body.setdefault("settings", {})["guide_frames"] = []
        template["mode"] = "none"
        for field in ("frameReview", "frameAcceptance", "keyframeFeedback", "keyframeBrief", "keyframePick"):
            cut.pop(field, None)
        cut.update(startFrame={"source": "none", "asset_id": None}, keyframeJobIds=[])
        return True

    def _lost_review(self, run: dict) -> bool:
        return any(run["options"].get(option) and (cut.get(field) or {}).get("state") == "running"
                   and cut[field].get("key") not in self.tasks
                   for cut in run["cuts"] for option, field in (("autoAccept", "frameReview"), ("autoAcceptTakes", "takeReview")))

    # --- finished takes: Claude in place of «Принять дубль» (options.autoAcceptTakes) -------------------------
    async def _measured_take(self, job_id: str) -> list[str]:
        """shot_contract.take_defects on the finished MP4 with the job's own pictures."""
        job = await self._api("GET", f"/api/jobs/{job_id}")
        manifest = json.loads(job.get("manifest_json") or "{}")
        images = manifest.get("images") or []
        with tempfile.TemporaryDirectory(prefix="h3-scene-measure-") as tmp:
            paths = await self._pictures([image["asset_id"] for image in images], Path(tmp))
            return await asyncio.to_thread(shot_contract.take_defects, job["output_path"],
                                           [(image.get("role") or "", str(path)) for image, path in zip(images, paths)],
                                           shot_contract.planned_cuts(job.get("compiled_prompt") or ""))

    async def _measure_only(self, job_id: str) -> dict:
        defects = await self._measured_take(job_id)
        if defects:
            return {"accept": False, "problems": "; ".join(defects), "at": time.time(), "by": "measure"}
        raise RuntimeError("не проверен: Claude на лимите")

    async def _claude_take(self, job_id: str, text: str, ids: list[str], references: list[dict]) -> dict:
        defects = await self._measured_take(job_id)
        if defects:  # measured, not judged: a cut or a pasted picture is a reject whatever a reviewer would say
            return {"accept": False, "problems": "; ".join(defects), "at": time.time(), "by": "measure"}
        # The take is judged against what the GPU was asked, not the draft: the draft still named heroes the launch
        # removed from the shot (scene_runs.drop_absent_heroes) and told the reviewer to expect them.
        compiled = str((await self._api("GET", f"/api/jobs/{job_id}")).get("compiled_prompt") or "")
        sent = parse_ir(compiled).get("detailed_description", "") if is_ir_prompt(compiled) else compiled
        if sent.strip():
            text = re.sub(r"(?s)\nAction: .*", "", text) + "\nAction, as sent to H3: " + sent.strip()
        frames = await self._api("GET", f"/api/jobs/{job_id}/frames?count=8")
        with tempfile.TemporaryDirectory(prefix="h3-scene-take-") as tmp:
            sheet = Path(tmp) / "sheet.jpg"
            sheet.write_bytes(base64.b64decode(frames["sheet_jpeg_base64"]))
            pictures = await self._pictures(ids, Path(tmp))
            technical = {**frames.get("technical", {}), "hard_cuts_s": frames.get("hard_cuts_s"),
                         "frame_times_s": frames.get("frame_times_s")}
            return await scene_frames.review_take(sheet, text, technical, references, pictures)

    def _start_take_review(self, run: dict, cut: dict) -> bool:
        """True while Claude checks the cut's take; the verdict lands in _apply_take."""
        job_id = cut.get("shotJobId")
        snapshot = self._review_snapshot(run, cut)
        if self._claude_off(run):  # an unchecked take never goes into the edit: it waits for the check
            if cut.get("takeMeasured") != job_id:  # the measurement needs no reviewer: a cut or a pasted picture is re-shot now
                cut["takeMeasured"] = job_id
                key = f"measure-{secrets.token_hex(6)}"
                cut["takeReview"] = {"job_id": job_id, "state": "running", "key": key}
                self._spawn(run["scene_id"], key, self._measure_only(job_id),
                            lambda run, record, cut_id=cut["id"], job_id=job_id, snapshot=snapshot, review_key=key: self._apply_take(run, cut_id, job_id, record, snapshot, review_key))
            run["message"] = "Claude на лимите — дубль ждёт проверки, в склейку не идёт"
            return True
        check = cut.get("takeReview") or {}
        if check.get("job_id") == job_id and check.get("state") == "running" and check.get("key") in self.tasks:
            return True
        from .franchise_profile import translit
        shot = cut.get("sceneShot") or {}
        cast = {translit(str(name)) for name in shot.get("characters") or [] if isinstance(name, str)}
        ids = [ref.get("id") or ref.get("asset_id") for ref in run["references"]
               if ref.get("kind") == "image" and ref.get("role") in HERO_ROLES and translit(str(ref.get("owner") or "")) in cast][:8]
        key = f"take-{secrets.token_hex(6)}"
        cut["takeReview"] = {"job_id": job_id, "state": "running", "key": key}
        brief = "\n".join(filter(None, [*take_review_rules(run.get("kit"), shot),
                                         "Purpose: " + str(shot.get("purpose") or ""),
                                         "Before: " + str(shot.get("state_before") or ""),
                                         "After: " + str(shot.get("state_after") or ""),
                                         "Action: " + str(cut.get("prompt") or "")]))
        self._spawn(run["scene_id"], key, self._claude_take(job_id, brief, ids, self._refs(run, ids)),
                    lambda run, record, cut_id=cut["id"], job_id=job_id, snapshot=snapshot, review_key=key: self._apply_take(run, cut_id, job_id, record, snapshot, review_key))
        return True

    def _apply_take(self, run: dict, cut_id: str, job_id: str, record: dict, snapshot: str | None = None, review_key: str | None = None) -> None:
        """Only a take Claude accepted goes into the edit. A rejected take is re-shot once, then the scene stops for
        the user; an unchecked take waits for the check (31.10: unchecked takes glued into scenes were all broken)."""
        cut = next((item for item in run["cuts"] if item.get("id") == cut_id), None)
        if not cut or cut.get("shotJobId") != job_id or run.get("state") not in {"awaiting", "running"}:
            return
        if review_key is not None and (cut.get("takeReview") or {}).get("key") != review_key:
            return  # A newer review or explicit manual decision superseded this callback.
        if snapshot is not None and snapshot != self._review_snapshot(run, cut):
            cut["takeReview"] = {"job_id": job_id, "state": "stale",
                                 "problems": "Постановка или референсы изменились во время проверки"}
            cut.pop("shotQc", None)
            cut.pop("shotQcReview", None)
            run.update(state="awaiting", qc_gate_cut_id=cut_id, message="Дубль требует проверки изменённой постановки")
            return
        at = run["cuts"].index(cut) + 1
        ok = record["state"] == "completed" and record.get("accept")
        problems = "" if ok else (record.get("problems") or record.get("error") or "не проверен")
        tries = int(cut.get("takeTries") or 1)
        if record["state"] == "completed" and not ok and tries < MAX_TAKE_TRIES:  # one re-shoot of a broken take
            self._record_production_review(job_id, "rejected", problems)
            cut["takeReview"] = {"job_id": job_id, "state": "rejected", "problems": problems}
            cut.update(shotJobId=None, selectedTakeId=None, shotQc=None, shotQcReview=None, takeTries=tries + 1)
            run.update(state="running", qc_gate_cut_id=None, awaiting=[],
                       message=f"Шот {at}: Claude вернул дубль на пересъёмку — {problems[:140]}")
            return
        if record["state"] != "completed":  # the check itself failed (limit, crash): check again later
            cut.pop("takeReview", None)
            if not str(record.get("error") or "").strip() or "limit" in str(record.get("error")).lower():
                self.claude_limited_until = time.time() + CLAUDE_LIMIT_WAIT_S
            run["message"] = f"Шот {at}: дубль не проверен ({str(record.get('error'))[:120]}) — жду проверки"
            return
        if record["state"] == "completed":
            self._record_production_review(job_id, "technical_pass" if ok else "rejected", problems)
        if not ok:
            cut["takeReview"] = {"job_id": job_id, "state": "rejected", "problems": problems}
            run.setdefault("flagged", []).append({"shot": at, "cut_id": cut_id, "job_id": job_id, "problems": problems[:500]})
            run.update(state="stopped", message=f"Шот {at}: брак после пересъёмки — нужен режиссёр: {problems[:160]}")
            return
        cut["takeReview"] = {"job_id": job_id, "state": "accepted", "problems": ""}
        cut.update(shotQc={"job_id": job_id, "decision": "accept", "by": "claude", "problems": ""}, shotQcReview="accepted")
        run.update(state="running", qc_gate_cut_id=None, awaiting=[], message=f"Шот {at}: Claude принял дубль")

    @staticmethod
    def _order(run: dict) -> tuple:
        return (run.get("options", {}).get("order", 999), run.get("created_at") or 0)

    async def _gpu_turn(self, run: dict) -> str | None:
        """The GPU never idles: a scene waits only while an earlier scene (options.order) has a take queued or
        rendering. Earlier scenes are advanced first in every pass, so they keep the GPU whenever they are ready.
        Returns the title of the scene whose take is on the GPU, or None when this scene may enqueue."""
        jobs = {job.get("job_id"): job.get("state") for job in await self._api("GET", "/api/jobs")}
        for other in sorted(self.all(), key=self._order):
            if other["scene_id"] == run["scene_id"]:
                return None
            if other.get("state") in {"running", "awaiting"} and any(
                    cut.get("shotJobId") and jobs.get(cut["shotJobId"]) not in FINISHED for cut in other["cuts"]):
                return other.get("title") or other["scene_id"]
        return None

    @staticmethod
    def _review_snapshot(run: dict, cut: dict) -> str:
        return json.dumps({"references": run["references"], "spec": cut.get("productionSpec"),
                           "prompt": cut.get("prompt"), "brief": cut.get("keyframeBrief")}, sort_keys=True, ensure_ascii=False)

    def _apply_review(self, run: dict, cut_id: str, asset: str, record: dict, snapshot: str | None = None) -> None:
        cut = next((item for item in run["cuts"] if item.get("id") == cut_id), None)
        if not cut or (cut.get("startFrame") or {}).get("asset_id") != asset:
            return  # the frame was replaced meanwhile
        if snapshot is not None and snapshot != self._review_snapshot(run, cut):
            cut["frameReview"] = {"asset_id": asset, "state": "stale", "problems": "Постановка или референсы изменились во время проверки"}
            cut.pop("frameAcceptance", None)
            return
        at = run["cuts"].index(cut) + 1
        if run.get("state") not in {"awaiting", "running"}:  # stopped by the user: keep the verdict, do not restart
            cut["frameReview"] = {"asset_id": asset, "state": "stopped", "problems": ""}
            return
        error = str(record.get("error") or "")
        if record["state"] != "completed" and error != INTERRUPTED and ("limit" in error.lower() or not error.strip()):
            self.claude_limited_until = time.time() + CLAUDE_LIMIT_WAIT_S  # an empty answer came with the limit too
        if record["state"] != "completed" and self._claude_off(run):  # checked again once the limit is over
            cut.pop("frameReview", None)
            run.update(state="awaiting", message=f"Шот {at}: Claude на лимите — кадр ждёт проверки")
            return
        if record["state"] != "completed":
            cut["frameReview"] = {"asset_id": asset, "state": "failed", "problems": record.get("error", "")}
            run.update(state="awaiting", message=f"Шот {at}: Claude не смог проверить кадр ({record.get('error', '')[:160]}) — прими его сам")
            return
        if record["accept"]:
            revision = ((cut.get("productionSpec") or {}).get("shot") or {}).get("revision")
            cut["frameAcceptance"] = {"asset_id": asset, "spec_revision": revision,
                                      "reference_ids": [ref.get("id") or ref.get("asset_id") for ref in run["references"]],
                                      "reference_bindings": [[ref.get("id") or ref.get("asset_id") or "", ref.get("role") or "", ref.get("owner") or ""] for ref in run["references"]],
                                      "keyframe_job_ids": [], "accepted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                      "accepted_by": "claude"}
            cut["frameReview"] = {"asset_id": asset, "state": "accepted", "problems": ""}
            run.update(state="running", message=f"Шот {at}: Claude принял кадр — продолжаю", awaiting=[])
            return
        tries = int(cut.get("frameTries") or 1)
        if tries >= MAX_FRAME_TRIES:
            cut["frameReview"] = {"asset_id": asset, "state": "gave_up", "problems": record["problems"]}
            run.update(state="awaiting", message=f"Шот {at}: Claude не принял {tries} кадра подряд ({record['problems'][:200]}) "
                                                  "— выбери или замени кадр сам")
            return
        cut["frameReview"] = {"asset_id": asset, "state": "rejected", "problems": record["problems"]}
        cut.update(keyframeFeedback=record["problems"], startFrame={"source": "keyframe", "asset_id": None},
                   frameTries=tries + 1)
        run.update(state="running", message=f"Шот {at}: Claude вернул кадр на перерисовку — {record['problems'][:160]}", awaiting=[])

    # --- the loop --------------------------------------------------------------------------------------------
    async def loop(self, app) -> None:
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", timeout=120)
        try:
            while True:
                try:
                    for run in sorted(self.all(), key=self._order):  # earlier scenes claim the GPU first
                        if run.get("state") == "awaiting" and self._lost_review(run):
                            run["state"] = "running"  # a check cut off by a restart starts again
                            self.save(run)
                        if run.get("state") == "running":
                            await self.advance(run["scene_id"])
                except Exception:  # one bad file or answer must not end every scene's build
                    log.exception("scene runs loop")
                await asyncio.sleep(POLL_S)
        finally:
            await self.client.aclose()

    async def advance(self, scene_id: str) -> dict | None:
        """One pass of the scene's chain: at most a few quick steps, then one that waits for the GPU or the user."""
        async with self._lock:
            run = self.load(scene_id)
            if not run or run.get("state") != "running":
                return run
            try:
                await self._advance(run)
            except Exception as exc:  # the tab stopped here too: the user sees why and presses «Продолжить»
                log.exception("scene run %s", scene_id)
                run.update(state="stopped", message=f"Сборка остановлена: {exc}")
            return self.save(run)

    def _record_production_review(self, job_id: str, verdict: str, problems: str) -> None:
        """Claude's take check becomes a production review (production_reviews/): review_gate counts the take as
        looked at, production_gates counts a rejection against the source. Best effort — never blocks the run."""
        async def record() -> None:
            try:
                job = await self._api("GET", f"/api/jobs/{job_id}")
                digest = job.get("output_sha256")
                if not digest:
                    return
                evidence = ("Claude, лист кадров дубля сцены: " + (problems or "принят без замечаний"))[:6000]
                await self._api("POST", f"/api/jobs/{job_id}/production-reviews",
                                {"output_sha256": digest, "verdict": verdict, "reviewer": "claude scene-run take check",
                                 "evidence": evidence})
            except Exception:  # noqa: BLE001 — the run must go on; the gate then asks a human to look
                pass
        try:
            asyncio.get_running_loop().create_task(record())
        except RuntimeError:
            pass

    async def _api(self, method: str, url: str, body: dict | None = None) -> Any:
        assert self.client is not None
        response = await self.client.request(method, url, json=body)
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise StepError(detail if isinstance(detail, str) else f"{url}: HTTP {response.status_code}")
        return payload

    async def _step(self, run: dict, jobs: list[dict]) -> dict:
        node = node_executable()
        if not node:
            raise StepError("Node.js не найден на этом Mac — сборка сцены не может идти без вкладки")
        options = dict(run["options"])
        options["maxImages"] = 8  # Codex sees every hero of the shot
        options["places"] = (run.get("kit") or {}).get("places", [])  # a keyframe gets only its own place
        payload = json.dumps({"cuts": run["cuts"], "jobs": jobs, "board": {**run["board"], "qcGate": True},
                              "references": run["references"], "options": options}, ensure_ascii=False)
        proc = await asyncio.create_subprocess_exec(node, str(STEP_SCRIPT), stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(payload.encode()), 60)
        if proc.returncode:
            raise StepError(f"шаг сцены не посчитан: {err.decode('utf-8', 'replace')[-400:]}")
        return json.loads(out)

    def _cut(self, run: dict, cut_id: str) -> dict:
        cut = next((item for item in run["cuts"] if item.get("id") == cut_id), None)
        if cut is None:
            raise StepError("шот не найден в сцене")
        return cut

    async def _advance(self, run: dict) -> None:
        if run["options"].get("autoAccept"):
            # Every drawn frame gets Claude's check at once, beside the drawing of later frames: an accepted shot
            # renders right away instead of waiting until the whole scene's keyframes exist (keyframesFirst).
            self._start_reviews(run, self._unaccepted_frames(run))
        ids = {cut.get("shotJobId") for cut in run["cuts"]} - {None}
        jobs = [job for job in await self._api("GET", "/api/jobs") if job.get("job_id") in ids]
        total = len(run["cuts"])
        for _ in range(6):
            step = await self._step(run, jobs)
            action, at = step["action"], step.get("index", 0)
            if action == "keyframe" and time.time() < run.get("codex_retry_at", 0):
                return  # waiting out the Codex limit; the message says until when
            if action == "keyframe":
                await self._order_keyframe(run, step)
                run["message"] = f"Шот {at + 1}: рисую опорный кадр…"
                return
            if action == "keyframe-wait":
                if await self._collect_keyframe(run, step):
                    continue
                if time.time() >= run.get("codex_retry_at", 0):
                    run["message"] = f"Собираю сцену: шот {at + 1} из {total} — готовится опорный кадр…"
                return
            if action == "extract":
                await self._last_frame(run, step["fromJobId"])
                continue
            if action == "wait":
                progress = step.get("progress")
                run["message"] = f"Собираю сцену: шот {at + 1} из {total} — генерируется" + ("…" if progress is None else f" {progress}%…")
                return
            if action == "enqueue":
                ahead = await self._gpu_turn(run)
                if ahead:
                    run["message"] = f"Шот {at + 1}: кадр готов, жду GPU — сначала доснимается «{ahead}»"
                    return
                job = await self._enqueue(run, step)
                jobs.append(job)
                run["message"] = f"Шот {at + 1} отправлен в очередь"
                return
            if action == "qc-wait":
                run.update(state="awaiting", awaiting=[], qc_gate_cut_id=step["cutId"], message=step["message"])
                if run["options"].get("autoAcceptTakes") and self._start_take_review(run, self._cut(run, step["cutId"])):
                    run["message"] = f"Claude проверяет дубль шота {at + 1}…"
                return
            if action == "done":
                await self._glue(run)
                run.update(state="done", message=step["message"])
                return
            run.update(state="awaiting" if step.get("awaiting") else "stopped", message=step["message"],
                       awaiting=step.get("awaiting") or [])
            if step.get("awaiting") and run["options"].get("autoAccept") and self._start_reviews(run, step["awaiting"]):
                run["message"] = f"Claude проверяет кадры шотов {', '.join(map(str, step['awaiting']))}…"
            return
        run["message"] = "Собираю сцену…"

    # --- steps (storyboard.mjs: orderKeyframe, collectKeyframe, extractLastFrame, enqueueShot, glue) ----------
    async def _order_keyframe(self, run: dict, step: dict) -> None:
        cut = self._cut(run, step["cutId"])
        medium = MEDIUM_FRAME.get(str((run.get("kit") or {}).get("mode") or ""))
        if medium and medium not in step["request"].get("prompt", ""):
            # The passport medium, whatever the scene style says or omits (a photoreal franchise must not turn cartoon).
            head = f"Medium: {medium}.\n"  # the shot text gives way: a keyframe prompt stays under 4000 characters
            step = {**step, "request": {**step["request"], "prompt": head + step["request"]["prompt"][:3800 - len(head)]}}
        # Codex draws it on the user's subscription; there is no GPU keyframe model.
        key = f"codex-{secrets.token_hex(6)}"
        request = step["request"]
        looks = await self._hero_looks(run, cut)
        if looks:  # the passport's words for each hero in view: Codex drew generic cats and wrong shirts
            request = {**request, "prompt": request["prompt"] + "\nHeroes in this frame, from the franchise passport: " + looks}
        self._spawn(run["scene_id"], key, self._codex_frame(request, self._refs(run, request["asset_ids"], request.get("roles")),
                                                             str(cut.get("keyframeFeedback") or "")))
        cut.update(keyframeJobId=None, keyframeJobIds=[key], keyframeVariants={key: "codex"}, keyframePick=None,
                   keyframeBrief=request["prompt"],  # what the frame must show; Claude checks against the same text
                   keyframeSpecSnapshot=spec_snapshot(cut.get("productionSpec")))

    async def _collect_keyframe(self, run: dict, step: dict) -> bool:
        cut = self._cut(run, step["cutId"])
        if all(key.startswith("codex-") for key in step["keyframeIds"]):
            jobs = [{"keyframe_id": key, **self.task_state(key)} for key in step["keyframeIds"]]
            failed = next((job for job in jobs if job["state"] == "failed"), None)
            if failed:
                tries = int(cut.get("frameTries") or 1)
                cut.update(keyframeJobIds=[])
                if failed.get("error") == INTERRUPTED:  # a Studio restart is not Codex's failed attempt
                    run["message"] = f"Шот {step['index'] + 1}: рисование прервано перезапуском — рисую заново"
                    return True
                if "лимит" in str(failed.get("error", "")):  # the subscription comes back by itself: wait, don't stop
                    run["codex_retry_at"] = time.time() + CODEX_LIMIT_WAIT_S
                    run["message"] = (f"Шот {step['index'] + 1}: у Codex закончился лимит — пробую снова в "
                                      f"{time.strftime('%H:%M', time.localtime(run['codex_retry_at']))}")
                    return False
                if tries >= MAX_FRAME_TRIES:
                    raise StepError(f"Codex не нарисовал кадр шота {step['index'] + 1} ({tries} попытки): {failed.get('error', '')[:200]}")
                cut["frameTries"] = tries + 1
                run["message"] = f"Шот {step['index'] + 1}: Codex не нарисовал кадр ({failed.get('error', '')[:120]}) — пробую ещё раз"
                return True
        else:  # a GPU keyframe ordered before 2026-11-03: nothing can finish it now
            cut.update(keyframeJobId=None, keyframeJobIds=[])
            raise StepError(f"Шот {step['index'] + 1}: кадр заказан на GPU, которого больше нет. "
                            "«Продолжить» закажет его у Codex.")
        if any(job.get("state") not in FINISHED for job in jobs):
            return False
        ready = [job["asset_id"] for job in jobs if job.get("state") == "completed" and job.get("asset_id")]
        if not ready:
            cut.update(keyframeJobId=None, keyframeJobIds=[])
            error = next((job["error"] for job in jobs if job.get("error")), "")
            raise StepError(f"опорный кадр шота {step['index'] + 1} не создан{': ' + str(error)[:200] if error else ''}. "
                            "Нажми «Продолжить» или загрузи кадр сам.")
        if cut.get("keyframeSpecSnapshot") and cut["keyframeSpecSnapshot"] != spec_snapshot(cut.get("productionSpec")):
            cut.update(keyframeJobId=None, keyframeJobIds=[], keyframeSpecSnapshot=None)
            raise StepError(f"Шот {step['index'] + 1}: опорный кадр создан по прежней постановке — он в библиотеке, "
                            "но не назначен. «Продолжить» закажет новый.")
        cut.update(startFrame={"source": "keyframe", "asset_id": ready[0]}, productionFrameChanged=False,
                   keyframeJobId=None, keyframeJobIds=[], keyframeVariants=None,
                   keyframePick={"candidates": ready, "chosen": ready[0], "reason": "", "fallback": False})
        return True

    async def _last_frame(self, run: dict, job_id: str) -> str:
        asset = await self._api("POST", f"/api/jobs/{job_id}/last-frame", {})
        run["board"].setdefault("lastFrames", {})[job_id] = asset["asset_id"]
        return asset["asset_id"]

    async def _resolve(self, run: dict, kind: str, cut_id: str, duration: int) -> str:
        cut = self._cut(run, cut_id)
        if kind == "start":
            asset = (cut.get("startFrame") or {}).get("asset_id")
            if asset:
                return asset
            if link_mode(cut) != "none" or not cut.get("shotJobId"):
                raise StepError("у шота ещё нет стартового кадра")
            kind = "last"  # a picture-only shot has no start frame: its rendered end shows the same place
        job_id = cut.get("shotJobId")
        if not job_id:
            raise StepError("предыдущий шот ещё не снят")
        if kind == "last":
            return run["board"].get("lastFrames", {}).get(job_id) or await self._last_frame(run, job_id)
        # Feed the actual end of the previous take to AddGuide, independent of the next shot's duration.
        video = await self._api("POST", f"/api/jobs/{job_id}/reference-video", {})
        info = await self._api("GET", f"/api/assets/{video['asset_id']}/excerpt-info")
        total = float(info["duration"])
        if total < CLIP_FRAMES / FPS:
            raise StepError(f"предыдущий шот слишком короткий для продолжения: нужно минимум {CLIP_FRAMES} кадров")
        excerpt = await self._api("POST", f"/api/assets/{video['asset_id']}/excerpt",
                                  {"start": max(0.0, total - TAIL_SECONDS), "duration": min(TAIL_SECONDS, total)})
        return excerpt["asset_id"]

    async def _enqueue(self, run: dict, step: dict) -> dict:
        cut = self._cut(run, step["cutId"])
        pending = run.get("pending_submission")
        if pending and not pending.get("request_id"):
            raise StepError("Предыдущая отправка шота требует сверки очереди; не повторяй её автоматически")
        if pending and pending.get("cut_id") != cut["id"]:
            raise StepError("Квитанция относится к другому шоту — требуется сверка")
        if pending:
            job = await self._api("GET", f"/api/job-receipts/{pending['request_id']}")
            cut.update(shotJobId=job["job_id"], selectedTakeId=job["job_id"], productionSpecChanged=False,
                       shotQc=None, shotQcReview=None, shotQcPending=False, shotQcError="")
            cut["takes"] = [*(cut.get("takes") or []), {"id": job["job_id"], "jobId": job["job_id"]}]
            if (step["plan"].get("startFrame") or {}).get("source") == "previous":
                cut["startFrame"] = step["plan"]["startFrame"]
            run.pop("pending_submission", None)
            self.save(run)
            return job
        label = f"Шот {step['index'] + 1}"
        template = run["templates"].get(cut["id"])
        if not template or template.get("mode") != link_mode(cut):
            raise StepError(f"{label} изменён после запуска — нажми «Собрать сцену» снова, задания пересоберутся")
        body = json.loads(json.dumps(template["body"], ensure_ascii=False))
        if int(cut.get("takeTries") or 1) > 1 and (body.get("settings") or {}).get("seed") not in (None, ""):
            # A re-shoot with the template's fixed seed renders the same video with the same defect: let H3 roll again.
            body["settings"]["seed"] = None
        duration = int(body.get("settings", {}).get("duration") or cut.get("duration") or 5)
        resolved: dict[str, str] = {}
        for token, kind, source in {match.groups() for match in TOKEN.finditer(json.dumps(body, ensure_ascii=False))}:
            resolved[token] = await self._resolve(run, kind, source, duration)
        drop_duplicate_pictures(body, resolved)
        drop_absent_heroes(body, cut.get("sceneShot"))
        shot_contract.normalize(body, cut.get("sceneShot"))
        broken = shot_contract.problems(body) + prompt_integrity_problems(body)
        if run.get("kit"):  # the take itself, with the run's own frames resolved, stays inside the scene kit
            owned = (set(resolved.values()) | {str((c.get("startFrame") or {}).get("asset_id") or "") for c in run["cuts"]}
                     | {str(r.get("id") or r.get("asset_id") or "") for r in run.get("references", []) if scene_kit.scene_made(r)})
            broken += scene_kit.body_findings(run["kit"], body, owned - {""})
        if broken:  # never spend the GPU on a prompt that asks for montage, pasted pictures or nothing happening
            raise StepError(f"{label}: " + "; ".join(broken))
        text = json.dumps(body, ensure_ascii=False)
        for token, asset in resolved.items():
            text = text.replace(f'"{token}"', json.dumps(asset))
        # Persist before the request: a lost response or process restart does not prove rejection.
        request_id = secrets.token_hex(16)
        run["pending_submission"] = {"cut_id": cut["id"], "started_at": time.time(), "request_id": request_id}
        self.save(run)
        try:
            job = await self._api("POST", "/api/jobs", {**json.loads(text), "client_request_id": request_id})
        except StepError:  # a definite 4xx answer: no job exists, keep its reason instead of a lost receipt
            run.pop("pending_submission", None)
            raise
        plan = step["plan"]
        cut.update(shotJobId=job["job_id"], selectedTakeId=job["job_id"], productionSpecChanged=False,
                   takes=[*(cut.get("takes") or ([{"id": cut["shotJobId"], "jobId": cut["shotJobId"]}] if cut.get("shotJobId") else [])),
                          {"id": job["job_id"], "jobId": job["job_id"], "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}],
                   shotQc=None, shotQcReview=None, shotQcPending=False, shotQcError="")
        if (plan.get("startFrame") or {}).get("source") == "previous":
            cut["startFrame"] = plan["startFrame"]
        run.pop("pending_submission", None)
        self.save(run)
        return job

    async def _glue(self, run: dict) -> None:
        for at, cut in enumerate(run["cuts"], 1):
            if not take_is_accepted(cut):
                raise StepError(f"Шот {at}: дубль не принят после QC; склейка остановлена")
        ids = [cut["shotJobId"] for cut in run["cuts"] if cut.get("shotJobId")]
        if len(ids) < 2:
            return
        run["board"]["lastConcat"] = await self._api("POST", "/api/storyboard/concat", {"job_ids": ids})


def public(run: dict) -> dict:
    """The run without the compiled jobs (those stay on the Mac)."""
    return {key: value for key, value in run.items() if key != "templates"}


def make_router(runs: SceneRuns) -> APIRouter:
    router = APIRouter(prefix="/api/scene-runs")

    @router.post("/compose")
    async def compose(payload: dict) -> dict:
        """Shot prompts written by the engine from the agent's fields (shot_contract.compose) and the franchise passport."""
        roles, passport = [], ""
        kit = None
        franchise = str(payload.get("franchise_id") or "")
        if franchise and SCENE_ID.fullmatch(franchise):
            profile = await runs._api("GET", f"/api/franchises/{franchise}/profile")
            roles, passport = profile.get("roles", []), json.dumps(profile, ensure_ascii=False)
            kit = await runs._api("GET", f"/api/franchises/{franchise}/scene-kit")
        prompts, problems, warnings, previous = [], [], [], None
        board = payload.get("board") if isinstance(payload.get("board"), dict) else {}
        world = board.get("fantasyWorld") if isinstance(board.get("fantasyWorld"), dict) else {}
        fantasy_world = str(payload.get("fantasy_world") or world.get("description") or "")
        for at, shot in enumerate(payload.get("shots") or [], 1):
            take = shot.get("take")
            if isinstance(take, dict):
                problems += [f"шот {at}: {problem}" for problem in
                             shot_contract.take_problems(take, kit, passport=passport, previous=previous)]
                warnings += [f"шот {at}: {problem}" for problem in shot_contract.camera_text_conflicts(take, shot)]
            previous = take if isinstance(take, dict) else None
            pictures = {str(ref.get("asset_id") or ref.get("id") or "") for ref in payload.get("references") or []}
            prompts.append(shot_contract.compose(take, shot, style=str(payload.get("style") or ""),
                                                 place=str(payload.get("place") or ""), roles=roles, pictures=pictures,
                                                 kit=kit, fantasy_world=fantasy_world)
                           if isinstance(take, dict) else str(shot.get("prompt") or ""))
            if isinstance(take, dict):
                warnings += [f"шот {at}: {problem}" for problem in shot_contract.prompt_warnings(prompts[-1])]
                problems += [f"шот {at}: {problem}" for problem in shot_contract.thin_description(prompts[-1])
                             + shot_contract.absent_hero_mentions(take, shot, kit)
                             + shot_contract.speech_problems(take, shot.get("duration"))]
                if kit and not (shot_contract.is_fantasy(take) or shot.get("fantasy") is True):
                    # JOURNAL 01.11: the passport breakdown (place without a picture, prop without its picture) used
                    # to reach only «Собрать сцену»; the agent sees it while it can still fix the draft.
                    from .scene_kit import breakdown_findings
                    warnings += [f"шот {at}: {problem}" for problem in
                                 breakdown_findings(kit, {"prompt": prompts[-1], "asset_ids": sorted(pictures)}, shot)]
        problems += shot_contract.repeated_sentences([shot.get("take") for shot in payload.get("shots") or []])
        problems += shot_contract.place_problems(str(payload.get("place") or ""), kit)
        problems += shot_contract.style_problems(str(payload.get("style") or ""), kit)
        return {"prompts": prompts, "problems": problems, "warnings": warnings}

    @router.post("/contract")
    async def contract(payload: dict) -> dict:
        """The take contract on a scene draft, for the agent's scene_draft before the user ever sees the card."""
        refs = [ref for ref in payload.get("references") or [] if ref.get("role") not in VIDEO_ROLES]
        ids = [str(ref.get("asset_id") or ref.get("id") or f"ref-{at}") for at, ref in enumerate(refs, 1)]
        found, passport, previous, profile = [], "", None, {}
        franchise = str(payload.get("franchise_id") or "")
        if franchise and SCENE_ID.fullmatch(franchise):
            profile = await runs._api("GET", f"/api/franchises/{franchise}/profile")
            passport = json.dumps(profile, ensure_ascii=False)
            wrong = medium_mismatch(profile, {"scene": {"body": {"settings": {"mode": payload.get("mode")}}}})
            if wrong:
                found.append(wrong.replace("Шоты scene", "Сцена"))
        for at, shot in enumerate(payload.get("shots") or [], 1):
            if franchise and not isinstance(shot.get("take"), dict):
                # A hand-written prompt («ALEX and NOVA use their assigned identity references») skips the heroes'
                # passports, and the card would pass with an empty review.
                found.append(f"шот {at}: в сцене франшизы промпт пишет движок из take (паспорт героев, живой мир) — "
                             "ручной prompt не принимается; заполни take")
            frame = ["start-frame"] if shot.get("start_frame") in {"new_keyframe", "previous_end"} else []
            body = {"prompt": str(shot.get("prompt") or ""), "asset_ids": ids + frame,
                    "reference_roles": {**{i: str(r.get("role") or "") for i, r in zip(ids, refs)},
                                        **({"start-frame": "Кадр сцены"} if frame else {})},
                    "reference_owners": {i: str(r.get("owner") or "") for i, r in zip(ids, refs) if r.get("owner")},
                    "reference_numbers": {i: n for n, i in enumerate(ids + frame, 1)},
                    "settings": {"duration": shot.get("duration")}}
            drop_absent_heroes(body, shot)
            shot_contract.normalize(body, shot)
            found += [f"шот {at}: {problem}" for problem in
                      shot_contract.problems(body) + shot_contract.staging_problems(body, passport, previous)]
            previous = body
        if franchise and profile:
            from .franchise_profile import Budget
            from .scene_kit import static_camera_problems
            budget = {**Budget().model_dump(), **(profile.get("budget") or {})}
            found += static_camera_problems(payload.get("shots") or [], budget)
        return {"ok": not found, "problems": found}

    def motion_path(scene_id: str) -> Path:
        return runs.path(scene_id).parent / "motion-plans" / f"{scene_id}.json"

    def read_motion_plan(scene_id: str) -> dict | None:
        path = motion_path(scene_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise HTTPException(500, "Не удалось прочитать сохранённый план Motion Director") from exc

    @router.get("/{scene_id}/motion-plan")
    async def get_motion_plan(scene_id: str) -> dict:
        plan = read_motion_plan(scene_id)
        if plan is None:
            raise HTTPException(404, "План Motion Director ещё не сохранён")
        return plan

    @router.put("/{scene_id}/motion-plan")
    async def save_motion_plan(scene_id: str, payload: SavedPlanInput) -> dict:
        try:
            fingerprints = payload.plan.fingerprints()
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        async with runs._lock:
            previous = read_motion_plan(scene_id) or {}
            if payload.expected_revision != previous.get("revision", 0):
                raise HTTPException(409, "План изменён другим агентом; прочитайте текущую версию")
            old = previous.get("segment_fingerprints", {})
            record = {"scene_id": scene_id, "revision": payload.expected_revision + 1,
                      "plan": payload.plan.model_dump(), "segment_fingerprints": fingerprints,
                      "invalidated_ids": [key for key, digest in fingerprints.items() if old.get(key) != digest],
                      "removed_ids": [key for key in old if key not in fingerprints],
                      "updated_at": time.time(), "submitted": False}
            target = motion_path(scene_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(target)
            return record

    @router.get("")
    async def list_runs() -> list[dict]:
        return [{key: run.get(key) for key in ("scene_id", "title", "state", "message", "awaiting", "updated_at")}
                for run in runs.all()]

    @router.get("/{scene_id}")
    async def get_run(scene_id: str) -> dict:
        run = runs.load(scene_id)
        if not run:
            raise HTTPException(404, "Сцена ещё не запускалась на Mac")
        return public(run)

    @router.post("/{scene_id}", status_code=201)
    async def start(scene_id: str, payload: RunInput) -> dict:
        """«Собрать сцену»: the scene's shots, settings and compiled jobs; the Mac takes it from here."""
        fantasy = [cut for cut in payload.cuts if cut.get("fantasy") is True]
        if fantasy:
            world = payload.board.get("fantasyWorld") or {}
            asset_id, ref_id = world.get("assetId"), world.get("refId")
            reference = next((ref for ref in payload.references if ref.get("id") == ref_id and
                              (ref.get("asset_id") or ref.get("id")) == asset_id), None)
            if not asset_id or not reference or any(ref_id in (cut.get("offRefs") or []) for cut in fantasy):
                raise HTTPException(422, "Шоты фантазии требуют готовый и привязанный «Мир фантазии» до сборки видео")
        franchise_id = str(payload.options.get("franchise_id") or "")
        if not franchise_id:  # 31.10: unchecked scenes mixed heroes, pictures and media of other franchises
            raise HTTPException(422, "Сцена без франшизы: комплект сцены (герои, картинки, манера) берётся только из её паспорта")
        if not SCENE_ID.fullmatch(franchise_id):
            raise HTTPException(422, "Некорректная франшиза сцены")
        profile = await runs._api("GET", f"/api/franchises/{franchise_id}/profile")
        wrong = medium_mismatch(profile, payload.templates)
        if wrong:
            raise HTTPException(422, wrong)
        kit = await runs._api("GET", f"/api/franchises/{franchise_id}/scene-kit")
        # A two-panel idea sheet («real | fantasy») as a reference makes H3 copy the split screen and its scale
        # (the hero vanishes, the room becomes the other panel). Fantasy goes through a camera move instead.
        for ref in payload.references:
            asset_id = ref.get("id") or ref.get("asset_id") or ""
            if ref.get("kind") == "image" and asset_id:
                response = await runs.client.get(f"/api/assets/{asset_id}/file")
                if response.status_code < 400 and await asyncio.to_thread(scene_frames.split_panel, response.content):
                    raise HTTPException(422, f"Референс «{ref.get('name') or asset_id}» — картинка из двух половин. "
                                             "Убери её из шотов: фантазия входит через движение камеры, а не через вторую панель")
        shots = {cut.get("id"): cut.get("sceneShot") for cut in payload.cuts}
        passport, previous = json.dumps(profile, ensure_ascii=False), None
        for cut_id, template in payload.templates.items():
            if not isinstance(template.get("body"), dict) or not template.get("mode"):
                raise HTTPException(422, f"Задание шота {cut_id} не собрано")
            drop_absent_heroes(template["body"], shots.get(cut_id))
            shot_contract.normalize(template["body"], shots.get(cut_id))
            broken = shot_contract.problems(template["body"]) + shot_contract.staging_problems(
                template["body"], passport, previous)
            previous = template["body"]
            if broken:  # the whole scene is checked before the first frame: no shot fails at night
                raise HTTPException(422, f"Шот {cut_id}: " + "; ".join(broken))
            defects = prompt_integrity_problems(template["body"])
            if defects:
                raise HTTPException(422, f"Шот {cut_id}: " + "; ".join(defects))
        outside = scene_kit.findings(kit, payload.references, payload.templates, payload.cuts,
                                     str(payload.options.get("sceneStyle") or ""))
        if outside:  # after normalize: Studio boilerplate is already gone, what repeats is the scene's own text
            raise HTTPException(422, "Сцена выходит за комплект франшизы: " + "; ".join(outside[:12]))
        async with runs._lock:
            previous = runs.load(scene_id) or {}
            if previous.get("pending_submission"):
                raise HTTPException(409, "Отправка шота требует сверки очереди; повторный запуск заблокирован")
            if previous.get("state") in {"running", "awaiting"}:
                raise HTTPException(409, "Сцена уже собирается или ждёт проверки кадра. Продолжи её очередь, не запускай заново")
            run = {"scene_id": scene_id, "title": payload.title, "cuts": payload.cuts,
                   "definition_snapshot": scene_definition(payload.cuts),
                   "board": {**payload.board, "running": True, "qcGate": True}, "references": payload.references,
                   "options": payload.options, "templates": payload.templates, "kit": kit, "state": "running", "awaiting": [],
                   "message": "Сцена передана Mac — собирается без вкладки", "created_at": previous.get("created_at", time.time())}
            if payload.source_context is not None:
                run["context_snapshot"] = context_definition(payload.source_context)
            runs.save(run)
        return public(await runs.advance(scene_id) or run)

    @router.post("/{scene_id}/stop")
    async def stop(scene_id: str) -> dict:
        async with runs._lock:
            run = runs.load(scene_id)
            if not run:
                raise HTTPException(404, "Сцена ещё не запускалась на Mac")
            run.update(state="stopped", message="Сборка остановлена. Уже отправленные шоты доделаются в очереди.")
            return public(runs.save(run))

    @router.post("/{scene_id}/resume")
    async def resume(scene_id: str) -> dict:
        async with runs._lock:
            run = runs.load(scene_id)
            if not run:
                raise HTTPException(404, "Сцена ещё не запускалась на Mac")
            if not run.get("kit"):
                raise HTTPException(409, "Старая сборка без комплекта франшизы: создай новую сцену из утверждённого сюжета; платные дубли сохранены")
            if run.get("pending_submission") and not run["pending_submission"].get("request_id"):
                raise HTTPException(409, "Отправка шота требует сверки очереди; продолжение заблокировано")
            run.update(state="running", message="Продолжаю сборку…", awaiting=[])
            runs.save(run)
        return public(await runs.advance(scene_id) or run)

    @router.patch("/{scene_id}/cuts/{cut_id}")
    async def patch_cut(scene_id: str, cut_id: str, change: dict) -> dict:
        """The user's word on a shot: «✓ Принять кадр», a replaced frame, a reviewed take. An accepted frame lets a
        scene that waited for it go on by itself."""
        unknown = set(change) - USER_FIELDS
        if unknown:
            raise HTTPException(422, f"Эти поля шота ведёт сама сборка: {', '.join(sorted(unknown))}")
        async with runs._lock:
            run = runs.load(scene_id)
            if not run:
                raise HTTPException(404, "Сцена ещё не запускалась на Mac")
            cut = next((item for item in run["cuts"] if item.get("id") == cut_id), None)
            if cut is None:
                raise HTTPException(404, "Нет такого шота в запущенной сцене")
            verdict = change.get("shotQcReview")
            if verdict in ("accepted", "rejected"):
                qc = change.get("shotQc") or (cut.get("shotQc") if verdict == "accepted" else {}) or {}
                if qc.get("job_id") != cut.get("shotJobId") or not cut.get("shotJobId"):
                    raise HTTPException(422, "Приёмка должна относиться к текущему дублю")
                if verdict == "rejected" or qc.get("decision") == "manual_review":
                    if cut.get("takeReview"):
                        cut.setdefault("takeReviewHistory", []).append(cut["takeReview"])
                    cut["takeReview"] = {"job_id": cut["shotJobId"], "state": verdict,
                                         "by": "user", "source": "reviewer_reported"}
            cut.update(change)
            if change.get("frameAcceptance"):
                cut["frameAcceptance"] = {**cut["frameAcceptance"],
                    "reference_ids": [ref.get("id") or ref.get("asset_id") for ref in run["references"]],
                    "reference_bindings": [[ref.get("id") or ref.get("asset_id") or "", ref.get("role") or "", ref.get("owner") or ""] for ref in run["references"]]}
            if change.get("frameAcceptance") and run.get("state") == "awaiting":
                run.update(state="running", message="Кадр принят — продолжаю сборку", awaiting=[])
            if change.get("shotQcReview") == "accepted" and run.get("qc_gate_cut_id") == cut_id:
                run.update(state="running", message="Дубль принят — продолжаю сборку", qc_gate_cut_id=None)
            if verdict == "rejected":
                run.update(state="stopped", qc_gate_cut_id=cut_id, awaiting=[],
                           message="Дубль отклонён пользователем — сборка остановлена")
            runs.save(run)
        return public(await runs.advance(scene_id) or run)

    @router.patch("/{scene_id}/options")
    async def set_options(scene_id: str, change: dict) -> dict:
        """keyframeEngine codex (the only one since 2026-11-03); autoAccept: Claude accepts the start frames for the user."""
        if set(change) - OPTION_FIELDS or change.get("keyframeEngine", "codex") != "codex":
            raise HTTPException(422, "Можно менять autoAccept; кадры рисует только Codex (keyframeEngine codex)")
        async with runs._lock:
            run = runs.load(scene_id)
            if not run:
                raise HTTPException(404, "Сцена ещё не запускалась на Mac")
            run["options"].update(change)
            if change.get("autoAccept") and run.get("state") == "awaiting":
                run["state"] = "running"
            runs.save(run)
        return public(await runs.advance(scene_id) or run)

    @router.delete("/{scene_id}")
    async def forget(scene_id: str) -> dict:
        """A deleted or reset scene: its run stops and is forgotten; jobs already in the queue finish."""
        async with runs._lock:
            runs.path(scene_id).unlink(missing_ok=True)
        return {"scene_id": scene_id, "deleted": True}

    return router
