"""Seed hunt (app/h3graph/seed_hunt.py): previews are pass 1 kept in an LQ package, finish is pass 2 of it."""
import copy
import json
import uuid
from pathlib import Path

import pytest

from app.h3graph import seed_hunt
from app.h3graph.hires import UPSCALE
from app.h3graph.nodes import Node

GOLDEN = json.loads((Path(__file__).with_name("golden") / "pictures_precompiled_graph.json").read_text())
HUNT = {"role": "preview", "hunt_id": str(uuid.uuid4()), "index": 2}


def reachable(graph: dict) -> set[str]:
    """Nodes ComfyUI runs: everything the save node depends on."""
    seen, stack = set(), [Node.SAVE]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        stack += [v[0] for v in graph[key]["inputs"].values() if isinstance(v, list) and len(v) == 2 and v[0] in graph]
    return seen


def test_preview_is_pass_one_at_half_size_kept_in_a_package():
    graph = seed_hunt.preview_graph(copy.deepcopy(GOLDEN), HUNT)
    save = graph[seed_hunt.PACKAGE]
    assert save["class_type"] == "MiniMaxH3LQPackageSave"
    assert save["inputs"]["samples"] == [Node.SAMPLE, 0]
    assert save["inputs"]["project"] + "/" + save["inputs"]["filename_prefix"] == seed_hunt.package_rel(HUNT)
    assert graph[Node.DECODE_VIDEO]["inputs"]["samples"] == [seed_hunt.PACKAGE, 0]
    assert graph[Node.REF2VA]["inputs"]["width"] * 2 == GOLDEN[Node.REF2VA]["inputs"]["width"]
    assert graph[Node.SCHEDULER]["inputs"]["steps"] == GOLDEN[Node.SCHEDULER]["inputs"]["steps"]  # every step
    assert UPSCALE not in graph and Node.SAMPLE in reachable(graph)


def test_finish_runs_only_pass_two_of_the_saved_preview():
    hunt = {**HUNT, "role": "finish"}
    graph = seed_hunt.finish_graph(copy.deepcopy(GOLDEN), hunt)
    load = graph[seed_hunt.PACKAGE]["inputs"]
    assert json.loads(load["selection_json"])["packages"] == [seed_hunt.package_rel(hunt)]
    assert graph[UPSCALE]["inputs"]["samples"] == [seed_hunt.PACKAGE, 0]
    assert graph[UPSCALE]["inputs"]["positive"] == [seed_hunt.PACKAGE, 1]
    runs = reachable(graph)
    assert Node.SAMPLE not in runs and Node.REF2VA not in runs and Node.TEXT_ENCODER not in runs
    assert {seed_hunt.PACKAGE, UPSCALE, "363"} <= runs


def test_parse_is_strict():
    assert seed_hunt.parse("") is None
    assert seed_hunt.parse(json.dumps(HUNT)) == HUNT
    for bad in ({**HUNT, "role": "x"}, {**HUNT, "hunt_id": "nope"}, {**HUNT, "index": 7}, {**HUNT, "index": "1"}):
        with pytest.raises(ValueError):
            seed_hunt.parse(json.dumps(bad))


# --- Studio: previews and finish are ordinary queue jobs ---------------------------------------------

def studio_with_job(tmp_path, settings=None, state="completed"):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from local_studio import seed_hunt as routes
    from local_studio.store import StudioStore
    store = StudioStore(tmp_path)
    with store.connect() as db:
        db.execute("""INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,manifest_json,mapping_json)
                      VALUES ('src',?,'p','p',?,'{"images":[],"videos":[],"audios":[]}','{}')""",
                   (state, json.dumps(settings or {"seed": 77, "steps": 25})))
    ctx = SimpleNamespace(store=store, gpu_backoff=SimpleNamespace(ok=lambda: None))
    app = FastAPI()
    app.include_router(routes.make_router(ctx))
    return store, TestClient(app)


def test_previews_then_finish_are_queued_with_their_seeds(tmp_path):
    store, client = studio_with_job(tmp_path)
    started = client.post("/api/jobs/src/seed-hunt", json={"count": 3}).json()
    previews = [store.get_job(job_id) for job_id in started["jobs"]]
    settings = [json.loads(job["settings_json"]) for job in previews]
    assert [job["state"] for job in previews] == ["queued_local"] * 3
    assert settings[0]["seed"] == 77 and len({s["seed"] for s in settings}) == 3
    assert [s["seed_hunt"]["index"] for s in settings] == [1, 2, 3]
    assert {s["seed_hunt"]["hunt_id"] for s in settings} == {started["hunt_id"]}
    second = started["jobs"][1]
    assert client.post(f"/api/jobs/{second}/seed-hunt/finish").status_code == 409  # not ready yet
    store.transition(second, "uploading"); store.transition(second, "queued_gpu", remote_job_id="r")
    store.transition(second, "running"); store.transition(second, "downloading")
    store.transition(second, "completed", output_path=str(tmp_path / "x.mp4"))
    finish = client.post(f"/api/jobs/{second}/seed-hunt/finish").json()
    done = json.loads(store.get_job(finish["job_id"])["settings_json"])
    assert done["seed"] == settings[1]["seed"] and done["seed_hunt"]["role"] == "finish" and done["seed_hunt"]["index"] == 2
    listed = client.get(f"/api/seed-hunts/{started['hunt_id']}").json()["jobs"]
    assert [(item["role"], item["index"]) for item in listed] == [("preview", 1), ("preview", 2), ("preview", 3), ("finish", 2)]
    assert client.post(f"/api/jobs/{second}/seed-hunt", json={}).status_code == 409
    assert client.post("/api/jobs/src/seed-hunt", json={"count": 9}).status_code == 422


def test_worker_sends_the_hunt_to_the_gpu():
    from local_studio.worker import gpu_form_fields
    from tests.test_render_golden import _store_job  # noqa: F401  (golden job shape)
    golden = json.loads((Path(__file__).with_name("golden") / "pictures_studio_job.json").read_text())
    job = {"job_id": "j", "compiled_prompt": golden["compiled_prompt"],
           "settings_json": json.dumps({**golden["settings"], "seed_hunt": {**HUNT, "source_job_id": "src"}}),
           "manifest_json": json.dumps(golden["manifest"]), "mapping_json": "{}"}
    sent = json.loads(gpu_form_fields(job)["seed_hunt_json"])
    assert seed_hunt.parse(json.dumps(sent)) == HUNT
