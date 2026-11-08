"""Regression tests for GPU API job handling (app/main.py create_job): required nodes, explicit
Studio choices on the Qwen path and the client fingerprint."""
import asyncio
import base64
import hashlib
import json
import os
import tempfile
import uuid
from unittest.mock import patch

import pytest

httpx = pytest.importorskip("httpx")
from app import main as remote_main  # noqa: E402

PICTURE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
ALL_NODES = {"H3CameraGuide", "H3OperatorCamera", "MiniMaxH3AddGuide", "LoraLoaderModelOnly",
             "ApplyMiniMaxH3FirstBlockCache"}
STATE = {"version": 1, "keys": [
    {"t": 0, "orbit": 0, "elevation": 0, "distance": 4, "target": [0, 1, 0], "fov": 45},
    {"t": 4, "orbit": 30, "elevation": 0, "distance": 4, "target": [0, 1, 0], "fov": 45}]}


class FakeComfy:
    def __init__(self, nodes=ALL_NODES):
        self.nodes = set(nodes)
        self.graph = None
        self.queue_count = 0
        self.object_info_calls = 0

    async def release_models_if_idle(self):
        return None

    async def upload_file(self, filename, data, content_type):
        return filename

    async def queue(self, graph, prompt_id=None):
        self.queue_count += 1
        self.graph = graph
        return "comfy-job-1"

    async def object_info(self):
        self.object_info_calls += 1
        return {name: {} for name in self.nodes}


def qwen_plan(role="identity", guide_frames=(), style="realism"):
    return {"schema_version": 1,
            "references": [{"asset_id": "asset-1", "role": role,
                            "fidelity": "fully_preserved", "visual_evidence": []}],
            "style": style, "additional_direction": "",
            "control": {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0},
            "camera_state": None, "guide_frames": list(guide_frames),
            "quality_intent": "final", "audio_intent": "native_stereo_effects_music"}


def form(**extra):
    manifest = {"version": 1, "images": [{"asset_id": "asset-1", "role": extra.pop("role", ""),
                "sha256": hashlib.sha256(PICTURE).hexdigest()}], "videos": [], "audios": []}
    data = {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
            "prompt": "<Picture 1> walks", "control_type": "none", "duration": "5",
            "quality": "quick", "cache_mode": "off"}
    data.update({key: str(value) for key, value in extra.items()})
    return data


def post(data, fake, plan=None, env=None):
    """POST /api/jobs once with one picture; returns (response, director mock)."""
    with tempfile.TemporaryDirectory() as temp, \
            patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{temp}/registry.sqlite3", **(env or {})}), \
            patch.object(remote_main, "comfy", fake), \
            patch.object(remote_main, "missing_model_files", lambda *_args: []), \
            patch.object(remote_main, "LocalQwenDirector") as director:
        director.return_value.plan.return_value = plan or qwen_plan()

        async def run():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/jobs", data=data,
                                         files=[("images", ("hero.png", PICTURE, "image/png"))])
        return asyncio.run(run()), director


# --- camera guide node only when the graph uses the guide video ------------------------------------

@pytest.mark.parametrize("qwen", [False, True])
def test_text_only_camera_does_not_require_guide_node(qwen):
    fake = FakeComfy(nodes=set())
    data = form(camera_state_json=json.dumps(STATE), precompiled_prompt=str(not qwen).lower(),
                emotional_direction="false")
    response, _ = post(data, fake, env={"H3_CAMERA_GUIDE_VIDEO": "0"})
    assert response.status_code == 202, response.text
    assert "330" not in fake.graph


@pytest.mark.parametrize("qwen", [False, True])
def test_guide_video_still_requires_camera_node(qwen):
    fake = FakeComfy(nodes=set())
    data = form(camera_state_json=json.dumps(STATE), precompiled_prompt=str(not qwen).lower(),
                emotional_direction="false")
    response, _ = post(data, fake, env={"H3_CAMERA_GUIDE_VIDEO": "1"})
    assert response.status_code == 503, response.text
    assert "H3CameraGuide" in response.text
    assert fake.queue_count == 0


# --- allow_short_motion_reference -----------------------------------------------------------------

