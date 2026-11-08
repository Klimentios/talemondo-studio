"""Golden render contract: picture-only and movement-video jobs must not drift.

Pins, with fixed inputs, everything that decides what H3 renders:
  * the Mac Studio job (compiled prompt, effective settings, manifest, mapping);
  * the ComfyUI graph from app.graph.build_prompt on the precompiled path;
  * the full Mac worker -> GPU API path (form submission, Qwen plan or its fallback,
    queued graph, Director summary and the client_job_id fingerprint);
  * the Qwen Director request for the picture-only job.

The picture job is the picture-only reference job (replace mode, three pictures,
640x1152, 25 steps, fast cache, dolly camera, director_config, emotional direction).

Regenerate the fixtures only for an intended render change:
  H3_GOLDEN_UPDATE=1 python -m pytest tests/test_render_golden.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import itertools
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.contract import GenerationRequest
from app.director import openai_director_payload
from app.graph import build_prompt
from local_studio.store import StudioStore

try:
    import httpx
    from app import main as remote_main
    from local_studio.worker import HttpGpuTransport
except ImportError:  # pragma: no cover - runtime deps missing
    httpx = None
    remote_main = None

GOLDEN = Path(__file__).with_name("golden")
UPDATE = os.environ.get("H3_GOLDEN_UPDATE") == "1"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
SEED = 4242424242

DOLLY = {"version": 2, "keys": [
    {"t": 0, "orbit": 0, "elevation": 0, "distance": 5.52, "target": [0, 1.1, 0], "fov": 37.85,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 5.52},
    {"t": 4.5, "orbit": 0, "elevation": 0, "distance": 3.2, "target": [0, 1.1, 0], "fov": 37.85,
     "focal_length_mm": 35, "aperture_f": 2.8, "focus_distance_m": 3.2},
]}
DIRECTOR_CONFIG = {
    "lens": {"id": "lens.35", "parameters": {"focal_length_mm": 35, "aperture_f": 2.8}},
    "movement": {"id": "movement.push", "parameters": {"movement": "dolly", "speed": "slow", "subject_lock": True}},
    "lighting": {"id": "lighting.neon", "parameters": {"source": "neon", "contrast": "high"}},
    "style": {"id": "style.realism", "parameters": {"rendering": "photoreal"}},
}
BASE_SETTINGS = {
    "duration": 5, "aspect_ratio": "9:16", "quality": "custom", "mode": "replace",
    "megapixels": 0.7, "steps": 25, "ref_image_size": "max",
    # Older Studio builds always sent motion_control "none"; saved jobs still carry it.
    "motion_control": "none", "style_adapter": "none", "cache_mode": "fast",
    "seed": SEED, "use_video_audio": False, "emotional_direction": True,
}

CASES = {
    "pictures": {
        "prompt": ("ADAM <Picture 1> and MARTIN <Picture 2> meet in the workshop <Picture 3>. "
                   "ADAM laughs and pats MARTIN on the shoulder; MARTIN answers with a tired smile."),
        "assets": [("adam.png", "image", "Персонаж целиком", "ADAM"),
                   ("martin.png", "image", "Лицо героя", "MARTIN"),
                   ("workshop.png", "image", "Окружение", "")],
        "settings": {"camera_state": DOLLY,
                     "camera_optics": {"sensor_format": "full_frame", "focus_mode": "subject_head"},
                     "director_config": DIRECTOR_CONFIG},
    },
    "video": {
        "prompt": "ADAM <Picture 1> repeats the movement and camera of <Video 1> in a snowy yard.",
        "assets": [("adam.png", "image", "Персонаж целиком", "ADAM"),
                   ("move.mp4", "video", "Движение и камера", "")],
        # Studio drops its camera path when a video drives the camera.
        "settings": {"camera_state": None, "camera_optics": None, "director_config": DIRECTOR_CONFIG},
        "performer": {"owner": "ADAM", "performer": "the visible person"},
    },
}

_CLEARED_ENV = ("H3_CAMERA_GUIDE_VIDEO", "H3_STATIC_CAMERA_GUIDE", "H3_FRAMING_LOCK", "H3_CACHE_MODE",
                "H3_CAMERA_GUIDE_SHORT_EDGE", "H3_ENABLED_STYLE_ADAPTERS", "H3_STRUCTURAL_CONTROL_ENABLED")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=1) + "\n"


class _Env:
    """Deterministic UUIDs and a clean render environment."""

    def __enter__(self):
        counter = itertools.count(1)
        self._patches = [
            patch.dict(os.environ, {}, clear=False),
            patch("uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))),
        ]
        for item in self._patches:
            item.start()
        for key in _CLEARED_ENV:
            os.environ.pop(key, None)
        return self

    def __exit__(self, *exc):
        for item in reversed(self._patches):
            item.stop()
        return False


def _store_job(root: Path, case: dict) -> tuple[StudioStore, dict]:
    store = StudioStore(root)
    ids, roles, owners = [], {}, {}
    for name, kind, role, owner in case["assets"]:
        data = PNG if kind == "image" else b"\x00\x00\x00\x18ftypmp42 golden movement fixture"
        path = store.assets_dir / name
        path.write_bytes(data)
        mime = "image/png" if kind == "image" else "video/mp4"
        asset = store.add_asset(kind=kind, role="Референс", original_name=name, mime=mime,
                                size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=path)
        ids.append(asset["asset_id"])
        roles[asset["asset_id"]] = role
        if owner:
            owners[asset["asset_id"]] = owner
    videos = [asset_id for asset_id, (_, kind, _, _) in zip(ids, case["assets"]) if kind == "video"]
    performer_bindings = ({videos[0]: {"pairs": [case["performer"]]}}
                          if videos and case.get("performer") else {})
    job = store.create_job(prompt=case["prompt"], asset_ids=ids,
                           settings={**BASE_SETTINGS, **case["settings"]},
                           reference_roles=roles, reference_owners=owners,
                           style_adapters={"character_swap"} if any(asset[1] == "video" for asset in case["assets"])
                           and case["settings"].get("mode", BASE_SETTINGS["mode"]) == "replace" else set(),
                           reference_performers=performer_bindings)
    return store, job


def _studio_record(job: dict) -> dict:
    return {
        "compiled_prompt": job["compiled_prompt"],
        "settings": json.loads(job["settings_json"]),
        "manifest": json.loads(job["manifest_json"]),
        "mapping": json.loads(job["mapping_json"]),
    }


def _precompiled_graph(job: dict) -> dict:
    settings = json.loads(job["settings_json"])
    manifest = json.loads(job["manifest_json"])
    request = GenerationRequest(
        job["compiled_prompt"], settings["duration"], settings["aspect_ratio"], settings["quality"],
        profile=settings["profile"], steps=settings["steps"], seed=settings["seed"],
        megapixels=settings["megapixels"], ref_image_size=settings["ref_image_size"],
    )
    return build_prompt(
        request,
        [f"picture{index}.png" for index, _ in enumerate(manifest["images"], 1)],
        reference_videos=[f"video{index}.mp4" for index, _ in enumerate(manifest["videos"], 1)],
        use_video_audio=settings["use_video_audio"],
        camera_state=settings["camera_state"], camera_optics=settings["camera_optics"],
        guide_frames=[tuple(item) for item in settings.get("guide_frames", [])],
        style_adapter=settings["style_adapter"], cache_mode=settings["cache_mode"],
        color_grade=settings.get("color_grade"), film_look=settings.get("film_look"),
    )


class _FakeComfy:
    NODES = {"MiniMaxH3ReferenceToVideo", "H3CameraGuide", "H3CameraPrompt", "H3OperatorCamera",
             "MiniMaxH3AddGuide", "ApplyMiniMaxH3FirstBlockCache", "H3ColorGrade", "H3FilmLook",
             "LoraLoaderModelOnly"}

    def __init__(self):
        self.graph = None

    async def release_models_if_idle(self):
        return None

    async def upload_file(self, filename, data, content_type):
        return f"uploaded/{filename}"

    async def queue(self, graph, prompt_id=None):
        self.graph = graph
        return "comfy-golden-1"

    @classmethod
    def info(cls):
        """A served GPU lists its installed BF16 baseline files (worker refuses a GPU without them)."""
        from app.render_profiles import PROFILES
        result = {name: {} for name in cls.NODES}
        result["UNETLoader"] = {"input": {"required": {"unet_name": [[PROFILES["bf16"]["unet"]]]}}}
        result["CLIPLoader"] = {"input": {"required": {"clip_name": [[PROFILES["bf16"]["clip"]]]}}}
        return result

    async def object_info(self):
        return self.info()


def _qwen_plan(manifest: dict) -> dict:
    roles = {"Персонаж целиком": "identity", "Лицо героя": "identity", "Окружение": "scene"}
    return {
        "schema_version": 1,
        "references": [{"asset_id": item["asset_id"], "role": roles[item["role"]],
                        "fidelity": "fully_preserved" if roles[item["role"]] == "identity" else "partly_preserved",
                        "visual_evidence": [f"golden detail of {item['role']}"]}
                       for item in manifest["images"]],
        "style": "realism",
        "additional_direction": "ADAM's laugh builds over two beats; MARTIN exhales before his smile.",
        "control": {"type": "none", "source_asset_id": None, "people": 1, "strength": 1.0},
        "camera_state": None, "guide_frames": [], "quality_intent": "final",
        "audio_intent": "native_stereo_effects_music",
    }


def _gpu_submission(store: StudioStore, job: dict, temp: str, qwen: str) -> dict:
    """Run the real worker submit against the real GPU API in-process (ComfyUI faked)."""
    fake = _FakeComfy()
    prepared = []
    director_calls = []
    manifest = json.loads(job["manifest_json"])

    class FakeDirector:
        def plan(self, *args, **kwargs):
            director_calls.append(len(args[1]))
            if qwen == "fails":
                raise RuntimeError("Qwen Director вернула ответ не по JSON-контракту: golden")
            return _qwen_plan(manifest)

    def fake_prepare(item, request, *, keep_audio, short_edge=None, exact_geometry=False,
                     minimum_duration_seconds=None):
        prepared.append({"filename": item[0], "length": request.length, "keep_audio": keep_audio,
                         "short_edge": short_edge, "exact_geometry": exact_geometry})
        return item

    real_client = httpx.AsyncClient

    def asgi_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.ASGITransport(app=remote_main.app), timeout=60, **kwargs)

    old_comfy = remote_main.comfy
    remote_main.comfy = fake
    remote_main._CAPABILITY_CACHE.update(at=0.0, value=None)
    os.environ["H3_JOB_REGISTRY_PATH"] = f"{temp}/registry-{qwen}.sqlite3"
    # Model the API capability explicitly for this replacement fixture. This
    # synthetic declaration is not evidence that any deployed GPU has the LoRA.
    capability_env = patch.dict(os.environ, {"H3_ENABLED_STYLE_ADAPTERS": "character_swap"})
    capability_env.start()
    try:
        with patch.object(remote_main, "LocalQwenDirector", FakeDirector), \
             patch.object(remote_main, "missing_model_files", lambda *_args, **_kwargs: []), \
             patch.object(remote_main, "prepare_video_reference", fake_prepare), \
             patch("httpx.AsyncClient", asgi_client):
            response = asyncio.run(HttpGpuTransport("http://gpu.test").submit(job, store))
        record = remote_main._registry().get(job["job_id"])
    finally:
        capability_env.stop()
        remote_main.comfy = old_comfy
        remote_main._CAPABILITY_CACHE.update(at=0.0, value=None)
    return {"response": response, "graph": fake.graph, "prepared_videos": prepared,
            "qwen_calls": director_calls, "request_hash": record["request_hash"]}


class RenderGoldenTests(unittest.TestCase):
    maxDiff = None

    def check(self, name: str, value: object) -> None:
        path = GOLDEN / f"{name}.json"
        text = _dump(value)
        if UPDATE or not path.exists():
            if not UPDATE:
                self.fail(f"Нет эталона {path}; создай его: H3_GOLDEN_UPDATE=1")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return
        self.assertEqual(path.read_text(encoding="utf-8"), text, f"Эталон {name} изменился")

    def test_custom_cache_store_worker_gpu_and_director_preserve_exact_parameters(self):
        from app.cache_policy import SAFE, CUSTOM_MODE
        from copy import deepcopy
        custom = {**SAFE, "threshold": 0.035, "max_consecutive_hits": 1}
        case = deepcopy(CASES["pictures"])
        case["settings"].update(cache_mode="custom", cache_config=custom)
        async def info(_self):
            result = _FakeComfy.info()
            result["ApplyMiniMaxH3FirstBlockCache"] = {"input": {"required": {
                "mode": [[CUSTOM_MODE]], **{key: [] for key in SAFE}}}}
            return result
        with _Env(), tempfile.TemporaryDirectory() as temp, patch.object(_FakeComfy, "object_info", info):
            store, job = _store_job(Path(temp), case)
            self.assertEqual(json.loads(job["settings_json"])["cache_config"], custom)
            result = _gpu_submission(store, job, temp, "plan")
            self.assertEqual(result["response"]["director"]["cache_execution"]["effective"], custom)
            for key, value in custom.items():
                self.assertEqual(result["graph"]["310"]["inputs"][key], value)

    def test_studio_job_and_precompiled_graph(self):
        for case_name, case in CASES.items():
            with self.subTest(case=case_name), _Env(), tempfile.TemporaryDirectory() as temp:
                _, job = _store_job(Path(temp), case)
                self.check(f"{case_name}_studio_job", _studio_record(job))
                self.check(f"{case_name}_precompiled_graph", _precompiled_graph(job))

    def test_picture_job_qwen_request(self):
        with _Env(), tempfile.TemporaryDirectory() as temp:
            _, job = _store_job(Path(temp), CASES["pictures"])
            manifest = json.loads(job["manifest_json"])
            references = [{"asset_id": item["asset_id"], "kind": "image", "tag": f"Picture {index}",
                           "role_override": {"Персонаж целиком": "identity", "Лицо героя": "identity",
                                             "Окружение": "scene"}[item["role"]],
                           "frames": [b"frame"], "filename": f"picture{index}.png"}
                          for index, item in enumerate(manifest["images"], 1)]
            options = {"requested_style": "auto", "emotional_direction": True, "camera_locked": True}
            if "motion_preference" in inspect.signature(openai_director_payload).parameters:
                options["motion_preference"] = "none"  # what the GPU API always passed with ControlNet off
            payload = openai_director_payload(job["compiled_prompt"], references, model="qwen-golden",
                                              frame_count=125, **options)
            self.check("pictures_qwen_request", payload)

    @unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
    def test_worker_to_gpu_api(self):
        runs = (("pictures", "plan"), ("pictures", "fails"), ("video", "plan"))
        for case_name, qwen in runs:
            with self.subTest(case=case_name, qwen=qwen), _Env(), tempfile.TemporaryDirectory() as temp:
                store, job = _store_job(Path(temp), CASES[case_name])
                result = _gpu_submission(store, job, temp, qwen)
                # Additive execution metadata must describe the real graph; historical recipe,
                # request hash, prompt and every graph input remain under the original golden.
                from app.cache_policy import cache_from_graph
                from app.ab_variant import graph_fingerprint
                director = result["response"]["director"]
                self.assertEqual(director.pop("cache_execution"), cache_from_graph(result["graph"]))
                self.assertEqual(director.pop("execution_record"), {"version": 1, "engine": "h3",
                    "stage": "graph_prepared", **graph_fingerprint(result["graph"])})
                self.check(f"{case_name}_gpu_qwen_{qwen}", result)


if __name__ == "__main__":
    unittest.main()
