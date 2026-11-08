"""Studio «Открыть в ComfyUI»: Studio redirect + the h3-studio-bridge node that keeps every graph."""
import json
import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.comfy_nodes import h3_studio_bridge as bridge
from local_studio.routes import jobs as job_routes

GRAPH = {"1": {"class_type": "KSampler", "inputs": {"seed": 7}}}


def studio(jobs, comfy_url="http://127.0.0.1:18188"):
    import tempfile
    from pathlib import Path
    from local_studio.comfy_schema import ObjectInfoCache
    from local_studio.job_graphs import JobGraphs
    ctx = SimpleNamespace(store=SimpleNamespace(get_job=jobs.get), comfy_url=comfy_url,
                          comfy_schema=ObjectInfoCache(comfy_url), job_graphs=JobGraphs(Path(tempfile.mkdtemp()), comfy_url))
    app = FastAPI()
    app.include_router(job_routes.make_router(ctx))
    return TestClient(app, follow_redirects=False)


def test_redirect_opens_the_remote_graph_in_comfyui():
    remote = str(uuid.uuid4())
    response = studio({"j1": {"job_id": "j1", "remote_job_id": remote}}).get("/api/jobs/j1/comfyui")
    assert response.status_code == 307
    assert response.headers["location"] == f"http://127.0.0.1:18188/?h3_job={remote}"


def test_redirect_refuses_jobs_comfyui_does_not_have():
    client = studio({"j1": {"job_id": "j1", "remote_job_id": None}})
    assert client.get("/api/jobs/j1/comfyui").status_code == 409
    assert client.get("/api/jobs/missing/comfyui").status_code == 404
    assert studio({"j1": {"job_id": "j1", "remote_job_id": "x"}}, "").get("/api/jobs/j1/comfyui").status_code == 503


def test_bridge_keeps_graph_under_the_id_comfyui_will_use(tmp_path):
    given = str(uuid.uuid4())
    assert bridge.save_graph({"prompt": GRAPH, "prompt_id": given}, tmp_path)["prompt_id"] == given
    minted = bridge.save_graph({"prompt": GRAPH}, tmp_path)["prompt_id"]
    for uid in (given, minted):
        assert bridge.find_graph(uid, tmp_path) == {"prompt": GRAPH, "source": "file"}


def test_bridge_never_breaks_a_prompt(tmp_path):
    odd = {"prompt": GRAPH, "prompt_id": "NOT-A-UUID"}
    assert bridge.save_graph(odd, tmp_path) is odd and not list(tmp_path.iterdir())
    assert bridge.save_graph({"number": 1}, tmp_path) == {"number": 1}
    blocked = tmp_path / "file"
    blocked.write_text("")
    data = {"prompt": GRAPH}
    assert bridge.save_graph(data, blocked) is data


def test_bridge_falls_back_to_comfyui_history_and_queue(tmp_path):
    old, queued = str(uuid.uuid4()), str(uuid.uuid4())
    queue = SimpleNamespace(
        get_history=lambda prompt_id: {old: {"prompt": (1, old, GRAPH, {}, [])}} if prompt_id == old else {},
        get_current_queue_volatile=lambda: ([], [(2, queued, GRAPH, {}, [])]))
    assert bridge.find_graph(old, tmp_path, queue)["source"] == "history"
    assert bridge.find_graph(queued, tmp_path, queue)["source"] == "queue"
    assert bridge.find_graph(str(uuid.uuid4()), tmp_path, queue) is None


def test_prune_keeps_newest(tmp_path):
    import os
    for n in range(5):
        path = tmp_path / f"{n}.json"
        path.write_text(json.dumps({"prompt": GRAPH}))
        os.utime(path, (n, n))
    bridge.prune(tmp_path, keep=2)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["3.json", "4.json"]


# --- edits sent back from ComfyUI (A2) --------------------------------------------------------------

from app.graph_edit import edit_problems, graph_changes  # noqa: E402

