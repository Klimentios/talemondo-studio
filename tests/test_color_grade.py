import pytest

from app.color_grade import color_grade_node_inputs, normalize_color_grade
from app.contract import GenerationRequest
from app.graph import build_prompt

GRADE = {"target_mean": [55.0, 12.0, 30.0], "target_std": [18.0, 6.0, 9.0], "strength": 0.6}


def test_normalize_validates_and_defaults():
    grade = normalize_color_grade(GRADE)
    assert grade["strength"] == 0.6 and grade["preserve_luminance"] is False
    assert normalize_color_grade({**GRADE, "strength": 0}) is None
    for bad in ({**GRADE, "target_mean": [200, 0, 0]}, {**GRADE, "target_std": [1, 2]},
                {**GRADE, "strength": 1.5}, {**GRADE, "extra": 1}):
        with pytest.raises(ValueError):
            normalize_color_grade(bad)


def test_graph_routes_decoded_frames_through_color_grade():
    request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
    plain = build_prompt(request, ["a.png"])
    assert "340" not in plain and plain["130"]["inputs"]["images"] == ["122", 0]
    graded = build_prompt(request, ["a.png"], color_grade=GRADE)
    assert graded["340"]["class_type"] == "H3ColorGrade"
    assert graded["340"]["inputs"]["images"] == ["122", 0]
    assert graded["130"]["inputs"]["images"] == ["340", 0]
    assert graded["340"]["inputs"]["target_b_mean"] == 30.0
    assert color_grade_node_inputs(normalize_color_grade(GRADE))["strength"] == 0.6


def test_node_moves_clip_toward_target_uniformly():
    torch = pytest.importorskip("torch")
    from app.comfy_nodes.h3_color_grade.grade import clip_lab_stats, grade_clip, lab_to_rgb, rgb_to_lab
    frames = torch.rand(6, 24, 32, 3) * 0.5  # dark, neutral-ish clip
    target_mean, target_std = (65.0, 15.0, 35.0), (12.0, 5.0, 8.0)
    out = grade_clip(frames, target_mean, target_std, 1.0, chunk=4)
    assert out.shape == frames.shape and float(out.min()) >= 0 and float(out.max()) <= 1
    mean, _ = clip_lab_stats(out)
    assert abs(float(mean[0]) - 65) < 6 and float(mean[2]) > 20  # brighter and warmer
    half = grade_clip(frames, target_mean, target_std, 0.5, chunk=4)
    half_mean, _ = clip_lab_stats(half)
    base_mean, _ = clip_lab_stats(frames)
    assert float(base_mean[2]) < float(half_mean[2]) < float(mean[2])
    rgb = torch.tensor([[1.0, 0.0, 0.0]])
    assert torch.allclose(lab_to_rgb(rgb_to_lab(rgb)), rgb, atol=1e-4)
    assert torch.equal(grade_clip(frames, target_mean, target_std, 0.0), frames)


def test_gpu_api_requires_node_and_routes_grade(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    import asyncio, base64, hashlib, json, uuid
    import app.main as remote_main
    from tests.test_remote_api import FakeComfy

    monkeypatch.setenv("H3_JOB_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    fake = FakeComfy()
    monkeypatch.setattr(remote_main, "comfy", fake)
    monkeypatch.setattr(remote_main, "_CAPABILITY_CACHE", {"at": 0.0, "value": None})
    picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
    manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}], "videos": [], "audios": []}

    def form():
        return {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest), "prompt": "<Picture 1> walks",
                "precompiled_prompt": "true", "control_type": "none", "duration": "5", "quality": "quick",
                "color_grade_json": json.dumps(GRADE)}

    async def exercise():
        transport = httpx.ASGITransport(app=remote_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            caps = (await client.get("/api/capabilities")).json()
            assert caps["color_grade_v1"] is False
            missing = await client.post("/api/jobs", data=form(), files=[("images", ("hero.png", picture, "image/png"))])
            assert missing.status_code == 503 and "H3ColorGrade" in missing.text
            original = fake.object_info
            async def with_node():
                return {**(await original()), "H3ColorGrade": {}}
            fake.object_info = with_node
            remote_main._CAPABILITY_CACHE.update(at=0.0, value=None)
            assert (await client.get("/api/capabilities")).json()["color_grade_v1"] is True
            ok = await client.post("/api/jobs", data=form(), files=[("images", ("hero.png", picture, "image/png"))])
            assert ok.status_code == 202, ok.text
            assert fake.graph["340"]["class_type"] == "H3ColorGrade"
            assert fake.graph["130"]["inputs"]["images"] == ["340", 0]
            assert ok.json()["director"]["color_grade"]["strength"] == 0.6

    asyncio.run(exercise())


def test_mac_worker_sends_grade_only_to_capable_gpu(tmp_path):
    pytest.importorskip("httpx")
    import asyncio, hashlib, json
    from unittest.mock import patch
    from local_studio.store import StudioStore
    from local_studio.worker import HttpGpuTransport

    store = StudioStore(tmp_path)
    path = store.assets_dir / "hero.png"
    path.write_bytes(b"fixture")
    asset = store.add_asset(kind="image", role="Референс", original_name=path.name, mime="image/png",
                            size=7, sha256=hashlib.sha256(b"fixture").hexdigest(), path=path)
    job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]], settings={"color_grade": GRADE})
    assert json.loads(job["settings_json"])["color_grade"]["strength"] == 0.6
    from tests.gpu_caps import CONTRACT_CAPS
    posted, caps = [], {**CONTRACT_CAPS, "color_grade_v1": False}

    class Response:
        def __init__(self, code, payload): self.status_code, self.payload = code, payload
        def json(self): return self.payload
        def raise_for_status(self): pass

    class Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **k): return Response(200, caps)
        async def post(self, url, *, data, files):
            posted.append(data)
            return Response(202, {"job_id": "remote-1"})

    with patch("httpx.AsyncClient", Client):
        with pytest.raises(ValueError, match="H3ColorGrade"):
            asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
        assert not posted
        caps["color_grade_v1"] = True
        asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
        assert json.loads(posted[0]["color_grade_json"])["target_mean"] == GRADE["target_mean"]


