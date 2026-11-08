"""«Комплект сцены»: the closed set one scene is built from — one franchise, its heroes, places, pictures and limits.

Why: without a closed set the franchise check runs only when a scene names its franchise; environment and idea
pictures go unchecked; a take is not re-checked before the GPU; one scene-wide sentence (a fantasy push into a hero's
eye) lands in every shot; most shots keep a static camera; a photoreal franchise drifts into 3D; heroes of one
franchise get named in another's prompt.

The kit is computed from the franchise passport (profile roles with performance sheets and budget) and the bible
cards of that franchise only. A picture is the kit's when the passport or a card of this franchise links it and no
card of another franchise does; a hero picture also needs its owner. Names of other franchises' heroes that this
franchise does not share are foreign: a prompt that names one is rejected. `findings` gates the scene launch,
`body_findings` every take right before /api/jobs. No GPU, no AI.
"""
from __future__ import annotations

import hashlib
import json
import re

from fastapi import APIRouter

from .franchise_profile import (HERO_ROLES, Profile, bible_roles, entity_cards, load_profile, names_of, translit)
from .franchises import FranchiseStore

RENDER_MODES = {"photoreal": "realistic", "animation3d": "animation3d", "animation2d": "animation2d"}
SHEET_ORDER = ("scale", "body", "gait", "posture", "poses", "face", "makeup", "costume", "voice", "never")
SCENE_ROLES = {"Кадр сцены"}  # frames the scene makes itself (Codex start frames, last frames)
PREVIS_ROLES = {"Композиция", "Камера"}  # Blockout staging of this scene (local_studio/previs.py): grey, no identity



def static_camera_problems(shots: list, budget: dict) -> list[str]:
    """«Собрать сцену» and the agent's scene_draft (/api/scene-runs/contract) judge the static share alike (JOURNAL 02.11)."""
    moves = [str((s or {}).get("camera_motion") or "") for s in shots if isinstance(s, dict)]
    static = sum(1 for m in moves if m in {"static", "locked", ""})
    limit = budget.get("max_static_share", 1)
    if len(moves) >= 3 and static / len(moves) > limit:
        return [f"камера стоит в {static} из {len(moves)} шотов (предел {limit:.0%}): "
                "у камеры нет повода двигаться — сцена выйдет статичной"]
    return []

def scene_made(ref: dict) -> bool:
    """A material the scene made itself: its frames, its previs (named previs-… by local_studio/previs.py) and its
    «Мир фантазии» (fantasy-world.png from story-canvas-worlds.mjs, drawn from the scene's own place)."""
    name = str(ref.get("name") or ref.get("original_name") or "")
    return (ref.get("role") in SCENE_ROLES or (ref.get("role") in PREVIS_ROLES and name.startswith("previs-"))
            or (ref.get("role") == "Окружение" and name.startswith("fantasy-world")))
# Signs of life a shot's prompt must carry (31.10: static shots, actors staring still, nothing moving around them).
LIFE = {
    "eyes": r"blink|glance|gaze|eyeline|eyes? (?:dart|flick|follow|track|narrow|widen|meet|shift|move|turn|search|lock|seek|catch|check|observe|find)"
            r"|looks? (?:at|up|down|away|back|over|around|into|toward|to)|into (?:the )?(?:lens|camera)|side-?eye|pupils?|stares?",
    "body": r"breath|breathes|inhal|exhal|chest (?:rises|falls|heaves)|weight shift|shifts? (?:his|her|its|their) weight|fidget"
            r"|ears? (?:twitch|flick|turn|swivel|flatten|perk)|tail (?:flick|twitch|swish|lash|curl)|whiskers?|shrug|swallow"
            r"|lean|tense|flinch|nod|tilts? (?:his|her|its|the) head|fingers? (?:tap|drum|curl|twitch)",
    "air": r"breeze|draft|draught|wind|air (?:moves|stirs|carries)|dust(?: motes)?|steam|smoke|haze|mist|vapou?r|curtains? (?:sway|stir|lift|move)"
           r"|hair (?:stirs|lifts|moves|sways|blows)|fur (?:ripples|lifts|stirs|bristles)|leaves? (?:rustle|stir|drift|flutter)|feathers?",
    "physics": r"wobbl|bounc|rolls?|sways?|swing|rattl|settles?|ripple|momentum|inertia|tips? over|topples?|slides?|skids?|recoil"
               r"|jiggl|creas|flaps?|drips?|splash|spill|thud|clatter|crumpl|sags?",
    "light": r"flicker|shadows? (?:move|shift|pass|stretch|sweep|slide|cross)|light (?:shifts|changes|pulses|sweeps|catches|flares|dims|brightens)"
             r"|(?:head|street|car|city|window) ?lights? (?:sweep|slide|cross|pass|move)|glint|gleam|reflection|refract|sun ?(?:beam|light) (?:moves|crosses|shifts)|neon (?:hum|buzz|pulse)|sparkl|caustic",
}
INTERACTION = r"at each other|each other|one another|between them|toward (?:him|her|them)|answers?|replies|responds?|reacts? to|hands? (?:him|her|it)|catches|meets? (?:his|her|their) (?:eyes|gaze)"
SENTENCE = re.compile(r"[^.!?\n]+[.!?]")


