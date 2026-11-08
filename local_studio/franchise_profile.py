"""«Паспорт франшизы»: the franchise owns its look, camera and edit rules and its asset roles.

A story's shot list names heroes, costumes, props and places; `resolve_shots` turns every shot into the
parts of a Studio job: asset_ids with reference roles and owners (face, full body, costume, weapon,
location, style…), the franchise look lines for the prompt and a director_config built from the
franchise defaults plus the shot's own camera choices. Characters and locations of the franchise
bible (entities with asset_ids) resolve on their own; profile roles add aliases, costumes, props,
fantasies and rules. The same profile adds franchise checks to shot_lint and defaults to the edit sheet.
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.contract import ASPECT_PATTERN
from app.director_config import FIELD_RULES, normalize_director_config
from app.shot_lint import ShotList, lint_shots
from .edl import ShotsToEdl, TRANSITIONS, edl_from_shots
from .franchises import FranchiseStore
from .store import REFERENCE_ROLE_PROMPTS

MAX_IMAGES = 9  # H3 reference pictures per job
HERO_ROLES = {"Лицо героя", "Персонаж целиком", "Костюм"}
KINDS = ("character", "costume", "weapon", "prop", "location", "fantasy", "style", "light", "color", "voice", "music")
DEFAULT_ROLE = {"character": "Персонаж целиком", "costume": "Костюм", "weapon": "Предмет", "prop": "Предмет",
                "location": "Окружение", "fantasy": "Окружение", "style": "Стиль", "light": "Свет", "color": "Цвет",
                "voice": "Голос", "music": "Музыка"}
SHOT_KEYS = set(FIELD_RULES.get("shot", {}))
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


class RoleAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str = Field(min_length=1, max_length=120)
    role: str = ""  # REFERENCE_ROLE_PROMPTS key; empty = the role's default for its kind

    @field_validator("role")
    @classmethod
    def known_role(cls, value: str) -> str:
        if value and value not in REFERENCE_ROLE_PROMPTS:
            raise ValueError(f"Неизвестная роль референса «{value}»")
        return value


class Sheet(BaseModel):
    """A hero's performance sheet (English): what every shot of every scene keeps, whoever writes the shot.
    Studio puts it into the scene kit, the Codex frame brief and Claude's frame/take checks."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scale: str = Field(default="", max_length=400)     # size against the other heroes and the room
    body: str = Field(default="", max_length=400)      # build, proportions, species anatomy
    gait: str = Field(default="", max_length=400)      # how the hero walks, runs, lands
    posture: str = Field(default="", max_length=400)   # resting stance, weight, where the tension sits
    poses: str = Field(default="", max_length=400)     # signature poses and gestures
    face: str = Field(default="", max_length=400)      # face, eyes, brows, how emotion reads
    makeup: str = Field(default="", max_length=400)    # makeup, hair, fur markings, accessories
    costume: str = Field(default="", max_length=400)   # what is always worn unless a costume role replaces it
    voice: str = Field(default="", max_length=400)
    never: str = Field(default="", max_length=400)     # what this hero never does or looks like


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern=f"^({'|'.join(KINDS)})$")
    aliases: list[str] = Field(default_factory=list, max_length=12)
    owner: str = Field(default="", max_length=80)       # hero a costume, weapon or voice belongs to
    with_owner: bool = False  # attach whenever the owner is in the shot (signature costume)
    always: bool = False      # attach to every shot (franchise style plate)
    entity_id: str = Field(default="", max_length=120)
    assets: list[RoleAsset] = Field(default_factory=list, max_length=8)
    prompt: str = Field(default="", max_length=600)  # English line added to the prompt when the role is in a shot
    sheet: Sheet = Field(default_factory=Sheet)      # characters: performance sheet of the scene kit


class Look(BaseModel):
    model_config = ConfigDict(extra="forbid")
    style: str = Field(default="", max_length=1500)    # English, opens every prompt's look
    texture: str = Field(default="", max_length=600)
    palette: str = Field(default="", max_length=600)
    language: str = Field(default="English", max_length=40)
    aspect_ratio: str = Field(default="9:16", pattern=ASPECT_PATTERN)  # STUDIO franchises: vertical by default


class CameraRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lens_min: int | None = Field(default=None, ge=8, le=600)
    lens_max: int | None = Field(default=None, ge=8, le=600)
    moves: list[str] = Field(default_factory=list, max_length=30)  # allowed shot.movement values, empty = any
    dutch: bool = False
    signature: str = Field(default="", max_length=600)


class EditRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transitions: list[str] = Field(default_factory=list, max_length=11)  # allowed shot.transition_in, empty = any
    signature_transition: str = ""
    min_shot: float = Field(default=1.0, ge=0.2, le=60)
    max_shot: float = Field(default=15.0, ge=0.5, le=60)
    music_asset_id: str = ""
    music_volume: float = Field(default=0.35, ge=0, le=2)
    fade_in: float = Field(default=0, ge=0, le=3)
    fade_out: float = Field(default=0.5, ge=0, le=5)
    look: str = ""                 # local_studio/grade.py LOOKS: the franchise colour on every edit
    look_intensity: float = Field(default=0.6, ge=0, le=1)
    match_color: bool = True       # H3 shots drift in colour; pull them to the first shot

    @field_validator("look")
    @classmethod
    def known_look(cls, value: str) -> str:
        from .grade import LOOKS
        if value and value not in LOOKS:
            raise ValueError(f"Нет лука «{value}». Есть: {', '.join(LOOKS)}")
        return value

    @field_validator("transitions")
    @classmethod
    def known_transitions(cls, values: list[str]) -> list[str]:
        unknown = set(values) - set(TRANSITIONS)
        if unknown:
            raise ValueError(f"Неизвестные переходы: {', '.join(sorted(unknown))}")
        return values

    @field_validator("signature_transition")
    @classmethod
    def known_signature(cls, value: str) -> str:
        if value and value not in TRANSITIONS:
            raise ValueError(f"Неизвестный переход «{value}»")
        return value


class Budget(BaseModel):
    """Limits every scene take of the franchise must fit (scene_kit.py checks them at launch and before the GPU)."""
    model_config = ConfigDict(extra="forbid")
    min_take_s: float = Field(default=10, ge=2, le=30)        # 4–6 s takes came back as montage (31.10)
    max_take_s: float = Field(default=15, ge=2, le=30)
    min_prompt_words: int = Field(default=300, ge=0, le=3000)
    max_prompt_words: int = Field(default=1100, ge=50, le=3000)
    max_images: int = Field(default=9, ge=1, le=9)
    max_static_share: float = Field(default=0.4, ge=0, le=1)  # 59% static shots on 31.10: nothing happened
    max_shared_action: float = Field(default=0.5, ge=0, le=1)  # one action sentence in more shots = scene-wide text


