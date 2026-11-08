"""Validated Studio Director choices carried through the Mac and GPU API.

These values are H3 visual instructions. Only camera_state/camera_optics are
separate native graph inputs; a camera brand here does not emulate hardware.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("director_schema.json")
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _field_options(field: dict) -> list[dict]:
    if isinstance(field.get("options"), list):
        return field["options"]
    ref = field.get("options_ref")
    if isinstance(ref, str) and ref.count(".") == 1:
        category, key = ref.split(".")
        target = next((item for item in SCHEMA["categories"].get(category, []) if item["key"] == key), None)
        if target and isinstance(target.get("options"), list):
            return target["options"]
    raise ValueError(f"director_schema.json: неверный options_ref для {field.get('key')}")


def _rule(field: dict):
    kind = field["type"]
    if kind == "enum":
        return {option["value"] for option in _field_options(field)}
    if kind == "boolean":
        return bool
    if kind == "number":
        return (field["min"], field["max"])
    if kind == "palette_tags":
        return "palette"
    if kind in {"swatches", "short_text"}:
        return kind
    raise ValueError(f"director_schema.json: неизвестный тип поля {kind}")


# Fields marked "prompt": false control graph nodes, not the H3 text direction.
NON_PROMPT_FIELDS = {
    (category, field["key"]) for category, fields in SCHEMA["categories"].items()
    for field in fields if field.get("prompt") is False
}

# Built from director_schema.json — edit fields there, not here.
FIELD_RULES = {
    category: {field["key"]: _rule(field) for field in fields}
    for category, fields in SCHEMA["categories"].items()
}
_ID = re.compile(r"^(?:[a-z]+\.[a-z0-9_]+|custom\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_SHORT_TEXT = re.compile(SCHEMA["short_text_pattern"])
_PALETTE_VALUES = set(SCHEMA["palette_tags"])


def normalize_director_config(value: dict | None) -> dict:
    """Reject unknown keys and invalid values before recording or submitting a job."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(FIELD_RULES):
        raise ValueError("director_config: неизвестная категория")
    result = {}
    for category, entry in value.items():
        if not isinstance(entry, dict) or set(entry) != {"id", "parameters"}:
            raise ValueError(f"director_config.{category}: ожидаются id и parameters")
        preset_id, params = entry["id"], entry["parameters"]
        if not isinstance(preset_id, str) or not _ID.fullmatch(preset_id) or (
            not preset_id.startswith((f"{category}.", "custom."))
        ):
            raise ValueError(f"director_config.{category}: неверный id")
        if not isinstance(params, dict) or set(params) - set(FIELD_RULES[category]):
            raise ValueError(f"director_config.{category}: неизвестный параметр")
        cleaned = {}
        for name, raw in params.items():
            rule = FIELD_RULES[category][name]
            if rule is bool:
                if type(raw) is not bool:
                    raise ValueError(f"director_config.{category}.{name}: нужен boolean")
                cleaned[name] = raw
            elif rule == "swatches":
                if not isinstance(raw, list) or len(raw) > 8 or any(
                    not isinstance(color, str) or not _HEX.fullmatch(color) for color in raw
                ):
                    raise ValueError("director_config.color.swatches: ожидается до 8 цветов #RRGGBB")
                cleaned[name] = [color.lower() for color in raw]
            elif rule == "palette":
                if (not isinstance(raw, list) or len(raw) > 4 or
                        any(type(color) is not str or color not in _PALETTE_VALUES for color in raw)):
                    raise ValueError("director_config.style.palette: неизвестный цветовой принцип")
                cleaned[name] = raw
            elif rule == "short_text":
                if type(raw) is not str or not _SHORT_TEXT.fullmatch(raw) or raw != raw.strip():
                    raise ValueError(f"director_config.{category}.{name}: краткое описание до 60 символов")
                cleaned[name] = raw
            elif isinstance(rule, tuple):
                if type(raw) not in (float, int) or not math.isfinite(raw) or not rule[0] <= raw <= rule[1]:
                    raise ValueError(f"director_config.{category}.{name}: вне допустимого диапазона")
                cleaned[name] = raw
            elif type(raw) is not str or raw not in rule:
                raise ValueError(f"director_config.{category}.{name}: неизвестное значение")
            else:
                cleaned[name] = raw
        result[category] = {"id": preset_id, "parameters": cleaned}
    if len(json.dumps(result, ensure_ascii=False)) > 4096:
        raise ValueError("director_config слишком велик")
    return result