def _words(text: str) -> int:
    return len(re.findall(r"[\w'’-]+", text))


def _named(name: str, text: str) -> bool:
    """Case-sensitive: a hero is written NOVA / Nova; «bell», «brig» are ordinary words, not BELL or BRIG."""
    return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def sheet_text(name: str, sheet: dict) -> str:
    parts = [f"{key}: {str(sheet[key]).strip()}" for key in SHEET_ORDER if str(sheet.get(key) or "").strip()]
    return f"{name} — " + "; ".join(parts) + "." if parts else ""


def assemble(fid: str, name: str, profile: Profile, cards: list[dict], others: list[tuple[str, Profile, list[dict]]],
             assets: dict[str, dict]) -> dict:
    """The kit of one franchise. `others`: (franchise_id, profile, cards) of every other franchise."""
    roles = bible_roles(profile, cards, assets)
    cast, places, props = [], [], []
    allowed: dict[str, dict] = {}
    for role in roles:
        aliases = sorted({role.name, *role.aliases} | ({translit(role.name).upper()} if role.kind == "character" else set()))
        for item in role.assets:
            owner = role.owner or (role.name if role.kind in {"character", "costume"} else "")
            entry = allowed.setdefault(item.asset_id, {"roles": [], "owners": []})
            if item.role and item.role not in entry["roles"]:
                entry["roles"].append(item.role)
            if owner and owner not in entry["owners"]:
                entry["owners"].append(owner)
        if role.kind == "character":
            cast.append({"name": role.name, "aliases": aliases, "line": role.prompt,
                         "sheet": role.sheet.model_dump(), "assets": [a.asset_id for a in role.assets]})
        elif role.kind in {"location", "fantasy"}:
            places.append({"name": role.name, "aliases": aliases, "line": role.prompt,
                           "assets": [a.asset_id for a in role.assets]})
        elif role.kind in {"prop", "weapon", "vehicle"}:
            props.append({"name": role.name, "aliases": aliases, "assets": [a.asset_id for a in role.assets]})
    for card in cards:  # pictures of any card of this franchise (scenes, ideas, rules) — owner-less
        for asset_id in card.get("asset_ids", []):
            allowed.setdefault(asset_id, {"roles": [], "owners": []})
        if card.get("type") == "character":
            heroes = {translit(n) for n in names_of(str(card.get("title", "")))}
            for asset_id in card.get("asset_ids", []):
                owners = allowed[asset_id]["owners"]
                for hero in cast:
                    if {translit(a) for a in hero["aliases"]} & heroes and hero["name"] not in owners:
                        owners.append(hero["name"])
    own_names = {translit(a) for hero in cast for a in hero["aliases"]}
    foreign: dict[str, str] = {}
    shared: set[str] = set()
    for other_id, other, other_cards in others:
        linked = {a.asset_id for r in other.roles for a in r.assets} | {a for c in other_cards for a in c.get("asset_ids", [])}
        own_explicit = {a.asset_id for r in profile.roles for a in r.assets}
        shared |= (linked & set(allowed)) - own_explicit
        names = {n for r in other.roles if r.kind == "character" for n in [r.name, *r.aliases]}
        names |= {n for c in other_cards if c.get("type") == "character" for n in names_of(str(c.get("title", "")))}
        for hero in names:
            hero = hero.strip()
            if len(hero) >= 3 and hero[0].isupper() and translit(hero) not in own_names:
                foreign.setdefault(hero, other_id)
    for asset_id in shared:  # a picture two franchises link is nobody's: it goes back to «На разбор»
        allowed.pop(asset_id, None)
    rendering = str((profile.director_defaults.get("style") or {}).get("rendering") or "")
    kit = {"franchise_id": fid, "franchise": name, "mode": RENDER_MODES.get(rendering, ""),
           "style": profile.look.style, "aspect_ratio": profile.look.aspect_ratio, "language": profile.look.language,
           "cast": cast, "places": places, "props": props, "allowed": allowed, "foreign": dict(sorted(foreign.items())),
           "shared_excluded": sorted(shared), "budget": profile.budget.model_dump(), "life": profile.life.model_dump()}
    kit["fingerprint"] = hashlib.sha256(json.dumps(kit, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return kit


def world_brief(kit: dict) -> str:
    """The franchise's living world (air, physics, light, background, eyes) as one English line for prompts and checks."""
    life = kit.get("life") or {}
    parts = [f"{key}: {str(life[key]).strip()}" for key in ("air", "physics", "light", "background", "eyes") if str(life.get(key) or "").strip()]
    return ("Living world — " + "; ".join(parts) + ".") if parts else ""


def hero_brief(kit: dict, names: list[str]) -> str:
    """Passport line + performance sheet of the heroes in a shot, for the Codex frame brief and Claude's checks."""
    wanted = {translit(str(n)) for n in names}
    lines = []
    for hero in kit.get("cast", []):
        if wanted & {translit(a) for a in hero["aliases"]}:
            lines += [hero["line"].strip(), sheet_text(hero["name"], hero["sheet"])]
    return " ".join(line for line in [*lines, world_brief(kit)] if line)


LOOK_KEYS = ("scale", "body", "makeup", "costume")      # who the hero is: subject_definitions
PLAY_KEYS = ("gait", "posture", "poses", "face")       # how the hero moves: the acting of [Shot 1]
WORLD_KEYS = ("air", "physics", "light", "background")


def _hero(kit: dict, name: str) -> dict | None:
    key = translit(str(name).strip())
    return next((h for h in kit.get("cast", []) if key in {translit(a) for a in h["aliases"]}), None)


def sheet_words(kit: dict, name: str, keys: tuple[str, ...]) -> str:
    """Part of a hero's performance sheet as one sentence, «NOVA — scale: …; gait: ….» (the kit's own words, so the
    repeat check of `findings` never takes them for scene-wide text)."""
    hero = _hero(kit, name)
    return sheet_text(hero["name"], {k: hero["sheet"].get(k, "") for k in keys}) if hero else ""


def take_life(kit: dict, take: dict) -> tuple[list[str], list[str]]:
    """The life of one take from the writer's fields → (English sentences for [Shot 1], problems).

    take.cast[]: eyes (where the hero looks, blinks, whom they check), answers (how they answer the other hero);
    take.world: {air, physics, light, background} — what moves on its own around the heroes during this take.
    One take = its own events («the tube rolls and wobbles against the leg»), not the passport's general world line.
    """
    sentences, problems = [], []
    cast = [m for m in take.get("cast") or [] if str(m.get("name") or "").strip()]
    for member in cast:
        name = member["name"].strip()
        eyes = str(member.get("eyes") or "").strip()
        if eyes:
            sentences.append(eyes if re.match(rf"(?i)({re.escape(name)}|he|she|they|his|her|its)\b", eyes) else f"{name}'s eyes: {eyes}")
        else:
            problems.append(f"{name}: нет поля eyes — куда смотрит, когда моргает, на кого и когда в камеру")
    answers = [str(m.get("answers") or "").strip() for m in cast if str(m.get("answers") or "").strip()]
    if len(cast) >= 2 and not answers:
        problems.append("герои в кадре не отвечают друг другу: заполни answers хотя бы у одного")
    sentences += answers
    world = take.get("world") or {}
    filled = [k for k in WORLD_KEYS if str(world.get(k) or "").strip()]
    sentences += [str(world[k]).strip() for k in filled]
    if len(filled) < 2:
        problems.append("мир дубля не живёт: в world нужно минимум два из air, physics, light, background — события этого дубля")
    text = " ".join(s if s.endswith((".", "!", "?")) else s + "." for s in sentences)
    missing = life_findings(kit, text + " " + " ".join(str(m.get(k) or "") for m in cast for k in ("notices", "does", "ends")))
    problems += [f"по тексту: {p}" for p in missing if not problems]
    return [s if s.endswith((".", "!", "?")) else s + "." for s in sentences], problems


def foreign_names(kit: dict, text: str) -> list[str]:
    return sorted({f"{name}" for name in kit.get("foreign", {}) if _named(name, text)})


def life_layers(text: str) -> set[str]:
    return {layer for layer, pattern in LIFE.items() if re.search(pattern, text, re.IGNORECASE)}


def life_findings(kit: dict, prompt: str) -> list[str]:
    """A living shot: eyes always alive, heroes in view answer each other, and the world around moves on its own."""
    life = kit.get("life")
    need = int((life or {}).get("min_layers", 0))
    if need <= 0:
        return []
    layers = life_layers(prompt)
    heroes = {hero["name"] for hero in kit.get("cast", []) if any(_named(a, prompt) for a in hero["aliases"])}
    if len(heroes) >= 2 and re.search(INTERACTION, prompt, re.IGNORECASE):
        layers.add("interaction")
    found = []
    if "eyes" not in layers:
        found.append("глаза героев не живут: нет морганий, взглядов друг на друга, по сторонам или в камеру")
    if len(heroes) >= 2 and "interaction" not in layers:
        found.append(f"{' и '.join(sorted(heroes))} в кадре не взаимодействуют: никто не отвечает другому взглядом или действием")
    if len(layers) < need:
        missing = [name for name in ("eyes", "body", "air", "physics", "light", "interaction") if name not in layers]
        found.append(f"мир шота мёртвый: признаков жизни {len(layers)} из нужных {need} (нет: {', '.join(missing)})")
    return found


def body_findings(kit: dict, body: dict, scene_owned: set[str] = frozenset()) -> list[str]:
    """One take's job right before /api/jobs: every picture from the kit (or made by this scene), the kit's owners,
    medium, size of the prompt, no hero of another franchise."""
    found = []
    allowed = kit.get("allowed", {})
    owners = body.get("reference_owners") or {}
    roles = body.get("reference_roles") or {}
    ids = [str(a) for a in body.get("asset_ids") or []]
    for asset_id in ids:
        if asset_id in scene_owned or asset_id.startswith("studio-run:"):
            continue
        entry = allowed.get(asset_id)
        if entry is None:
            found.append(f"картинка {asset_id[:8]} не из комплекта франшизы {kit.get('franchise')}"
                         + (" (её связывает и другая франшиза)" if asset_id in kit.get("shared_excluded", []) else ""))
            continue
        owner = str(owners.get(asset_id) or "").strip()
        if roles.get(asset_id) in HERO_ROLES:
            if not owner or translit(owner) not in {translit(o) for o in entry["owners"]}:
                found.append(f"фото героя {asset_id[:8]} принадлежит {', '.join(entry['owners']) or 'никому'}, "
                             f"а не «{owner or '—'}»")
    budget = kit.get("budget", {})
    if len(ids) > budget.get("max_images", 9):
        found.append(f"{len(ids)} картинок при пределе {budget.get('max_images')}")
    # What the store will actually render: a missing field is not «no opinion» but its default (9:16, «replace»).
    from app.contract import DEFAULTS
    mode = (body.get("settings") or {}).get("mode") or DEFAULTS["mode"]
    if kit.get("mode") and mode != kit["mode"]:
        found.append(f"режим «{mode}», паспорт франшизы — «{kit['mode']}»")
    aspect = (body.get("settings") or {}).get("aspect_ratio") or "9:16"  # store.py: effective.get("aspect_ratio", "9:16")
    if kit.get("aspect_ratio") and aspect != kit["aspect_ratio"]:
        # a 16:9 take in a 9:16 franchise is thrown away whole
        found.append(f"кадр {aspect}, паспорт франшизы — {kit['aspect_ratio']}")
    prompt = str(body.get("prompt") or "")
    words = _words(prompt)
    if words < budget.get("min_prompt_words", 0):
        found.append(f"промпт {words} слов — меньше {budget['min_prompt_words']}: шот не описан")
    if words > budget.get("max_prompt_words", 10 ** 6):
        found.append(f"промпт {words} слов — больше {budget['max_prompt_words']}: действие тонет в общем тексте")
    duration = (body.get("settings") or {}).get("duration") or 5  # store.py: int(effective.get("duration", 5))
    if not budget.get("min_take_s", 0) <= float(duration) <= budget.get("max_take_s", 60):
        found.append(f"дубль {duration} с вне {budget['min_take_s']:g}–{budget['max_take_s']:g} с")
    alien = foreign_names(kit, prompt)
    if alien:
        found.append("герои другой франшизы в промпте: " + ", ".join(alien))
    return found + place_findings(kit, body) + life_findings(kit, prompt)


ENVIRONMENT_LINE = re.compile(r"is the environment:\s*([^\n.:;]*)", re.I)


def shot_place(prompt: str, location: str = "") -> str:
    """Where the take happens: the shot's location, else the head of its «is the environment:» line."""
    match = ENVIRONMENT_LINE.search(prompt or "")
    return " ".join(part for part in (location.strip(), match.group(1).strip() if match else "") if part)


def place_findings(kit: dict, body: dict, location: str = "") -> list[str]:
    """31.10 «White Planet»: the apartment picture went into balcony shots and H3 drew the living room. A location
    picture of a passport place goes only into a take whose place is that place."""
    where = shot_place(str(body.get("prompt") or ""), location)
    if not where:
        return []
    roles = body.get("reference_roles") or {}
    found = []
    for asset_id in [str(a) for a in body.get("asset_ids") or []]:
        if roles.get(asset_id) != "Окружение":
            continue
        own = [p for p in kit.get("places", []) if asset_id in p.get("assets", [])]
        if own and not any(_says(where, alias) for p in own for alias in [p["name"], *p.get("aliases", [])]):
            found.append(f"картинка места «{own[0]['name']}» в шоте, где место — «{where}»: "
                         "выключи её для шота или добавь в паспорт картинку этого места")
    return found


def _scene_sentences(prompt: str, kit: dict, scene_style: str = "") -> set[str]:
    """Sentences of a prompt that a scene could copy into every shot: not picture bindings, not the kit's own lines
    (style, passport lines, performance sheets), not the scene's one style line."""
    own = " ".join([kit.get("style", ""), scene_style, world_brief(kit)] + [h["line"] for h in kit.get("cast", [])]
                   + [sheet_text(h["name"], h["sheet"]) for h in kit.get("cast", [])] + [p["line"] for p in kit.get("places", [])])
    own = " ".join(own.split()).casefold()
    sheets = tuple(f"{h['name'].casefold()} — " for h in kit.get("cast", []))  # sheet_words sentences of the kit
    kept = set()
    # A subject definition line is a binding, whole: the scene place goes into every take's environment subject
    # by design (shot_contract.compose), and the style opening is Studio's, not the scene's (02.11, idea 38).
    lines = [line for line in prompt.splitlines() if "<subject" not in line.casefold() and "<picture" not in line.casefold()]
    for sentence in SENTENCE.findall("\n".join(lines)):
        text = " ".join(sentence.split()).casefold()
        if _words(text) < 8 or text.startswith(("scene style", "the target video is in", *sheets)) or text in own \
                or own and text.lstrip("[").split("]")[-1].strip()[:40] in own:
            continue
        kept.add(text)
    return kept


def findings(kit: dict, references: list[dict], templates: dict[str, dict], cuts: list[dict],
             scene_style: str = "") -> list[str]:
    """The whole scene before its first frame: references, every shot's job, cast and camera of the shot plan."""
    found = []
    if not kit.get("cast"):
        found.append(f"в паспорте {kit.get('franchise')} нет ни одного героя — комплект пуст")
    allowed = kit.get("allowed", {})
    for ref in references:
        asset_id = str(ref.get("id") or ref.get("asset_id") or "")
        if not asset_id or scene_made(ref):
            continue
        if asset_id not in allowed:
            found.append(f"референс «{ref.get('name') or asset_id[:8]}» не привязан к паспорту и карточкам {kit.get('franchise')}")
        elif ref.get("role") in HERO_ROLES:
            owner = str(ref.get("owner") or "")
            if translit(owner) not in {translit(o) for o in allowed[asset_id]["owners"]}:
                found.append(f"фото героя {asset_id} не привязано к {owner or 'владельцу'} в паспорте франшизы")
    names = {translit(a) for hero in kit.get("cast", []) for a in hero["aliases"]}
    shots = [cut.get("sceneShot") or {} for cut in cuts]
    shots = [json.loads(s) if isinstance(s, str) and s.strip().startswith("{") else s for s in shots]
    for at, shot in enumerate(shots, 1):
        for hero in (shot or {}).get("characters") or []:
            if isinstance(hero, str) and translit(hero.strip()) not in names:
                found.append(f"шот {at}: «{hero}» нет среди героев паспорта {kit.get('franchise')}")
    budget = kit.get("budget", {})
    found += static_camera_problems(shots, budget)
    owned = ({str((cut.get("startFrame") or {}).get("asset_id") or "") for cut in cuts}
             | {str(ref.get("id") or ref.get("asset_id") or "") for ref in references if scene_made(ref)}) - {""}
    counts: dict[str, int] = {}
    for cut_id, template in templates.items():
        body = template.get("body") or {}
        found += [f"шот {cut_id}: {problem}" for problem in body_findings(kit, body, owned)]
        for sentence in _scene_sentences(str(body.get("prompt") or ""), kit, scene_style):
            counts[sentence] = counts.get(sentence, 0) + 1
    if len(templates) >= 3:
        limit = budget.get("max_shared_action", 1) * len(templates)
        for sentence, count in counts.items():
            if count > limit:
                found.append(f"одна фраза в {count} шотах из {len(templates)} — общий текст сцены вместо действия "
                             f"шота: «{sentence[:90]}…». Приметы героя — в его лист в паспорте, остальное — в нужный шот")
    shot_of = {cut.get("id"): (shot if isinstance(shot, dict) else {}) for cut, shot in zip(cuts, shots)}
    for cut_id, template in templates.items():
        found += [f"шот {cut_id}: {problem}" for problem in breakdown_findings(kit, template.get("body") or {},
                                                                               shot_of.get(cut_id) or {})]
    return found


def _says(text: str, alias: str) -> bool:
    return bool(alias) and re.search(rf"(?<![\w-]){re.escape(alias)}(?![\w-])", text, re.I) is not None


def breakdown_findings(kit: dict, body: dict, shot: dict | None = None) -> list[str]:
    """Breakdown of one shot against the passport, before anything is drawn (ScriptBreak's element breakdown, plus
    the asset check it lacks): the place the shot happens in is a passport place with a picture — otherwise H3 invents
    it anew in every shot (31.10 «White Planet»: a balcony with no picture) — and a passport prop the shot names
    travels with its picture."""
    prompt = str(body.get("prompt") or "")
    ids = {str(a) for a in body.get("asset_ids") or []}
    found = []
    where = shot_place(prompt, str((shot or {}).get("location") or ""))
    if where and kit.get("places") is not None:
        here = [p for p in kit["places"] if any(_says(where, a) for a in [p["name"], *p.get("aliases", [])])]
        if not here:
            found.append(f"место «{where}» не описано в паспорте {kit.get('franchise')}: без картинки H3 рисует его "
                         "заново в каждом шоте — добавь в паспорт место с картинкой")
        elif not any(p.get("assets") for p in here):
            found.append(f"у места «{here[0]['name']}» в паспорте нет картинки — добавь её, чтобы место не менялось от шота к шоту")
    detail = ENVIRONMENT_LINE.sub("", prompt)
    for prop in kit.get("props", []):
        if prop.get("assets") and not ids & set(prop["assets"]) and any(
                _says(detail, a) for a in [prop["name"], *prop.get("aliases", [])]):
            found.append(f"предмет «{prop['name']}» есть в шоте, а его картинки нет — он выйдет другим, чем в паспорте")
    return found


def get_scene_kit(store, fid: str) -> dict:
    data = FranchiseStore(store.root)
    profile, _ = load_profile(data, fid)
    cards = entity_cards(data, fid)
    with data.db() as db:
        rows = db.execute("SELECT id, name FROM franchises").fetchall()
    name = next((row["name"] for row in rows if row["id"] == fid), fid)
    others = [(row["id"], load_profile(data, row["id"])[0], entity_cards(data, row["id"])) for row in rows if row["id"] != fid]
    ids = {a.asset_id for r in profile.roles for a in r.assets} | {a for c in cards for a in c.get("asset_ids", [])}
    assets = {i: a for i in ids if (a := store.get_asset(i))}
    return assemble(fid, name, profile, cards, others, assets)


def make_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/franchises/{fid}")

    @router.get("/scene-kit")
    def scene_kit(fid: str) -> dict:
        return get_scene_kit(store, fid)

    return router