class Life(BaseModel):
    """The living world of every shot of the franchise (user 31.10: «все сцены — живые: от воздуха до физики и света,
    глаза актёров моргают, смотрят по сторонам, в камеру, друг на друга; всё окружение всегда живое»).
    English lines go into the scene kit; scene_kit.life_findings refuses a take whose prompt has no such life."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    air: str = Field(default="", max_length=400)         # what moves in the air: dust, steam, curtains, hair, fur, leaves
    physics: str = Field(default="", max_length=400)     # weight, inertia, cloth, props that wobble, roll and settle
    light: str = Field(default="", max_length=400)       # light that changes: flicker, passing shadows, glints
    background: str = Field(default="", max_length=400)  # what lives behind the heroes: street, neighbours, birds
    eyes: str = Field(default="", max_length=400)         # blinks, glances, eyelines, a look into the lens
    min_layers: int = Field(default=4, ge=0, le=6)       # of eyes, body, air, physics, light, interaction


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    look: Look = Field(default_factory=Look)
    director_defaults: dict[str, dict] = Field(default_factory=dict)  # {category: {param: value}}
    camera: CameraRules = Field(default_factory=CameraRules)
    edit: EditRules = Field(default_factory=EditRules)
    roles: list[Role] = Field(default_factory=list, max_length=120)
    notes: str = Field(default="", max_length=4000)
    budget: Budget = Field(default_factory=Budget)
    life: Life = Field(default_factory=Life)

    @field_validator("director_defaults")
    @classmethod
    def valid_defaults(cls, value: dict) -> dict:
        normalize_director_config(director_config(value))
        return value


class ProfileUpdate(Profile):
    revision: int = Field(ge=0, strict=True)


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    shots: list[dict] = Field(min_length=1, max_length=60)


class FranchiseShotList(ShotList):
    model_config = ConfigDict(extra="ignore")


def director_config(defaults: dict, shot: dict | None = None) -> dict:
    """{category: {param: value}} (+ the shot's own shot.* choices) → Studio director_config."""
    merged = {category: dict(params) for category, params in defaults.items() if isinstance(params, dict)}
    for key in SHOT_KEYS & set(shot or {}):
        value = shot[key]
        if value not in ("", None):
            merged.setdefault("shot", {})[key] = value
    return {category: {"id": f"{category}.franchise", "parameters": params}
            for category, params in merged.items() if params}


def translit(text: str) -> str:
    return "".join(TRANSLIT.get(c, c) for c in text.lower())


# Group labels in front of a card's name: «S1 · Анна Ли · Дом Ли», «УЛИЦА · Макс», «ХОЗЯИН · Вера».
# Read as the name, they would merge many characters into three heroes «S1», «УЛИЦА», «ХОЗЯИН».
GROUP_LABEL = re.compile(r"(?i)^(?:S\d+|E\d+|S\d+E\d+|УЛИЦА|ХОЗЯИН|ХОЗЯЕВА|ГОРОД|ЭПИЗОД|СЕЗОН \d+)$")
# Location cards: «Сезон 1 · L02 · Ночная закусочная», «ПРОЕКТ МИРА · W01 · Квартира: дом…», «Референсы для генерации ·
# Улицы» would make places named «L02», «ПРОЕКТ МИРА», «Референсы для генерации»: headings and codes are not names.
PLACE_LABEL = re.compile(r"(?i)^(?:[LW]\d+|ПРОЕКТ МИРА|РЕФЕРЕНСЫ ДЛЯ ГЕНЕРАЦИИ)$")
PLACE_CODE = re.compile(r"(?i)^[LW]\d+$")
REJECTED = re.compile(r"(?i)^\s*(?:ОТКЛОНЕНО|REJECTED)\b")
ON_REVIEW = re.compile(r"(?i)^\s*НА ПЕРЕРАБОТКУ\b")
# «КАНДИДАТ НА КРОССОВЕР, НЕ УТВЕРЖДЁННЫЙ УЧАСТНИК» would put another franchise's hero into this cast.
CANDIDATE = re.compile(r"(?i)^\s*(?:КАНДИДАТ|CANDIDATE)\b")


def names_of(title: str, place: bool = False) -> list[str]:
    """«НОВА · эталон внешности» → ['НОВА', 'nova']; «S1 · Анна Ли · Дом» → ['Анна Ли', …].
    `place`: a location card — headings and codes go too («Сезон 1 · L02 · Ночная закусочная» → ['Ночная закусочная',
    …, 'L02']), and so does the tail after a colon («Квартира: дом обоих героев» → 'Квартира'); the code stays an alias."""
    parts = [part.strip() for part in re.split(r"\s+[·—|-]\s+|\s*\(", title)]
    codes = []
    while len(parts) > 1 and (GROUP_LABEL.match(parts[0]) or (place and PLACE_LABEL.match(parts[0]))):
        head = parts.pop(0)
        if place and PLACE_CODE.match(head):
            codes.append(head.upper())
    head = parts[0] if parts else ""
    if place:
        head = head.split(":", 1)[0].strip() or head
    latin = translit(head)
    return [head] + ([latin] if latin != head.lower() else []) + codes


def mentioned(name: str, text: str) -> bool:
    return bool(name) and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def shot_text(shot: dict) -> str:
    parts = []
    for key, value in shot.items():
        if key in {"job_id", "asset_id", "asset_ids"}:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if isinstance(item, str))
    return "\n".join(parts)