SOURCE = {"10": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_h3_ref2va_bf16.safetensors"}},
          "20": {"class_type": "KSampler", "inputs": {"seed": 7, "steps": 24, "model": ["10", 0]}}}


def edited(**inputs):
    graph = json.loads(json.dumps(SOURCE))
    graph["20"]["inputs"].update(inputs)
    return graph


def test_changes_list_literals_links_and_nodes():
    graph = edited(seed=8, model=["30", 0])
    graph["30"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["10", 0]}}
    changes = graph_changes(SOURCE, graph)
    assert [(c["input"], c["before"], c["after"]) for c in changes["changed"]] == [
        ("model", "← узел 10", "← узел 30"), ("seed", 7, 8)]
    assert changes["added"] == [{"node": "30", "class_type": "LoraLoaderModelOnly"}]


def test_any_model_allowed_unchanged_graphs_refused():
    assert edit_problems(SOURCE, edited(seed=8)) == []
    turbo = edited(seed=8)
    turbo["30"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"lora_name": "minimax_h3_dmd_8step_turbo.safetensors", "model": ["10", 0]}}
    assert edit_problems(SOURCE, turbo) == []  # public edition: any model or LoRA
    assert "не изменён" in edit_problems(SOURCE, SOURCE)[0]
    assert edit_problems(SOURCE, {"1": {"inputs": {}}}) == ["Граф из ComfyUI пустой или не в API-формате"]


def test_bridge_keeps_edits_per_source(tmp_path):
    source = str(uuid.uuid4())
    edit_id = bridge.save_edit(source, edited(seed=8), tmp_path)
    assert bridge.list_edits(source, tmp_path)[0]["edit_id"] == edit_id
    assert bridge.list_edits(str(uuid.uuid4()), tmp_path) == []
    assert bridge.read_edit(edit_id, tmp_path)["source_prompt_id"] == source
    import pytest
    with pytest.raises(ValueError):
        bridge.save_edit(source, {"1": "x"}, tmp_path)


class EditComfy:
    def __init__(self, graph, source_id):
        self.graphs = {f"graph/{source_id}": {"prompt": SOURCE},
                       "edited/e1": {"source_prompt_id": source_id, "prompt": graph}}
        self.queued = []

    async def bridge(self, path):
        return self.graphs.get(path)

    async def queue(self, graph, prompt_id=None):
        self.queued.append(graph)
        return prompt_id


def post_graph_job(fake, body, tmp_path):
    import asyncio
    import os
    from unittest.mock import patch
    import httpx
    from app import main as remote_main
    with patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": str(tmp_path / "registry.sqlite3")}), \
            patch.object(remote_main, "comfy", fake):
        async def run():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/graph-jobs", json=body)
        return asyncio.run(run())


def test_gpu_api_queues_the_edit_exactly_and_is_idempotent(tmp_path):
    source = str(uuid.uuid4())
    fake = EditComfy(edited(seed=8), source)
    body = {"source_job_id": source, "edit_id": "e1", "client_job_id": "studio-1"}
    first = post_graph_job(fake, body, tmp_path)
    assert first.status_code == 202, first.text
    assert fake.queued == [edited(seed=8)]
    assert first.json()["director"]["comfyui_edit"]["changed"][0]["input"] == "seed"
    fake.graphs[f"graph/{source}"] = {"prompt": SOURCE}
    again = post_graph_job(fake, body, tmp_path)
    assert again.json()["job_id"] == first.json()["job_id"] and len(fake.queued) == 1


def test_gpu_api_queues_model_swap_and_refuses_foreign_source(tmp_path):
    source = str(uuid.uuid4())
    swap = edited()
    swap["10"]["inputs"]["unet_name"] = "minimax_h3_ref2va_int8_convrot.safetensors"
    fake = EditComfy(swap, source)
    response = post_graph_job(fake, {"source_job_id": source, "edit_id": "e1", "client_job_id": "s"}, tmp_path)
    assert response.status_code == 202, response.text
    assert fake.queued and fake.queued[0]["10"]["inputs"]["unet_name"].endswith("int8_convrot.safetensors")
    other = post_graph_job(EditComfy(edited(seed=8), source),
                           {"source_job_id": str(uuid.uuid4()), "edit_id": "e1", "client_job_id": "s2"}, tmp_path)
    assert other.status_code == 404


