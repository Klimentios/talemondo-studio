"""Version the authored shot sequence without transient queue and review fields."""
from __future__ import annotations

from app.fingerprints import json_sha256

SHOT_FIELDS = ('id', 'prompt', 'duration', 'productionSpec', 'sceneShot', 'offRefs', 'fantasy')


def shot_definition(cut: dict) -> dict:
    return {key: cut[key] for key in SHOT_FIELDS if key in cut}


def scene_definition(cuts: list[dict]) -> dict:
    shots = [shot_definition(cut) for cut in cuts if isinstance(cut, dict)]
    return {'schema_version': 1, 'shots': shots, 'sha256': json_sha256(shots)}


def definition_drift(saved_cuts: list[dict], run: dict) -> dict:
    current = scene_definition(saved_cuts)
    original = run.get('definition_snapshot') or scene_definition(run.get('cuts') or [])
    return {'matches': current['sha256'] == original['sha256'],
            'saved_sha256': current['sha256'], 'run_sha256': original['sha256'],
            'scope': 'authored_shot_sequence',
            'source': 'launch_snapshot' if run.get('definition_snapshot') else 'legacy_run'}


CONTEXT_FIELDS = ('sceneStyle', 'seed', 'renderState', 'referenceState')


def context_definition(scene: dict) -> dict:
    context = {key: scene[key] for key in CONTEXT_FIELDS if key in scene}
    return {'schema_version': 1, 'context': context, 'sha256': json_sha256(context)}


def context_drift(scene: dict, run: dict) -> dict:
    original = run.get('context_snapshot')
    current = context_definition(scene)
    return {'matches': current['sha256'] == original['sha256'] if original else None,
            'saved_sha256': current['sha256'], 'run_sha256': original['sha256'] if original else None,
            'scope': 'saved_scene_settings_and_reference_bindings',
            'source': 'launch_snapshot' if original else 'unknown_legacy'}