def director_config_direction(config: dict) -> str:
    """Authoritative, bounded direction appended after Qwen planning."""
    config = normalize_director_config(config)
    if not config:
        return ""
    lines = []
    for category in FIELD_RULES:
        if category not in config:
            continue
        params = {name: value for name, value in config[category]["parameters"].items()
                  if (category, name) not in NON_PROMPT_FIELDS}
        if params:
            lines.append(f"{category.upper()}: " + "; ".join(
                f"{name.replace('_', ' ')}={json.dumps(value, ensure_ascii=False)}"
                for name, value in params.items()
            ))
        if category == "movement":
            move_id = config[category]["parameters"].get("camera_move")
            move_field = next((field for field in SCHEMA["categories"][category]
                               if field["key"] == "camera_move"), None)
            option = next((item for item in _field_options(move_field)
                           if item["value"] == move_id), None) if move_field and move_id else None
            if option:
                lines.append(f"CAMERA MOVE ({option['category']}): {option['prompt']}")
    return ("STUDIO DIRECTOR CONFIG (user-selected, apply to this shot; preserve the story, "
            "reference roles, identity and action):\n" + "\n".join(lines)) if lines else ""


def apply_official_director_config(prompt: str, direction: str) -> str:
    """Place selected Studio settings inside the official H3 IR description section."""
    if not direction:
        return prompt
    marker = re.search(r"(?m)^detailed_description:[ \t]*(?:\r?\n|$)", prompt)
    if not marker:
        raise ValueError("Официальный промпт не содержит detailed_description для настроек Studio")
    instruction = (
        direction + "\n"
        "Apply these selected Studio camera, lens, lighting, color and style settings throughout the shot, "
        "preserving the written story, actions, timing and reference roles.\n\n"
    )
    return prompt[:marker.end()] + instruction + prompt[marker.end():]


_W = lambda value: str(value).replace("_", " ")
_LIGHT_FROM = {"front": "from the front", "side": "from the side", "back": "from behind", "top": "from above"}
_TIME = {"golden_hour": "golden hour", "blue_hour": "blue hour", "midday": "midday sun", "overcast_day": "overcast day",
         "night": "night", "interior_day": "interior by day", "interior_night": "interior at night"}
_SCHEME = {"three_point": "three-point lighting", "high_key": "high-key lighting", "low_key": "low-key lighting",
           "practical_only": "lit only by practical sources"}