def bible_roles(profile: Profile, entities: list[dict], assets: dict[str, dict]) -> list[Role]:
    """Profile roles, then bible characters/locations with pictures that no role already covers."""
    roles = list(profile.roles)
    covered = {role.entity_id for role in roles if role.entity_id}
    taken = {translit(name) for role in roles for name in [role.name, *role.aliases]}
    for entity in entities:
        if entity.get("type") not in {"character", "location"} or entity["id"] in covered or not entity.get("asset_ids"):
            continue
        if REJECTED.match(entity.get("title", "")) or ON_REVIEW.match(entity.get("body") or "") \
                or CANDIDATE.match(entity.get("body") or ""):
            continue  # a rejected design, a role sent back for rework or a crossover candidate is no hero
        names = names_of(entity.get("title", ""), place=entity.get("type") == "location")
        if not names[0] or translit(names[0]) in taken:
            continue
        taken.add(translit(names[0]))
        kind = entity["type"]
        role_assets = []
        for asset_id in entity["asset_ids"][:8]:
            own = (assets.get(asset_id) or {}).get("role", "")
            usable = own if own in REFERENCE_ROLE_PROMPTS and REFERENCE_ROLE_PROMPTS[own][0] == "image" else ""
            if kind == "location" and usable in HERO_ROLES:
                usable = ""
            role_assets.append(RoleAsset(asset_id=asset_id, role=usable))
        roles.append(Role(name=names[0], kind=kind, aliases=names[1:], entity_id=entity["id"], assets=role_assets))
    return roles


def resolve_shot(profile: Profile, roles: list[Role], assets: dict[str, dict], shot: dict) -> dict:
    text = shot_text(shot)
    present = {role.name for role in roles if any(mentioned(n, text) for n in [role.name, *role.aliases])}
    present_names = {translit(name) for role in roles if role.name in present for name in [role.name, *role.aliases]}
    chosen = [role for role in roles if role.name in present or role.always or
              (role.with_owner and translit(role.owner) in present_names)]
    # A costume replaces its owner's full-body picture: two outfits on one body (full-body
    # picture + costume picture) confuse H3. The face stays.
    costumed = {translit(role.owner) for role in chosen if role.kind == "costume" and role.owner}
    asset_ids, reference_roles, reference_owners, used, notes = [], {}, {}, [], []
    images = 0
    order = {"character": 0, "costume": 1, "weapon": 2, "location": 3, "fantasy": 4, "prop": 5}
    for role in sorted(chosen, key=lambda r: order.get(r.kind, 9)):
        for item in role.assets:
            asset = assets.get(item.asset_id)
            if not asset:
                notes.append(f"{role.name}: файл {item.asset_id[:8]} не найден в библиотеке")
                continue
            ref_role = item.role or DEFAULT_ROLE[role.kind]
            if REFERENCE_ROLE_PROMPTS[ref_role][0] != asset.get("kind"):
                notes.append(f"{role.name}: роль «{ref_role}» не подходит файлу {asset.get('kind')}")
                continue
            if (ref_role == "Персонаж целиком" and role.kind == "character"
                    and any(translit(n) in costumed for n in [role.name, *role.aliases])):
                notes.append(f"{role.name}: полный рост не приложен — в шоте костюм, два наряда на одном теле конфликтуют")
                continue
            if item.asset_id in reference_roles:
                notes.append(f"{role.name}: файл уже в шоте как «{reference_roles[item.asset_id]}»")
                continue
            if asset.get("kind") == "image":
                if images >= MAX_IMAGES:
                    notes.append(f"{role.name}: больше {MAX_IMAGES} картинок в шоте — файл не добавлен")
                    continue
                images += 1
            asset_ids.append(item.asset_id)
            reference_roles[item.asset_id] = ref_role
            owner = role.name if role.kind == "character" else role.owner
            if owner and ref_role in HERO_ROLES | {"Голос", "Актёрская игра"}:
                reference_owners[item.asset_id] = owner
        used.append({"name": role.name, "kind": role.kind, "from": "profile" if role in profile.roles else "bible"})
    names = [n for n in shot.get("characters", []) if isinstance(n, str)]
    missing = [n for n in names if not any(mentioned(alias, n) or mentioned(n, alias)
                                           for role in roles for alias in [role.name, *role.aliases])]
    look = profile.look
    lines = [line for line in (look.style, look.texture and f"Texture: {look.texture}",
                               look.palette and f"Palette: {look.palette}",
                               profile.camera.signature and f"Camera: {profile.camera.signature}") if line]
    lines += [role.prompt for role in chosen if role.prompt]
    config = director_config(profile.director_defaults, shot)
    try:
        config = normalize_director_config(config)
    except ValueError as exc:
        notes.append(f"Режиссура шота: {exc}")
        config = normalize_director_config(director_config(profile.director_defaults))
    return {"asset_ids": asset_ids, "reference_roles": reference_roles, "reference_owners": reference_owners,
            "roles": used, "missing": missing, "look": lines, "director_config": config, "notes": notes}


