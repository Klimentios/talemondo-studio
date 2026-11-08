import hashlib
import json

import pytest

from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields
from app.contract import GenerationRequest
from app.graph import build_prompt


def fixture(tmp_path):
    store = StudioStore(tmp_path)
    path = store.assets_dir / 'hero.png'
    path.write_bytes(b'hero')
    asset = store.add_asset(kind='image', role='Персонаж целиком', original_name='hero.png',
        mime='image/png', size=4, sha256=hashlib.sha256(b'hero').hexdigest(), path=path)
    spec = {'franchise_id': 'f1', 'characters': [{'id': 'Vega', 'revision': 1, 'identity': 'The approved adult cat',
        'reference_asset_ids': [asset['asset_id']]}], 'shot': {'id': 'shot1', 'revision': 1, 'duration': 5.0,
        'purpose': 'Notice a moving object', 'state_in': 'Unaware', 'state_out': 'Alert',
        'beats': [{'id': 'b1', 'actor_ids': ['Vega'], 'start': 0.0, 'end': 3.0,
            'cause': 'The object moves', 'observed_body_action': 'Turns ears and head toward the object',
            'effect': 'Tracks the object with his eyes'}]}}
    return store, asset['asset_id'], spec


def test_spec_reaches_saved_job_gpu_form_and_sampler_graph_without_gpu(tmp_path):
    store, asset_id, spec = fixture(tmp_path)
    job = store.create_job(prompt='<Picture 1> watches the object', asset_ids=[asset_id], settings={}, production_spec=spec)
    assert job['original_prompt'] == '<Picture 1> watches the object'
    assert json.loads(job['production_spec_json'])['shot']['id'] == 'shot1'
    fields = gpu_form_fields(job)
    assert fields['duration'] == '5.0' or fields['duration'] == '5'
    assert 'Turns ears and head' in fields['prompt']
    request = GenerationRequest(fields['prompt'], 5, '9:16', 'custom', seed=1, steps=24, megapixels=0.7)
    graph = build_prompt(request=request, references=['hero.png'])
    assert 'Turns ears and head' in graph['136']['inputs']['prompt']
    replay = store.create_job(prompt=job['original_prompt'], asset_ids=[asset_id],
        settings=json.loads(job['settings_json']), production_spec=json.loads(job['production_spec_json']))
    assert replay['compiled_prompt'] == job['compiled_prompt']


def test_conflicting_settings_and_unattached_identity_are_rejected(tmp_path):
    store, asset_id, spec = fixture(tmp_path)
    with pytest.raises(ValueError, match='Длительность'):
        store.create_job(prompt='A cat', asset_ids=[asset_id], settings={'duration': 10}, production_spec=spec)
    with pytest.raises(ValueError, match='не приложен'):
        store.create_job(prompt='A cat', asset_ids=[], settings={}, production_spec=spec)


def test_draft_behavior_can_be_authored_but_cannot_be_silently_rendered(tmp_path):
    store, asset_id, spec = fixture(tmp_path)
    spec['behavior_patterns'] = [{'id': 'p1', 'actor_id': 'Vega', 'trigger': 'Object moves',
        'observed_body_action': 'Ears turn before the head', 'status': 'draft'}]
    spec['shot']['beats'][0]['behavior_pattern_ids'] = ['p1']
    with pytest.raises(ValueError, match='черновиком'):
        store.create_job(prompt='A cat', asset_ids=[asset_id], settings={}, production_spec=spec)
    assert store.list_jobs() == []
    spec['behavior_patterns'][0].update(status='verified', source='Author observation', evidence=['clip at 00:01'])
    job = store.create_job(prompt='A cat', asset_ids=[asset_id], settings={}, production_spec=spec)
    assert 'Ears turn before the head' in job['compiled_prompt']


def test_visual_method_is_saved_and_reaches_video_prompt(tmp_path):
    from local_studio.visual_profiles import visual_profiles
    store, asset_id, spec = fixture(tmp_path)
    profile = visual_profiles()[0]['visual_profile']
    spec['visual_profile'] = profile
    spec['shot']['spatial_layout'] = 'Door left, hero right'
    job = store.create_job(prompt='A cat', asset_ids=[asset_id], settings={}, production_spec=spec)
    assert profile['video_direction'] in job['compiled_prompt']
    assert 'Door left, hero right' in job['compiled_prompt']
    assert json.loads(job['production_spec_json'])['visual_profile']['sources'] == profile['sources']
