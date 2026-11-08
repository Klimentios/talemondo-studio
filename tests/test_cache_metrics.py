import copy
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.cache_metrics import validated_metrics
from app.comfy_nodes.h3_cache_metrics.runtime import append_report, snapshot
from scripts import install_cache_metrics as installer


@dataclass
class Config:
    threshold: float = .08
    temporal_guard: bool = True


def report():
    cache = SimpleNamespace(config=Config(), full_steps=3, cached_steps=2, cached_step_numbers=[3,5],
                            diff_values=[.01,.2,float('nan')], temporal_diff_values=[.1])
    return snapshot(cache,'safe',1.5,True,'64')


def test_metrics_are_durable_per_prompt_and_sampler(tmp_path):
    uid='c2075298-573a-4d5c-bac6-f07a8772c5d4'
    append_report(tmp_path,uid,report())
    append_report(tmp_path,uid,{**report(),'sampler_node_id':'65'})
    data=json.loads((tmp_path/(uid+'.json')).read_text())
    assert validated_metrics(data,uid)==data
    assert len(data['passes'])==2
    assert data['passes'][0]['residual_difference']=={'count':2,'min':.01,'max':.2}
    assert validated_metrics(data,'wrong-prompt') is None
    bad=copy.deepcopy(data);bad['passes'][0]['reused_forward_calls']=6
    assert validated_metrics(bad,uid) is None
    with pytest.raises(ValueError):append_report(tmp_path,'../../outside',report())


def test_instrumentation_preserves_results_errors_and_reset():
    # Execute the exact replacement body, checking both normal and exception paths.
    captured=[]
    cache=SimpleNamespace(reset=lambda:captured.append('reset'),summary=lambda:'summary')
    body=installer.NEW
    code='def wrapper(executor):\n'+body+'\n'
    namespace={'cache':cache,'preset':'safe','logging':logging,'time':time,'args':(), 'kwargs':{},
               '_record_cache_metrics':lambda c,p,s,ok:captured.append(('report',ok))}
    exec(code,namespace)
    result=object()
    assert namespace['wrapper'](lambda:result) is result
    assert captured==[('report',True),'reset']
    captured.clear()
    def fail():raise RuntimeError('inference failed')
    with pytest.raises(RuntimeError,match='inference failed'):namespace['wrapper'](fail)
    assert captured==[('report',False),'reset']


def test_patcher_is_idempotent_and_rejects_unknown_source():
    source='import logging\n'+installer.OLD
    with patch.object(installer,'SHA',hashlib.sha256(source.encode()).hexdigest()):
        patched=installer.patched(source)
        assert installer.patched(patched)==patched
        with pytest.raises(ValueError):installer.patched(patched+'# unexpected edit')


def test_worker_persists_only_matching_metrics(tmp_path):
    import asyncio
    from local_studio.store import StudioStore
    from local_studio.worker import QueueWorker
    from tests.test_queue_worker import FakeGpu
    studio=StudioStore(tmp_path)
    path=studio.assets_dir/'a.png';path.write_bytes(b'fixture')
    asset=studio.add_asset(kind='image',role='hero',original_name='a.png',mime='image/png',
                           size=7,sha256='fixture',path=path)
    job=studio.create_job(prompt='<Picture 1> waves',asset_ids=[asset['asset_id']],settings={})
    gpu=FakeGpu();worker=QueueWorker(studio,gpu)
    asyncio.run(worker.run_once())
    remote=studio.get_job(job['job_id'])['remote_job_id']
    counters={'version':1,'prompt_id':remote,'passes':[report()]}
    gpu.states[remote]={'status':'success','cache_runtime':counters}
    asyncio.run(worker.run_once())
    saved=json.loads(StudioStore(tmp_path).get_job(job['job_id'])['director_json'])
    assert saved['cache_execution']['runtime_counters']==counters


def test_comfy_client_reads_metrics_without_requiring_extension():
    import asyncio
    import httpx
    from app.comfy_client import ComfyClient
    uid='c2075298-573a-4d5c-bac6-f07a8772c5d4'
    value={'version':1,'prompt_id':uid,'passes':[report()]}
    real=httpx.AsyncClient
    for response,expected in [(httpx.Response(200,json=value),value),(httpx.Response(404),None),
                              (httpx.Response(200,json={**value,'prompt_id':'another-job'}),None)]:
        transport=httpx.MockTransport(lambda request:response)
        with patch('httpx.AsyncClient',lambda **kw:real(transport=transport,**kw)):
            assert asyncio.run(ComfyClient('http://test').cache_metrics(uid))==expected