def profile_findings(profile: Profile, shots: list[dict]) -> list[dict]:
    """Franchise rules on top of the cinematographer's check."""
    out = []
    camera, edit = profile.camera, profile.edit

    def add(severity, rule, index, message, fix=""):
        out.append({"severity": severity, "rule": f"franchise_{rule}", "shot": index + 1, "message": message,
                    **({"fix": fix} if fix else {})})

    for index, shot in enumerate(shots):
        lens = shot.get("lens_mm")
        if isinstance(lens, (int, float)):
            if camera.lens_min and lens < camera.lens_min or camera.lens_max and lens > camera.lens_max:
                add("warning", "lens", index, f"Объектив {lens} мм вне линейки франшизы "
                    f"({camera.lens_min or '…'}–{camera.lens_max or '…'} мм)")
        move = shot.get("movement") or ""
        if camera.moves and move and move not in camera.moves and move != "static":
            add("warning", "move", index, f"Движение «{move}» не из языка камеры франшизы",
                f"Разрешено: {', '.join(camera.moves)}")
        if shot.get("angle") == "dutch" and not camera.dutch:
            add("warning", "dutch", index, "Заваленный горизонт не в стиле франшизы")
        transition = shot.get("transition_in") or ""
        if index and edit.transitions and transition and transition not in edit.transitions:
            add("warning", "transition", index, f"Переход «{transition}» не из монтажной палитры франшизы",
                f"Палитра: {', '.join(edit.transitions)}")
        duration = shot.get("duration")
        # A shot of the plan is one H3 take (10–15 s, its native length); the passport's min/max_shot is the cut rhythm
        # of the montage inside and between takes. Checked against the take, a 6 s rhythm sent the agent back to 5 s
        # takes, which H3 fills with montage (31.10: 63 of 82 takes broken).
        if isinstance(duration, (int, float)) and not 10 <= duration <= 15:
            add("warning", "take_length", index, f"{duration:g} с — дубль H3 делается 10–15 с",
                f"Ритм склеек франшизы {edit.min_shot:g}–{edit.max_shot:g} с — для монтажа, не для длины дубля")
    return out


def load_profile(data: FranchiseStore, fid: str) -> tuple[Profile, int]:
    with data.db() as db:
        if not db.execute("SELECT 1 FROM franchises WHERE id=?", (fid,)).fetchone():
            raise HTTPException(404, "Франшиза не найдена")
        row = db.execute("SELECT content, revision FROM profiles WHERE franchise_id=?", (fid,)).fetchone()
    return (Profile.model_validate_json(row["content"]), row["revision"]) if row else (Profile(), 0)