# --- agent edits without the editor (MCP job_graph_edit) -------------------------------------------

from app.graph_edit import apply_settings  # noqa: E402

NODE_INFO = {
    "KSampler": {"input": {"required": {"seed": ["INT", {"min": 0, "max": 100}], "steps": ["INT", {"min": 1, "max": 200}],
                                        "model": ["MODEL"]}}},
    "UNETLoader": {"input": {"required": {"unet_name": [["minimax_h3_ref2va_bf16.safetensors", "minimax_h3_ref2va_int8_convrot.safetensors"]]}}},
}


def test_apply_settings_checks_schema():
    import pytest
    assert apply_settings(SOURCE, [{"node": "20", "input": "steps", "value": 30}], NODE_INFO)["20"]["inputs"]["steps"] == 30
    for bad, words in (([{"node": "20", "input": "seed", "value": 101}], "вне"),
                       ([{"node": "20", "input": "seed", "value": "7"}], "число"),
                       ([{"node": "20", "input": "model", "value": "x"}], "связи"),
                       ([{"node": "20", "input": "cfg", "value": 1}], "нет в схеме"),
                       ([{"node": "99", "input": "seed", "value": 1}], "нет в графе"),
                       ([{"node": "10", "input": "unet_name", "value": "other.safetensors"}], "не из списка"),
                       ([], "Нет изменений")):
        with pytest.raises(ValueError, match=words):
            apply_settings(SOURCE, bad, NODE_INFO)
    swapped = apply_settings(SOURCE, [{"node": "10", "input": "unet_name",
                                       "value": "minimax_h3_ref2va_int8_convrot.safetensors"}], NODE_INFO)
    assert edit_problems(SOURCE, swapped) == []


def test_studio_agent_edit_is_saved_in_comfyui(monkeypatch):
    import httpx
    remote = str(uuid.uuid4())
    saved = []

    def handler(request):
        path = request.url.path
        if path == f"/h3/studio/graph/{remote}":
            return httpx.Response(200, json={"prompt": SOURCE})
        if path.startswith("/object_info/"):
            name = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={name: NODE_INFO[name]})
        if path == "/h3/studio/edited":
            saved.append(json.loads(request.content))
            return httpx.Response(200, json={"edit_id": "e1"})
        return httpx.Response(404)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    client = studio({"j1": {"job_id": "j1", "remote_job_id": remote}}, "http://comfy.test")
    ok = client.post("/api/jobs/j1/comfyui-edits", json={"set": [{"node": "20", "input": "seed", "value": 9}]})
    assert ok.status_code == 201, ok.text
    assert ok.json()["changes"]["changed"] == [{"node": "20", "class_type": "KSampler", "input": "seed", "before": 7, "after": 9}]
    assert saved == [{"source_prompt_id": remote, "prompt": edited(seed=9)}]
    swap = client.post("/api/jobs/j1/comfyui-edits", json={"set": [
        {"node": "10", "input": "unet_name", "value": "minimax_h3_ref2va_int8_convrot.safetensors"}]})
    assert swap.status_code == 201 and len(saved) == 2
    assert client.get("/api/jobs/j1/graph").json()["graph"] == SOURCE