def post_with_motion_video(fake, data, registry_dir):
    video = b"fake-mp4-bytes"
    manifest = json.loads(data["manifest_json"])
    manifest["videos"] = [{"asset_id": "video-1", "role": "Движение",
                           "sha256": hashlib.sha256(video).hexdigest()}]
    data = dict(data, manifest_json=json.dumps(manifest), prompt="<Picture 1> dances like <Video 1>",
                precompiled_prompt="true")
    seen = []

    def fake_prepare(item, request, *, keep_audio, short_edge=None, exact_geometry=False,
                     minimum_duration_seconds=None):
        seen.append(minimum_duration_seconds)
        return item

    with patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{registry_dir}/registry.sqlite3"}), \
            patch.object(remote_main, "comfy", fake), \
            patch.object(remote_main, "prepare_video_reference", fake_prepare):
        async def run():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/jobs", data=data, files=[
                    ("images", ("hero.png", PICTURE, "image/png")),
                    ("videos", ("dance.mp4", video, "video/mp4"))])
        return asyncio.run(run()), seen


def test_short_motion_reference_is_allowed_only_on_request(tmp_path):
    base = form()
    response, seen = post_with_motion_video(FakeComfy(), base, tmp_path)
    assert response.status_code == 202, response.text
    assert seen == [5]
    # The same client job with the field explicitly false keeps its fingerprint (idempotent retry).
    response, _ = post_with_motion_video(FakeComfy(), dict(base, allow_short_motion_reference="false"), tmp_path)
    assert response.status_code == 202, response.text
    # Set to true it is a different job: new fingerprint, no minimum duration.
    response, _ = post_with_motion_video(FakeComfy(), dict(base, allow_short_motion_reference="true"), tmp_path)
    assert response.status_code == 409, response.text
    response, seen = post_with_motion_video(
        FakeComfy(), dict(base, allow_short_motion_reference="true", client_job_id=str(uuid.uuid4())), tmp_path)
    assert response.status_code == 202, response.text
    assert seen == [None]


# --- explicit guide frames on the Qwen path --------------------------------------------------------

def test_explicit_guide_frames_win_over_qwen_and_keep_negative_index():
    fake = FakeComfy()
    plan = qwen_plan(role="scene", guide_frames=[{"asset_id": "asset-1", "frame_idx": 0}])
    response, director = post(form(guide_frames_json=json.dumps([[1, -1]]), emotional_direction="false"),
                              fake, plan=plan)
    assert response.status_code == 202, response.text
    director.return_value.plan.assert_called_once()
    guides = [node for node in fake.graph.values() if node["class_type"] == "MiniMaxH3AddGuide"]
    assert [node["inputs"]["frame_idx"] for node in guides] == [-1]


def test_explicit_guide_frames_require_guide_node_on_qwen_path():
    fake = FakeComfy(nodes=set())
    response, _ = post(form(guide_frames_json=json.dumps([[1, -1]]), emotional_direction="false"), fake)
    assert response.status_code == 503, response.text
    assert "MiniMaxH3AddGuide" in response.text
    assert fake.queue_count == 0


def test_qwen_negative_guide_frame_is_kept():
    from app.director import apply_cinematography_policy, validate_director_plan
    plan = qwen_plan(role="scene", guide_frames=[{"asset_id": "asset-1", "frame_idx": -1}])
    kept = apply_cinematography_policy(plan, "<Picture 1> walks", 124)
    assert kept["guide_frames"] == [{"asset_id": "asset-1", "frame_idx": -1}]
    validate_director_plan(kept, {"asset-1": "image"}, 124)
    out_of_clip = qwen_plan(role="scene", guide_frames=[{"asset_id": "asset-1", "frame_idx": -125}])
    assert apply_cinematography_policy(out_of_clip, "<Picture 1> walks", 124)["guide_frames"] == []


# --- explicit Studio style LoRA on the Qwen path -----------------------------------------------------

def test_explicit_style_adapter_is_applied_when_qwen_style_matches():
    fake = FakeComfy()
    response, _ = post(form(style_adapter="style3d", emotional_direction="false"), fake,
                       plan=qwen_plan(style="animation_3d"), env={"H3_ENABLED_STYLE_ADAPTERS": "style3d"})
    assert response.status_code == 202, response.text
    assert fake.graph["311"]["class_type"] == "LoraLoaderModelOnly"