def director_config_sentences(config: dict, skip: frozenset = frozenset(), node_grain: bool = False) -> str:
    """The selected settings as a few plain English sentences, each setting once (no key=value lines).

    H3 reads camera, style and light words in its deep blocks (attention probe 05.11); machine-style lines
    and repeated settings only add tokens. `skip` drops categories the prompt already states (scene takes
    write framing, angle and camera move in shot_contract.opening()).
    """
    config = normalize_director_config(config)
    p = {cat: {k: v for k, v in entry["parameters"].items() if (cat, k) not in NON_PROMPT_FIELDS}
         for cat, entry in config.items() if cat not in skip}
    out = []
    lens, focus = p.get("lens", {}), p.get("focus", {})
    if lens or focus:
        parts = []
        glass = " ".join(x for x in (f"{lens['focal_length_mm']:g} mm" if "focal_length_mm" in lens else "",
                                     _W(lens["look"]) if "look" in lens else "") if x)
        if glass:
            parts.append(f"shot on a {glass} lens" + (f" at f/{lens['aperture_f']:g}" if "aperture_f" in lens else ""))
        elif "aperture_f" in lens:
            parts.append(f"shot at f/{lens['aperture_f']:g}")
        if "depth" in focus:
            parts.append(f"{focus['depth']} depth of field")
        if focus.get("behavior") == "rack" and focus.get("from") and focus.get("to"):
            parts.append(f"rack focus from the {focus['from']} to the {focus['to']}")
        elif focus.get("behavior") == "tracking":
            parts.append(f"focus follows the {focus.get('target', 'subject')}")
        elif "target" in focus:
            parts.append(f"focus on the {focus['target']}")
        if parts:
            out.append(", ".join(parts))
    light = p.get("lighting", {})
    if light:
        parts = [_TIME[light["time_of_day"]]] if "time_of_day" in light else []
        quality = " ".join(_W(light[k]) for k in ("softness", "temperature") if k in light)
        source = {"practical": "practical-lamp", "sun": "sunlight"}.get(light.get("source"), _W(light.get("source", "")))
        if source or quality:
            parts.append(" ".join(x for x in (quality, source, "light") if x) + (
                f" {_LIGHT_FROM[light['direction']]}" if "direction" in light else ""))
        if "scheme" in light:
            parts.append(_SCHEME.get(light["scheme"], f"{_W(light['scheme'])} lighting"))
        if light.get("rim"):
            parts.append("a rim light on the subject")
        if light.get("atmosphere") in ("haze", "volumetric_rays"):
            parts.append("haze in the air" if light["atmosphere"] == "haze" else "volumetric light rays")
        if "contrast" in light:
            parts.append(f"{light['contrast']} contrast")
        if parts:
            out.append(", ".join(parts))
    color = p.get("color", {})
    if color:
        parts = []
        if "temperature" in color:
            parts.append(f"{_W(color['temperature'])} colour grade")
        if color.get("saturation") not in (None, "natural"):
            parts.append("monochrome" if color["saturation"] == "none" else f"{color['saturation']} saturation")
        if "contrast" in color:
            parts.append(f"{color['contrast']} contrast")
        parts += [f"{color[k]} {k}" for k in ("shadows", "highlights") if color.get(k)]
        if parts:
            out.append(", ".join(parts))
    style = p.get("style", {})
    if style:
        look = " ".join(_W(style[k]) for k in ("rendering", "genre") if k in style)
        # H3FilmLook adds real grain after decoding; asking the model for grain as well doubles it
        extras = [f"{_W(style[k])} {k}" for k in ("texture", "detail") if k in style
                  and not (node_grain and k == "texture" and "grain" in style[k])]
        if look or extras:
            out.append((f"{look} look" if look else "a look") + (" with " + " and ".join(extras) if extras else ""))
    return " ".join(s[0].upper() + s[1:] + "." for s in out)


def place_in_first_shot(prompt: str, sentences: str) -> str:
    """Put the sentences right after the first [Shot N] header of an official H3 prompt (or the section start)."""
    if not sentences:
        return prompt
    shot = re.search(r"\[Shot 1\][ \t]*", prompt)
    if shot:
        # after the shot's opening sentence (framing and angle come first), before the action
        end = re.compile(r"[.!?](?=\s)").search(prompt, shot.end())
        cut = end.end() if end and "\n" not in prompt[shot.end():end.end()] else shot.end()
        return prompt[:cut].rstrip(" \t") + " " + sentences + " " + prompt[cut:].lstrip(" \t")
    marker = re.search(r"(?m)^detailed_description:[ \t]*(?:\r?\n|$)", prompt)
    if not marker:
        raise ValueError("Официальный промпт не содержит detailed_description для настроек Studio")
    return prompt[:marker.end()] + sentences + " " + prompt[marker.end():]
