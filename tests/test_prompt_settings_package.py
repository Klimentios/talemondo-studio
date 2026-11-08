import json
import pytest
from app.prompt_settings_package import prompt_settings_package, ensure_audio_direction
from app.h3_ir import render_ir, parse_ir
from local_studio.store import StudioStore


def package(prompt, settings):
    return prompt + '\n```studio-settings\n' + json.dumps({'version': 1, 'settings': settings}) + '\n```'


def test_writer_settings_override_stale_studio_and_reach_saved_native_job(tmp_path):
    store = StudioStore(tmp_path)
    path = store.assets_dir / 'hero.png'; path.write_bytes(b'image')
    asset = store.add_asset(kind='image', role='hero', original_name='hero.png', mime='image/png', size=5, sha256='image', path=path)
    result = store.create_job(prompt=package('<Picture 1> A man walks down a corridor',
        {'duration': 5, 'aspect_ratio': '9:16', 'steps': 25, 'style_adapter': 'none'}), asset_ids=[asset['asset_id']],
        settings={'duration': 15, 'aspect_ratio': '16:9', 'steps': 24, 'film_look': {'contrast': .22}})
    settings = json.loads(result['settings_json'])
    assert settings['duration'] == 5 and settings['aspect_ratio'] == '9:16' and settings['steps'] == 25
    assert settings['style_adapter'] == 'none' and settings['film_look'] is None
    assert 'studio-settings' not in result['compiled_prompt']
    assert 'action-synchronized sound effects' in result['compiled_prompt']


def test_unknown_or_broken_contract_cannot_silently_queue():
    _, settings = prompt_settings_package(package("A fight", {"steps":30}), {"style_adapter":"combat"})
    assert settings["style_adapter"] == "combat"
    for prompt in [package('scene', {'turbo': True}), 'scene\n```studio-settings\n{}']:
        with pytest.raises(ValueError):
            prompt_settings_package(prompt, {})


def test_sound_default_preserves_written_dialogue_and_existing_soundscape():
    sections = {'subject_definitions': '<Subject 1> is VEGA.', 'summary': 'VEGA speaks.',
                'retention_analysis': 'Keep VEGA.', 'detailed_description': 'VEGA says "Hello".',
                'overall_soundscape': '', 'non_diegetic_music': 'No music.'}
    prompt = ensure_audio_direction(render_ir(sections))
    assert 'VEGA says "Hello"' in prompt
    assert parse_ir(prompt)['overall_soundscape']
    sections['overall_soundscape'] = 'Footsteps; VEGA says "Hello" in his assigned voice.'
    complete = render_ir(sections)
    assert ensure_audio_direction(complete) == complete