@pytest.mark.parametrize("enabled,qwen_style,message", [
    ("style3d", "realism", "требует стиль «3D-анимация»"),
    ("", "animation_3d", "недоступна на этом GPU-сервере"),
])
def test_explicit_style_adapter_is_never_silently_dropped(enabled, qwen_style, message):
    fake = FakeComfy()
    response, _ = post(form(style_adapter="style3d", emotional_direction="false"), fake,
                       plan=qwen_plan(style=qwen_style), env={"H3_ENABLED_STYLE_ADAPTERS": enabled})
    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]
    assert fake.queue_count == 0


# --- FirstBlockCache node required when the cache is on ----------------------------------------------

@pytest.mark.parametrize("qwen", [False, True])
@pytest.mark.parametrize("cache_mode", ["safe", "fast"])
def test_cache_node_is_required_before_the_queue(qwen, cache_mode):
    fake = FakeComfy(nodes=ALL_NODES - {"ApplyMiniMaxH3FirstBlockCache"})
    data = form(cache_mode=cache_mode, precompiled_prompt=str(not qwen).lower(), emotional_direction="false")
    response, _ = post(data, fake)
    assert response.status_code == 503, response.text
    assert "ApplyMiniMaxH3FirstBlockCache" in response.text
    assert fake.queue_count == 0
    response, _ = post(dict(data, client_job_id=str(uuid.uuid4())), FakeComfy())
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("field,value", [("mode", "realistic"), ("sampling_profile", "motion_ab")])
def test_non_default_mode_and_sampling_profile_change_the_fingerprint(tmp_path, field, value):
    data = form(precompiled_prompt="true")
    with patch("app.sampling.sampling_available", lambda *_args: True):
        response, _ = post_keep_registry(data, tmp_path)
        assert response.status_code == 202, response.text
        # The default value sent explicitly is the same job.
        default = {"mode": "replace", "sampling_profile": "standard"}[field]
        response, _ = post_keep_registry(dict(data, **{field: default}), tmp_path)
        assert response.status_code == 202, response.text
        response, _ = post_keep_registry(dict(data, **{field: value}), tmp_path)
        assert response.status_code == 409, response.text


def post_keep_registry(data, registry_dir):
    with patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{registry_dir}/registry.sqlite3"}), \
            patch.object(remote_main, "comfy", FakeComfy()):
        async def run():
            transport = httpx.ASGITransport(app=remote_main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/jobs", data=data,
                                         files=[("images", ("hero.png", PICTURE, "image/png"))])
        return asyncio.run(run()), None


def test_cache_off_needs_no_cache_node():
    fake = FakeComfy(nodes=set())
    response, _ = post(form(cache_mode="off", precompiled_prompt="true"), fake)
    assert response.status_code == 202, response.text
    assert "310" not in fake.graph


# --- capabilities and server-update hints ----------------------------------------------------------

def test_camera_guide_capability_checks_the_nodes_the_graph_builds():
    caps = remote_main.capabilities_from_nodes
    assert caps({"H3CameraGuide": {}, "H3CameraPrompt": {}})["camera_guide"]["available"] is False
    assert caps({"H3CameraGuide": {}, "H3OperatorCamera": {}})["camera_guide"]["available"] is True


def test_update_hints_name_the_existing_one_command_installer():
    from pathlib import Path
    from app.contract import gpu_contract_problem
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts" / "h3_new_server.sh").is_file()
    assert "scripts/h3_new_server.sh" in gpu_contract_problem({"api_contract": 1})
    assert "scripts/h3_new_server.sh" in gpu_contract_problem(
        {"api_contract": remote_main.API_CONTRACT, "director_schema_sha256": "other"})
    response, _ = post(form(contract_version=remote_main.API_CONTRACT + 1), FakeComfy())
    assert response.status_code == 422
    assert "scripts/h3_new_server.sh" in response.json()["detail"]
    deploy = (root / "scripts" / "deploy_h3_new_server.sh").read_text()
    assert "/Users/" not in deploy
