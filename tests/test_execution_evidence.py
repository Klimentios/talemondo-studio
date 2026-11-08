from app.execution_evidence import history_evidence


def test_history_is_bound_to_prompt_and_runtime_does_not_claim_weight_verification():
    graph = {'1': {'class_type': 'Sampler', 'inputs': {'seed': 42}}}
    entry = {'prompt': [0, 'p1', graph], 'status': {'status_str': 'success', 'messages': [
        ['execution_start', {'timestamp': 10}], ['execution_success', {'timestamp': 20}]]}}
    stats = {'system': {'python_version': '3.11', 'pytorch_version': '2.8', 'argv': ['secret']}}
    report = history_evidence('p1', entry, stats)
    assert report['timestamps']['execution_success'] == 20
    assert report['runtime_observed'] == {'python_version': '3.11', 'pytorch_version': '2.8'}
    assert report['weights_sha256'] is None and report['custom_node_versions'] is None
    assert history_evidence('another', entry) is None
    assert history_evidence('p1', {}) is None
    graph['1']['inputs']['seed'] = 43
    assert report['graph_sha256'] != history_evidence('p1', entry)['graph_sha256']


def test_completed_history_keeps_evidence_when_runtime_endpoint_is_unavailable(monkeypatch):
    import asyncio
    import httpx
    from app.comfy_client import ComfyClient
    reads = []
    entry = {'prompt': [0, 'p1', {'1': {'class_type': 'Sampler', 'inputs': {}}}],
             'status': {'status_str': 'success'},
             'outputs': {'92': {'images': [{'filename': 'done.mp4', 'type': 'output'}]}}}
    def handler(request):
        reads.append(request.url.path)
        if request.url.path == '/history/p1':
            return httpx.Response(200, json={'p1': entry})
        return httpx.Response(503)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    async def scenario():
        client = ComfyClient('http://comfy')
        first, second = await client.status('p1'), await client.status('p1')
        assert first['status'] == second['status'] == 'success'
        assert first['execution_observation']['runtime_observed'] is None
        assert first['execution_observation']['prompt_id'] == 'p1'
    asyncio.run(scenario())
    assert reads.count('/system_stats') == 1
