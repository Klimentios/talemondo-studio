"""Validated optical controls for the installed H3OperatorCamera guide node."""
from __future__ import annotations

import math


_RANGES = {
    "focal_length_mm": (12.0, 135.0),
    "aperture_f": (1.2, 22.0),
    "focus_distance_m": (0.3, 50.0),
    "shutter_angle_deg": (10.0, 360.0),
    "iso": (50.0, 12800.0),
}
_FOCUS_MODES = {"subject_head", "manual"}
_SENSORS = {"full_frame", "super_35", "micro_four_thirds"}  # H3OperatorCamera inputs
# app/director_schema.json writes "full-frame" / "large format" / "super_35"; the node has no large
# format, and its largest sensor (full frame, 24 mm high) is the closest.
_SENSOR_ALIASES = {"large_format": "full_frame", "lf": "full_frame", "fullframe": "full_frame",
                   "s35": "super_35", "super35": "super_35", "mft": "micro_four_thirds", "m43": "micro_four_thirds"}


def normalize_sensor_format(value: object) -> object:
    if not isinstance(value, str):
        return value
    key = "_".join(value.strip().lower().replace("-", " ").split())
    return _SENSOR_ALIASES.get(key, key)


def normalize_camera_optics(value: dict | None, camera_state: dict | None) -> dict | None:
    """Return complete node inputs; optical controls require a camera trajectory."""
    if value is None:
        return None
    if not isinstance(value, dict) or not value or set(value) - (set(_RANGES) | {"sensor_format", "focus_mode"}):
        raise ValueError("Неверные параметры оптического гида камеры")
    if not isinstance(camera_state, dict) or not camera_state.get("keys"):
        raise ValueError("Для оптики камеры нужна траектория camera_state")
    first = camera_state["keys"][0]
    defaults = {
        "sensor_format": "full_frame",
        "focal_length_mm": first.get("focal_length_mm", 50.0),
        "aperture_f": first.get("aperture_f", 2.8),
        "focus_mode": "manual",
        "focus_distance_m": first.get("focus_distance_m", first.get("distance", 4.0)),
        "shutter_angle_deg": 180.0,
        "iso": 400.0,
    }
    result = {**defaults, **value}
    result["sensor_format"] = normalize_sensor_format(result["sensor_format"])
    if result["sensor_format"] not in _SENSORS or result["focus_mode"] not in _FOCUS_MODES:
        raise ValueError("Неверный сенсор или режим фокуса камеры")
    for name, (lower, upper) in _RANGES.items():
        number = result[name]
        if type(number) not in (int, float) or not math.isfinite(number) or not lower <= number <= upper:
            raise ValueError(f"Параметр камеры {name} вне диапазона {lower}–{upper}")
        result[name] = float(number)
    return result