def test_studio_agent_structural_edit_keeps_job_models(monkeypatch):
    """ops (app/graph_ops.py) on a job graph: a node added and wired against the full /object_info;
    a LoRA may be added too (public edition: any model)."""
    import httpx
    remote = str(uuid.uuid4())
    info = {**NODE_INFO,
            "KSampler": {**NODE_INFO["KSampler"], "output": ["LATENT"]}, "UNETLoader": {**NODE_INFO["UNETLoader"], "output": ["MODEL"]},
            "ModelSamplingSD3": {"input": {"required": {"model": ["MODEL"], "shift": ["FLOAT", {"default": 3.0, "min": 0, "max": 100}]}},
                                 "output": ["MODEL"]},
            "LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL"], "lora_name": [["style.safetensors"]],
                                                           "strength_model": ["FLOAT", {"default": 1.0}]}}, "output": ["MODEL"]}}
    saved = []

    def handler(request):
        path = request.url.path
        if path == f"/h3/studio/graph/{remote}":
            return httpx.Response(200, json={"prompt": SOURCE})
        if path == "/object_info":
            return httpx.Response(200, json=info)
        if path == "/h3/studio/edited":
            saved.append(json.loads(request.content))
            return httpx.Response(200, json={"edit_id": f"e{len(saved)}"})
        return httpx.Response(404)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    client = studio({"j1": {"job_id": "j1", "remote_job_id": remote}}, "http://comfy.test")
    ok = client.post("/api/jobs/j1/comfyui-edits", json={"ops": [
        {"op": "add_node", "class_type": "ModelSamplingSD3", "inputs": {"model": ["10", 0], "shift": 5}, "ref": "ms"},
        {"op": "link", "node": "20", "input": "model", "from": ["$ms", 0]}]})
    assert ok.status_code == 201, ok.text
    assert saved[0]["prompt"]["20"]["inputs"]["model"] == ["21", 0]
    assert saved[0]["prompt"]["21"]["inputs"] == {"model": ["10", 0], "shift": 5.0}
    lora = client.post("/api/jobs/j1/comfyui-edits", json={"ops": [{"op": "add_lora", "lora_name": "style.safetensors"}]})
    assert lora.status_code == 201 and len(saved) == 2
    wrong = client.post("/api/jobs/j1/comfyui-edits", json={"ops": [{"op": "link", "node": "20", "input": "model", "from": ["20", 0]}]})
    assert wrong.status_code == 422 and "LATENT" in wrong.text and len(saved) == 2


def test_workflow_inbox_keeps_named_api_graphs(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("bridge_wf", "app/comfy_nodes/h3_studio_bridge/__init__.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    graph = {"1": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}}
    item = bridge.save_workflow("Мой граф", graph, tmp_path)
    assert bridge.list_workflows(tmp_path)[0] | {"saved_at": None} == {"id": item, "name": "Мой граф", "saved_at": None, "nodes": 1, "studio_id": None, "origin": None}
    assert bridge.read_workflow(item, tmp_path)["prompt"] == graph
    assert bridge.read_workflow("../x", tmp_path) is None
    import pytest
    with pytest.raises(ValueError):
        bridge.save_workflow("x", {"nodes": []}, tmp_path)


def test_workflow_inbox_keeps_studio_id_for_round_trip(tmp_path):
    import pytest
    from app.comfy_nodes.h3_studio_bridge import list_workflows, read_workflow, save_workflow
    graph = {"1": {"class_type": "SaveImage", "inputs": {}}}
    item = save_workflow("W", graph, tmp_path, "wf_0123456789ab")
    assert read_workflow(item, tmp_path)["studio_id"] == "wf_0123456789ab"
    assert list_workflows(tmp_path)[0]["studio_id"] == "wf_0123456789ab"
    assert "studio_id" not in read_workflow(save_workflow("N", graph, tmp_path), tmp_path)
    with pytest.raises(ValueError):
        save_workflow("X", graph, tmp_path, "../../etc")


def test_workflow_opened_from_studio_is_marked_and_validated(tmp_path):
    list_workflows, save_workflow = bridge.list_workflows, bridge.save_workflow
    graph = {"1": {"class_type": "SaveImage", "inputs": {}}}
    save_workflow("N", graph, tmp_path, "wf_0123456789ab", "studio")
    assert list_workflows(tmp_path)[0]["origin"] == "studio"
    import pytest
    with pytest.raises(ValueError):
        save_workflow("N", graph, tmp_path, None, "elsewhere")