def test_film_look_validation_graph_chain_and_node():
    from app.color_grade import normalize_film_look
    look = normalize_film_look({"contrast": 0.2, "grain": 0.4, "grain_size": 2.0, "seed": 5})
    assert look == {"contrast": 0.2, "grain": 0.4, "grain_size": 2.0, "seed": 5}
    assert normalize_film_look({"contrast": 0, "grain": 0}) is None
    with pytest.raises(ValueError):
        normalize_film_look({"grain": 3})
    request = GenerationRequest("<Picture 1> walks", 5, "16:9", "quick", steps=24)
    graph = build_prompt(request, ["a.png"], color_grade=GRADE, film_look=look)
    assert graph["340"]["inputs"]["images"] == ["122", 0]
    assert graph["341"]["class_type"] == "H3FilmLook" and graph["341"]["inputs"]["images"] == ["340", 0]
    assert graph["130"]["inputs"]["images"] == ["341", 0]
    only_film = build_prompt(request, ["a.png"], film_look=look)
    assert only_film["341"]["inputs"]["images"] == ["122", 0] and "340" not in only_film
    torch = pytest.importorskip("torch")
    from app.comfy_nodes.h3_color_grade.film import film_look
    frames = torch.full((4, 32, 48, 3), 0.5)
    out = film_look(frames, 0.0, 0.5, 2.0, seed=3, chunk=2)
    assert out.shape == frames.shape and float((out - frames).abs().mean()) > 0.005
    assert not torch.equal(out[0], out[1])                      # grain moves between frames
    assert torch.equal(out, film_look(frames, 0.0, 0.5, 2.0, seed=3))  # deterministic per seed
    ramp = torch.linspace(0, 1, 11).view(1, 1, 11, 1).expand(1, 1, 11, 3)
    curved = film_look(ramp, 0.3, 0.0, 1.2, 0)
    assert float(curved[0, 0, 2, 0]) < float(ramp[0, 0, 2, 0]) and float(curved[0, 0, 8, 0]) > float(ramp[0, 0, 8, 0])


def test_fingerprint_includes_grade_and_look_only_when_present(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    import asyncio, base64, hashlib, json, uuid
    import app.main as remote_main
    from tests.test_remote_api import FakeComfy

    monkeypatch.setenv("H3_JOB_REGISTRY_PATH", str(tmp_path / "registry.sqlite3"))
    fake = FakeComfy()
    original = fake.object_info

    async def with_nodes():
        return {**(await original()), "H3ColorGrade": {}, "H3FilmLook": {}}
    fake.object_info = with_nodes
    monkeypatch.setattr(remote_main, "comfy", fake)
    monkeypatch.setattr(remote_main, "_CAPABILITY_CACHE", {"at": 0.0, "value": None})
    picture = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
    manifest = {"version": 1, "images": [{"asset_id": "asset-1", "sha256": hashlib.sha256(picture).hexdigest()}], "videos": [], "audios": []}
    look = {"contrast": 0.2, "grain": 0.4, "grain_size": 2.0, "seed": 5}

    def form(job_id, **extra):
        return {"client_job_id": job_id, "manifest_json": json.dumps(manifest), "prompt": "<Picture 1> walks",
                "precompiled_prompt": "true", "duration": "5", "quality": "quick", **extra}

    async def post(client, data):
        return await client.post("/api/jobs", data=data, files=[("images", ("hero.png", picture, "image/png"))])

    async def exercise():
        transport = httpx.ASGITransport(app=remote_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            plain_id, graded_id = str(uuid.uuid4()), str(uuid.uuid4())
            assert (await post(client, form(plain_id))).status_code == 202
            plain_hash = remote_main._registry().get(plain_id)["request_hash"]
            # Resubmitting the same plain job is idempotent; adding a grade under its ID is a conflict.
            assert (await post(client, form(plain_id))).status_code == 202
            conflict = await post(client, form(plain_id, color_grade_json=json.dumps(GRADE)))
            assert conflict.status_code == 409
            graded = form(graded_id, color_grade_json=json.dumps(GRADE), film_look_json=json.dumps(look))
            assert (await post(client, graded)).status_code == 202
            assert (await post(client, graded)).status_code == 202
            assert (await post(client, form(graded_id, color_grade_json=json.dumps(GRADE)))).status_code == 409
            assert remote_main._registry().get(graded_id)["request_hash"] != plain_hash

    asyncio.run(exercise())
