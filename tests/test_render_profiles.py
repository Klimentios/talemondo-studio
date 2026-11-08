"""Optional render profiles must reach actual loaders without changing the approved baseline."""
import json

import pytest

from app.contract import GenerationRequest
from app.graph import build_prompt
from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields


def test_int8_request_reaches_loader_with_cache_and_full_encoder():
    request = GenerationRequest('A cat walks', 5, '16:9', 'quick', profile='int8_full', steps=24, seed=42)
    graph = build_prompt(request, ['cat.png'], cache_mode='safe')
    assert graph['127']['inputs']['unet_name'] == 'minimax_h3_ref2va_int8_convrot.safetensors'
    assert graph['128']['inputs']['clip_name'] == 'qwen3vl_32b_minimax_h3_bf16.safetensors'
    assert graph['310']['inputs']['temporal_guard'] is True
    assert graph['124']['inputs']['steps'] == 24


def test_store_and_transport_keep_explicit_profile_and_options(tmp_path):
    store = StudioStore(tmp_path)
    path = store.assets_dir / 'cat.png'; path.write_bytes(b'fixture')
    asset = store.add_asset(kind='image', role='Окружение', original_name='cat.png', mime='image/png',
                            size=7, sha256='fixture', path=path)
    job = store.create_job(prompt='<Picture 1> shows a courtyard', asset_ids=[asset['asset_id']],
        settings={'profile':'int8_full', 'render_options':{'attention':'sol-attn'}, 'cache_mode':'fast'})
    fields = gpu_form_fields(job)
    assert fields['profile'] == 'int8_full'
    assert json.loads(fields['render_options_json']) == {'attention':'sol-attn'}
    assert fields['cache_mode'] == 'fast'


def test_experimental_capability_requires_real_files():
    from app.render_profiles import render_capability, PROFILES
    empty = render_capability({})
    assert empty['profiles']['int8_full']['available'] is False
    files = {key: {'input': {'required': {field: [[filename]]}}} for key,field,filename in [
        ('UNETLoader','unet_name',PROFILES['int8_full']['unet']),
        ('CLIPLoader','clip_name',PROFILES['int8_full']['clip'])]}
    assert render_capability(files)['profiles']['int8_full']['available'] is True
    assert render_capability(files)['profiles']['w4a8']['available'] is False


def test_orbit_and_sparse_are_real_nodes_with_unchanged_cache():
    request = GenerationRequest('A cat in a courtyard', 5, '16:9', 'quick', seed=42)
    graph = build_prompt(request, ['cat.png'], cache_mode='fast',
        render_options={'adapter':'orb360', 'strength':1.0, 'attention':'sol-attn'})
    assert graph['318']['inputs']['lora_name'] == 'minimax_h3_orb360_step1500.safetensors'
    assert graph['319']['class_type'] == 'BlockSparseAttention'
    assert graph['310']['inputs']['mode'] == 'H3 Fast — 0.10 / max 2'
    assert 'ORB360_CW' in graph['136']['inputs']['prompt']
    assert graph['310']['inputs']['model'] == ['319',0]
    assert graph['318']['inputs']['model'] == ['310',0]
    assert graph['126']['inputs']['model'] == ['318',0]


def test_no_options_is_byte_identical_to_baseline():
    request = GenerationRequest('A cat walks',5,'16:9','quick',seed=42)
    assert build_prompt(request,['cat.png']) == build_prompt(request,['cat.png'],render_options={})


@pytest.mark.parametrize('options', [
    {'adapter':'unknown'}, {'strength':float('nan')}, {'adapter':'orb360','strength':True},
    {'attention':'unknown'}, {'whatever':True}, {'encoder_device':'cuda:7'},
])
def test_options_fail_closed(options):
    from app.render_profiles import normalize_render_options
    with pytest.raises(ValueError): normalize_render_options(options)


