"""Allow-listed production routes. No arbitrary graphs or unmeasured 'fastest model' claims."""
from __future__ import annotations

from .contract import DEFAULTS
from .cache_policy import normalize_cache_config
from .sampling import sampling_inputs


def resolve_recipe(snapshot) -> dict:
    settings = snapshot.settings
    normalize_cache_config(settings.get('cache_mode', DEFAULTS['cache_mode']), settings.get('cache_config'))
    sampling_inputs(settings.get('sampling_profile', 'standard'))
    return {'id': 'h3-reference-scene', 'version': 1, 'engine': 'h3',
            'reason': 'Новая сцена по фото/видео-референсам: Context-IR → Ref2VA.',
            'quality_status': 'requires_gpu_visual_acceptance',
            'cache_mode': settings.get('cache_mode', DEFAULTS['cache_mode']),
            'sampling_profile': settings.get('sampling_profile', 'standard'),
            'steps': settings.get('steps', DEFAULTS['steps'])}
