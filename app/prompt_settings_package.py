"""Writer-to-Studio settings envelope, consumed before normal native validation."""
import json
import re
from pathlib import Path

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / 'preview/h3-director/prompt-settings-contract.json').read_text())
BLOCK = re.compile(r'```studio-settings\s*\n([\s\S]*?)\n```')


def prompt_settings_package(prompt, settings):
    matches = list(BLOCK.finditer(prompt))
    if not matches:
        if '```studio-settings' in prompt:
            raise ValueError('Блок настроек промпта не завершён')
        return prompt, dict(settings)
    if len(matches) != 1:
        raise ValueError('В промпте должен быть один блок studio-settings')
    try:
        data = json.loads(matches[0][1])
    except ValueError as exc:
        raise ValueError('В блоке studio-settings некорректный JSON') from exc
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('settings'), dict) or set(data) - {'version', 'settings'}:
        raise ValueError('Нужен пакет version: 1 и settings')
    unknown = set(data['settings']) - set(CONTRACT['keys'])
    if unknown:
        raise ValueError(f"Неподдерживаемые настройки промпта: {', '.join(sorted(unknown))}")
    return (prompt[:matches[0].start()] + prompt[matches[0].end():]).strip(), {**settings, 'film_look': None, 'color_grade': None, 'camera_state': None, 'camera_optics': None, 'director_config': None, **data['settings']}


def ensure_audio_direction(prompt):
    from .h3_ir import is_ir_prompt, parse_ir, render_ir
    if is_ir_prompt(prompt):
        sections = parse_ir(prompt)
        if not sections.get('overall_soundscape', '').strip():
            sections['overall_soundscape'] = CONTRACT['audio_direction']
            return render_ir(sections)
        return prompt
    return prompt + "\n\nSOUND DIRECTION: " + CONTRACT['audio_direction']