def entity_cards(data: FranchiseStore, fid: str) -> list[dict]:
    """Bible cards of one franchise with their revision."""
    with data.db() as db:
        return [{"id": row["id"], "revision": row["revision"], **json.loads(row["content"])} for row in db.execute(
            "SELECT id, content, revision FROM entities WHERE franchise_id=? ORDER BY rowid", (fid,))]


def make_router(store) -> APIRouter:
    data = FranchiseStore(store.root)
    with data.db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS profiles (franchise_id TEXT PRIMARY KEY REFERENCES franchises(id),
            content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, previous TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    router = APIRouter(prefix="/api/franchises/{fid}")

    def load(fid: str) -> tuple[Profile, int]:
        return load_profile(data, fid)

    def entities(fid: str) -> list[dict]:
        return entity_cards(data, fid)

    def library(ids: set[str]) -> dict[str, dict]:
        return {asset_id: asset for asset_id in ids if (asset := store.get_asset(asset_id))}

    @router.get("/profile")
    def get_profile(fid: str) -> dict:
        profile, revision = load(fid)
        return {"revision": revision, **profile.model_dump()}

    @router.put("/profile")
    def put_profile(fid: str, payload: ProfileUpdate) -> dict:
        load(fid)
        content = payload.model_dump_json(exclude={"revision"})
        with data.db() as db:
            if payload.revision == 0 and not db.execute("SELECT 1 FROM profiles WHERE franchise_id=?", (fid,)).fetchone():
                db.execute("INSERT INTO profiles(franchise_id, content, revision) VALUES (?,?,1)", (fid, content))
            elif not db.execute("""UPDATE profiles SET previous=content, content=?, revision=revision+1,
                    updated_at=CURRENT_TIMESTAMP WHERE franchise_id=? AND revision=?""",
                                (content, fid, payload.revision)).rowcount:
                raise HTTPException(409, "Паспорт франшизы изменён в другом месте. Перечитай его и повтори правку.")
        return get_profile(fid)

    @router.post("/resolve-shots")
    def resolve(fid: str, payload: ResolveInput) -> dict:
        profile, _ = load(fid)
        cards = entities(fid)
        ids = {a.asset_id for r in profile.roles for a in r.assets} | {a for e in cards for a in e.get("asset_ids", [])}
        assets = library(ids)
        roles = bible_roles(profile, cards, assets)
        return {"aspect_ratio": profile.look.aspect_ratio, "language": profile.look.language,
                "shots": [resolve_shot(profile, roles, assets, shot) for shot in payload.shots]}

    @router.post("/shot-lint")
    def franchise_lint(fid: str, payload: FranchiseShotList) -> dict:
        profile, _ = load(fid)
        result = lint_shots(payload)
        extra = profile_findings(profile, [shot.model_dump() for shot in payload.shots])
        findings = result["findings"] + extra
        order = {"error": 0, "warning": 1, "tip": 2}
        findings.sort(key=lambda f: (order[f["severity"]], f["shot"]))
        counts = {k: sum(1 for f in findings if f["severity"] == k) for k in order}
        return {**result, "ok": counts["error"] == 0, "counts": counts, "findings": findings}

    @router.post("/edl-from-shots")
    def franchise_edl(fid: str, payload: ShotsToEdl) -> dict:
        profile, _ = load(fid)
        edit = profile.edit
        defaults = {"aspect_ratio": profile.look.aspect_ratio}
        if edit.music_asset_id and not payload.music_asset_id:
            defaults["music_asset_id"] = edit.music_asset_id
        sheet = edl_from_shots(payload.model_copy(update=defaults))
        sheet["fade_in"], sheet["fade_out"] = edit.fade_in, edit.fade_out
        if edit.look:
            sheet["look"], sheet["look_intensity"] = edit.look, edit.look_intensity
        if edit.match_color and len(sheet["clips"]) > 1:
            sheet["match_color"] = True
        if "music" in sheet:
            sheet["music"]["volume"] = edit.music_volume
        return sheet

    return router
