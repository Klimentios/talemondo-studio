"""Opt-in H3 loader/LoRA experiments. Absence preserves historical graphs and job fingerprints."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .object_info import input_options

ASSETS = json.loads(Path(__file__).with_name('render_assets.json').read_text())
ENCODER = 'qwen3vl_32b_minimax_h3_bf16.safetensors'
PROFILES = {name: {'unet': asset['filename'], 'clip': ENCODER}
            for name, asset in ASSETS['profiles'].items()}
ADAPTERS = ASSETS['adapters']


def normalize_render_options(value: dict | None) -> dict | None:
    if value is None or value == {}:
        return None
    if not isinstance(value, dict) or set(value) - {'adapter', 'strength', 'attention', 'encoder_device'}:
        raise ValueError('Неизвестные параметры эксперимента H3')
    adapter = value.get('adapter', 'none')
    if not isinstance(adapter, str) or adapter not in {'none', *ADAPTERS}:
        raise ValueError('Неизвестная экспериментальная LoRA')
    result = {}
    if adapter != 'none':
        strength = value.get('strength', ADAPTERS[adapter]['strength'])
        if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 < strength <= 1.2:
            raise ValueError('Сила экспериментальной LoRA: больше 0 и до 1,2')
        result.update(adapter=adapter, strength=float(strength))
    elif 'strength' in value:
        raise ValueError('Сила LoRA задана без экспериментальной LoRA')
    attention = value.get('attention', 'dense')
    if not isinstance(attention,str) or attention not in {'dense', 'sol-attn'}:
        raise ValueError('Внимание: dense или sol-attn')
    if attention != 'dense':
        result['attention'] = attention
    device = value.get('encoder_device', 'default')
    if not isinstance(device,str) or device not in {'default', 'cpu'}:
        raise ValueError('Устройство энкодера: default или cpu')
    if device != 'default':
        result['encoder_device'] = device
    return result or None


def missing_render_files(profile: str, options: dict | None, info: dict) -> list[str]:
    options = normalize_render_options(options) or {}
    wanted = [('UNETLoader', 'unet_name', PROFILES[profile]['unet']),
              ('CLIPLoader', 'clip_name', PROFILES[profile]['clip'])]
    if options.get('adapter'):
        wanted.append(('LoraLoaderModelOnly', 'lora_name', ADAPTERS[options['adapter']]['filename']))
    return [filename for node, key, filename in wanted if filename not in input_options(info, node, key)]


def render_required_nodes(options: dict | None) -> set[str]:
    options = normalize_render_options(options) or {}
    return ({'LoraLoaderModelOnly'} if options.get('adapter') else set()) | (
        {'BlockSparseAttention'} if options.get('attention') else set())


def render_problem(profile: str, options: dict | None, info: dict) -> str | None:
    options = normalize_render_options(options) or {}
    missing = missing_render_files(profile, options, info)
    nodes = render_required_nodes(options) - set(info)
    if missing or nodes:
        return 'Эксперимент H3 недоступен: ' + ', '.join(sorted(nodes) + missing)
    if options.get('encoder_device') and options['encoder_device'] not in input_options(info, 'CLIPLoader', 'device'):
        return 'ComfyUI не поддерживает выбранное устройство энкодера'
    if options.get('attention'):
        from .ab_variant import SPARSE_ATTENTION_DEFAULTS
        fields = (info.get('BlockSparseAttention') or {}).get('input') or {}
        inputs = {**(fields.get('required') or {}), **(fields.get('optional') or {})}
        names = set(inputs)
        methods = input_options(info, 'BlockSparseAttention', 'selection')
        selection = inputs.get('selection') or []
        if len(selection) > 1 and selection[0] == 'COMFY_DYNAMICCOMBO_V3' and isinstance(selection[1],dict):
            options = selection[1].get('options') or []
            methods = [item.get('key') for item in options if isinstance(item,dict)]
            sol = next((item for item in options if isinstance(item,dict) and item.get('key') == 'sol-attn'),{})
            nested = sol.get('inputs') or {}
            names |= {'selection.'+name for section in ('required','optional') for name in nested.get(section,{})}
        if not set(SPARSE_ATTENTION_DEFAULTS) <= names or 'sol-attn' not in methods:
            return 'Обнови BlockSparseAttention: схема sol-attn не совпадает с графом Studio'
    return None


def render_capability(info: dict) -> dict:
    profiles = {}
    for name, asset in ASSETS['profiles'].items():
        missing = missing_render_files(name, None, info)
        profiles[name] = {'label': asset['label'], 'available': not missing, 'missing': missing,
                          'experimental': name != 'bf16', 'quality_verified': False,
                          'unet': asset['filename'], 'encoder': ENCODER}
    adapters = {name: {'label': asset['label'], 'available': asset['filename'] in input_options(info, 'LoraLoaderModelOnly', 'lora_name'),
                      'filename':asset['filename'], 'strength':asset['strength'], 'quality_verified':False}
                for name,asset in ADAPTERS.items()}
    sparse_problem = render_problem('bf16', {'attention':'sol-attn'}, info)
    return {'version':1, 'profiles':profiles, 'adapters':adapters,
            'attention':['dense'] + (['sol-attn'] if sparse_problem is None else []),
            'encoder_devices':input_options(info, 'CLIPLoader', 'device'),
            'default':'bf16', 'cache_policy':'preserve_selection'}


def validate_render_combination(options: dict | None, style_adapter: str, refinement=False) -> None:
    options = normalize_render_options(options) or {}
    if refinement and options:
        raise ValueError('Экспериментальные параметры недоступны при уточнении героев')
    adapter = options.get('adapter')
    if adapter and style_adapter != 'none' and not (adapter == 'wushu_r2v_v7' and style_adapter == 'combat'):
        raise ValueError('Экспериментальную LoRA нельзя смешивать с текущей стилевой/Character Swap LoRA')


def check_render_capability(caps: dict | None, profile: str, value: dict | None) -> None:
    options = normalize_render_options(value) or {}
    if not isinstance(caps, dict) or caps.get('version') != 1:
        raise ValueError('GPU API не поддерживает экспериментальные профили; обнови сервер')
    if not (caps.get('profiles', {}).get(profile) or {}).get('available'):
        raise ValueError(f'Профиль {profile} не установлен на GPU; требуется установка выбранных весов')
    adapter = options.get('adapter')
    if adapter and not (caps.get('adapters', {}).get(adapter) or {}).get('available'):
        raise ValueError(f'Экспериментальная LoRA {adapter} не установлена на GPU')
    if options.get('attention') and options['attention'] not in caps.get('attention', []):
        raise ValueError('SOL attention недоступен на этом GPU')
    if options.get('encoder_device') and options['encoder_device'] not in caps.get('encoder_devices', []):
        raise ValueError('Устройство энкодера недоступно на этом GPU')


def apply_render_options(graph: dict, value: dict | None, style_adapter='none') -> None:
    options = normalize_render_options(value)
    if not options:
        return
    validate_render_combination(options, style_adapter)
    if options.get('encoder_device'):
        graph['128']['inputs']['device'] = options['encoder_device']
    model = graph['126']['inputs']['model']
    adapter = options.get('adapter')
    if adapter:
        asset = ADAPTERS[adapter]
        if style_adapter == 'combat':
            # One variable: replace only the existing Wushu action LoRA; keep spatial physics.
            graph['311']['inputs'].update(lora_name=asset['filename'], strength_model=options['strength'])
        else:
            graph['318'] = {'class_type':'LoraLoaderModelOnly', '_meta':{'title':asset['label']},
                            'inputs':{'model':model,'lora_name':asset['filename'],'strength_model':options['strength']}}
            model = ['318',0]
        trigger = asset['trigger']
        text = graph['136']['inputs']['prompt']
        if trigger not in text:
            from .h3_ir import is_ir_prompt
            import re
            if is_ir_prompt(text):
                text = re.sub(r'(^detailed_description\s*:\s*)', lambda m:m[1]+trigger+', ', text, count=1, flags=re.M|re.I)
            else:
                text = trigger+', '+text
            graph['136']['inputs']['prompt'] = text
    if options.get('attention'):
        from .ab_variant import SPARSE_ATTENTION_DEFAULTS
        # Patch the base before cache/LoRA cloning; preserve both downstream branches.
        graph['319'] = {'class_type':'BlockSparseAttention','_meta':{'title':'SOL attention · эксперимент'},
                        'inputs':{'model':['127',0],**SPARSE_ATTENTION_DEFAULTS}}
        for uid,item in graph.items():
            if uid != '319' and item.get('inputs',{}).get('model') == ['127',0]:
                item['inputs']['model'] = ['319',0]
        if model == ['127',0]:
            model = ['319',0]
    graph['126']['inputs']['model'] = model
    graph['124']['inputs']['model'] = model
