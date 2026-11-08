import copy
import hashlib

from app.comfy_nodes.h3_execution_context.runtime import capture_context, graph_digest
from app.execution_evidence import merge_prompt_context
from app.model_inventory import attest_weights


def test_disk_attestation_and_loaded_method_evidence_do_not_mutate_graph(tmp_path):
    class Sampler:
        def execute(self, seed):
            return seed
    weight = tmp_path / 'model.safetensors'
    weight.write_bytes(b'weights')
    index = attest_weights(tmp_path, {weight.name: hashlib.sha256(b'weights').hexdigest()})
    graph = {'1': {'class_type': 'Sampler', 'inputs': {'unet_name': weight.name, 'seed': 42}}}
    before = copy.deepcopy(graph)
    report = capture_context(graph, tmp_path, index, {'Sampler': Sampler}, lambda c, n: str(weight))
    assert graph == before
    assert report['weights'][0]['state'] == 'unchanged_since_attestation'
    assert report['node_methods_sha256']['Sampler']['execute']
    assert report['environment_complete'] is False
    report['prompt_id'] = 'p1'
    evidence = {'prompt_id': 'p1', 'graph_sha256': graph_digest(graph)}
    assert merge_prompt_context(evidence, report)['prompt_context_matches'] is True
    assert merge_prompt_context({**evidence, 'prompt_id': 'another'}, report)['prompt_context'] is None
    weight.write_bytes(b'changed')
    drift = capture_context(graph, tmp_path, index, {}, lambda c, n: str(weight))
    assert drift['weights'][0]['sha256'] is None
    assert drift['weights'][0]['state'] == 'changed_or_missing'


def test_missing_index_or_unknown_weight_selector_remains_unknown(tmp_path):
    graph = {'1': {'class_type': 'UnknownNode', 'inputs': {'custom_weight': 'a.safetensors'}}}
    report = capture_context(graph, tmp_path, {}, {}, lambda c, n: None)
    assert report['weights'][0]['state'] == 'unknown'
    assert report['node_methods_sha256'] == {'UnknownNode': {}}


def test_installed_observer_writes_sidecar_without_changing_prompt_graph(tmp_path, monkeypatch):
    import importlib.util
    import json
    import shutil
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from app.model_inventory import write_inventory
    source = Path(__file__).resolve().parents[1]
    installed = tmp_path / 'custom_nodes' / 'observer'
    installed.mkdir(parents=True)
    for name in ('__init__.py', 'runtime.py'):
        shutil.copyfile(source / 'app/comfy_nodes/h3_execution_context' / name, installed / name)
    shutil.copyfile(source / 'app/model_inventory.py', installed / 'inventory.py')
    output = tmp_path / 'output'
    monkeypatch.setitem(sys.modules, 'folder_paths', SimpleNamespace(
        base_path=str(tmp_path / 'ComfyUI'), get_output_directory=lambda: str(output),
        get_full_path=lambda category, name: None))
    monkeypatch.setitem(sys.modules, 'nodes', SimpleNamespace(NODE_CLASS_MAPPINGS={}))
    monkeypatch.setenv('H3_ROOT', str(tmp_path))
    # No ComfyUI server is needed to exercise the installed on-prompt hook.
    spec = importlib.util.spec_from_file_location('installed_context', installed / '__init__.py',
                                                 submodule_search_locations=[str(installed)])
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, 'installed_context', module)
    spec.loader.exec_module(module)
    graph = {'1': {'class_type': 'Example', 'inputs': {'seed': 42}}}
    data = {'prompt': graph}
    assert module.capture_prompt(data) is data and data['prompt'] is graph
    receipt = json.loads((output / '.h3-execution-context' / (data['prompt_id'] + '.json')).read_text())
    assert receipt['prompt_id'] == data['prompt_id']
    assert receipt['graph_sha256'] == graph_digest(graph)
    assert receipt['stage'] == 'prompt_received'
