"""Compile a validated production description into the existing H3 input path."""
from __future__ import annotations

from app.h3_ir import is_ir_prompt, parse_ir, render_ir
from .production_spec import ProductionSpec, validate_spec


def compile_production(prompt: str, value: dict, asset_ids: list[str], settings: dict,
                       owners: dict | None) -> tuple[str, dict, dict, dict]:
    spec = ProductionSpec.model_validate(value)
    report = validate_spec(spec)
    if not report["valid"]:
        raise ValueError("Ошибки постановки: " + ", ".join(item["code"] for item in report["issues"]))
    settings, owners = dict(settings), dict(owners or {})
    if "duration" in settings and float(settings["duration"]) != spec.shot.duration:
        raise ValueError("Длительность задания расходится с ShotSpec")
    settings["duration"] = spec.shot.duration
    direction = {**(spec.visual_profile.director_config if spec.visual_profile else {}), **spec.shot.director_config}
    if direction:
        existing = settings.get("director_config")
        if existing and any(key in existing and existing[key] != value for key, value in direction.items()):
            raise ValueError("Режиссура задания расходится с ShotSpec")
        settings["director_config"] = {**(existing or {}), **direction}
    lines = [f"Shot purpose: {spec.shot.purpose}", f"Initial state: {spec.shot.state_in}"]
    if spec.shot.spatial_layout:
        lines.append(f"Fixed spatial layout: {spec.shot.spatial_layout}")
    if spec.visual_profile:
        for asset_id in spec.visual_profile.reference_asset_ids:
            if asset_id not in asset_ids:
                raise ValueError(f"Визуальный референс {asset_id} не приложен к заданию")
        lines.append(f"Visual treatment: {spec.visual_profile.video_direction or spec.visual_profile.frame_direction}")
    for actor in spec.characters:
        if not actor.reference_asset_ids:
            raise ValueError(f"Персонажу {actor.id} не назначен референс")
        for asset_id in actor.reference_asset_ids:
            if asset_id not in asset_ids:
                raise ValueError(f"Референс {asset_id} персонажа {actor.id} не приложен к заданию")
            if asset_id in owners and owners[asset_id] != actor.id:
                raise ValueError(f"Референс {asset_id} назначен другому персонажу")
            owners[asset_id] = actor.id
        lines.append(f"{actor.id}: {actor.identity}. Physical constraints: {actor.physical_constraints}. Individual behavior: {actor.individual_behavior}.")
    patterns = {pattern.id: pattern for pattern in spec.behavior_patterns}
    for beat in sorted(spec.shot.beats, key=lambda item: item.start):
        lines.append(f"At {beat.start:g}–{beat.end:g}s, {', '.join(beat.actor_ids)}: "
                     f"cause: {beat.cause}; observable action: {beat.observed_body_action}; result: {beat.effect}.")
        for key in beat.behavior_pattern_ids:
            pattern = patterns[key]
            if pattern.status != "verified":
                raise ValueError(f"Поведение {key} остаётся черновиком; убери его из исполняемого шота")
            lines.append(f"{pattern.actor_id}: {pattern.observed_body_action}.")
    lines.append(f"Final state: {spec.shot.state_out}")
    block = "\n".join(lines)
    if is_ir_prompt(prompt):
        sections = parse_ir(prompt)
        sections["detailed_description"] = sections.get("detailed_description", "") + "\n\n" + block
        prompt = render_ir(sections)
    else:
        prompt = prompt.rstrip() + "\n\n" + block
    return prompt, settings, owners, spec.model_dump()