def test_experimental_adapter_cannot_silently_stack_on_character_swap():
    request = GenerationRequest('A cat walks',5,'16:9','quick',seed=42)
    with pytest.raises(ValueError,match='LoRA'):
        build_prompt(request,['cat.png'],style_adapter='character_swap',render_options={'adapter':'orb360'})


def test_installer_has_immutable_pins_and_refuses_base_replacement():
    from scripts.h3_install_render_assets import selection
    from app.render_profiles import ASSETS
    chosen = selection(ASSETS,'int8_full','orb360,wushu_r2v_v7')
    assert len(chosen) == 3
    assert all(len(asset['sha256']) == 64 for _,asset in chosen)
    with pytest.raises(ValueError): selection(ASSETS,'bf16','')


def test_native_continuation_overlap_is_trimmed_from_video_and_audio(tmp_path):
    from local_studio.storyboard import concat, probe, _tools
    import subprocess
    ffmpeg, ffprobe = _tools()
    paths = []
    for i,color in enumerate(['red','blue']):
        path = tmp_path/f'{i}.mp4'
        subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i',f'color={color}:s=64x64:r=24:d=2',
            '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac','-shortest',str(path)],check=True)
        paths.append(path)
    output = tmp_path/'film.mp4'
    made = concat(paths,output,ffmpeg,ffprobe,trim_frames=[0,22])
    assert abs(made['duration']-(4-22/24)) < 0.1
    assert probe(output,ffprobe)['audio'] is not None
    assert made['trim_frames'] == [0,22]


def test_sparse_dynamic_schema_is_understood_before_submission():
    from app.render_profiles import render_problem, PROFILES
    from app.ab_variant import SPARSE_ATTENTION_DEFAULTS
    info = {
        'UNETLoader':{'input':{'required':{'unet_name':[[PROFILES['bf16']['unet']]]}}},
        'CLIPLoader':{'input':{'required':{'clip_name':[[PROFILES['bf16']['clip']]]}}},
        'BlockSparseAttention':{'input':{'required':{
            **{name:['FLOAT',{}] for name in SPARSE_ATTENTION_DEFAULTS if name != 'selection.tau'},
            'selection':['COMFY_DYNAMICCOMBO_V3', {'options':[{'key':'sol-attn','inputs':{'required':{'tau':['FLOAT',{}]}}}]}],
        }}},
    }
    assert render_problem('bf16',{'attention':'sol-attn'},info) is None
    info['BlockSparseAttention']['input']['required']['selection'][1]['options'][0]['inputs']['required'].clear()
    assert render_problem('bf16',{'attention':'sol-attn'},info) is not None


def test_gpu_api_rejects_missing_quantized_file_without_upload_or_enqueue():
    from tests.test_gpu_api_fixes import FakeComfy, post, form
    fake = FakeComfy()
    response, _ = post(form(profile='int8_full',precompiled_prompt='true'),fake)
    assert response.status_code == 422
    assert fake.graph is None and fake.queue_count == 0
    assert 'minimax_h3_ref2va_int8_convrot' in response.json()['detail']


def test_gpu_api_keeps_quantized_loader_and_reports_actual_profile():
    from tests.test_gpu_api_fixes import FakeComfy, post, form
    from app.render_profiles import PROFILES
    class ProfileComfy(FakeComfy):
        async def object_info(self):
            info = await super().object_info()
            for cls,key,filename in [('UNETLoader','unet_name',PROFILES['int8_full']['unet']),
                                      ('CLIPLoader','clip_name',PROFILES['int8_full']['clip'])]:
                info[cls] = {'input':{'required':{key:[[filename]]}}}
            return info
    fake = ProfileComfy()
    response,_ = post(form(profile='int8_full',precompiled_prompt='true',cache_mode='safe'),fake)
    assert response.status_code == 202, response.text
    assert fake.graph['127']['inputs']['unet_name'] == PROFILES['int8_full']['unet']
    assert fake.graph['310']['inputs']['temporal_guard'] is True

    assert response.json()["director"]["render_profile"]["profile"] == "int8_full"
