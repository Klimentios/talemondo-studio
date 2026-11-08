"""Validated color-grade request: Lab statistics of a reference photo → H3ColorGrade node."""
from __future__ import annotations

import math

_LIMITS = {"target_mean": ((0, 100), (-128, 127), (-128, 127)), "target_std": ((0, 100), (0, 128), (0, 128))}


def normalize_color_grade(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"target_mean", "target_std", "strength", "preserve_luminance"}:
        raise ValueError("color_grade: ожидаются target_mean, target_std, strength")
    result = {}
    for key, limits in _LIMITS.items():
        triple = value.get(key)
        if not isinstance(triple, list) or len(triple) != 3:
            raise ValueError(f"color_grade.{key}: нужны три числа L, a, b")
        for number, (lo, hi) in zip(triple, limits):
            if type(number) not in (int, float) or not math.isfinite(number) or not lo <= number <= hi:
                raise ValueError(f"color_grade.{key}: значение вне диапазона")
        result[key] = [float(number) for number in triple]
    strength = value.get("strength", 0.5)
    if type(strength) not in (int, float) or not 0 <= strength <= 1:
        raise ValueError("color_grade.strength: от 0 до 1")
    preserve = value.get("preserve_luminance", False)
    if type(preserve) is not bool:
        raise ValueError("color_grade.preserve_luminance: нужен boolean")
    if strength == 0:
        return None
    result["strength"] = float(strength)
    result["preserve_luminance"] = preserve
    return result


def color_grade_node_inputs(grade: dict) -> dict:
    mean, std = grade["target_mean"], grade["target_std"]
    return {
        "target_l_mean": mean[0], "target_a_mean": mean[1], "target_b_mean": mean[2],
        "target_l_std": std[0], "target_a_std": std[1], "target_b_std": std[2],
        "strength": grade["strength"], "preserve_luminance": grade["preserve_luminance"],
    }


def normalize_film_look(value: dict | None) -> dict | None:
    """Camera preset look: {contrast -0.5..0.5, grain 0..1, grain_size 1..3, seed}."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"contrast", "grain", "grain_size", "seed"}:
        raise ValueError("film_look: ожидаются contrast, grain, grain_size, seed")
    limits = {"contrast": (-0.5, 0.5, 0.0), "grain": (0.0, 1.0, 0.0), "grain_size": (1.0, 3.0, 1.2)}
    result = {}
    for key, (lo, hi, default) in limits.items():
        number = value.get(key, default)
        if type(number) not in (int, float) or not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError(f"film_look.{key}: вне диапазона {lo}–{hi}")
        result[key] = float(number)
    seed = value.get("seed", 0)
    if type(seed) is not int or not 0 <= seed < 2**31:
        raise ValueError("film_look.seed: целое 0…2^31")
    result["seed"] = seed
    if result["contrast"] == 0 and result["grain"] == 0:
        return None
    return result
