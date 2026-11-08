"""«Превиз»: Blockout staging reaches the Studio only through allowed actions and as a grey «Композиция» in the shot's aspect."""
import base64
import io

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from local_studio import previs


class Store:
    def __init__(self, root):
        self.assets_dir, self.saved = root, None

    def add_asset(self, **asset):
        self.saved = asset
        return {"asset_id": "a1", "role": asset["role"]}


def client(tmp_path, monkeypatch, aspect="9:16", shot=None):
    sent = []
    frame = io.BytesIO()
    Image.new("RGB", (960, 540), (40, 80, 200)).save(frame, format="PNG")

    async def fake(action, params=None):
        if action not in previs.ACTIONS:
            raise previs.HTTPException(422, "не поддерживается")
        sent.append((action, params or {}))
        if action == "screenshot":
            return {"imageBase64": base64.b64encode(frame.getvalue()).decode()}
        return {"shot": {"aspect": aspect, **(shot or {})}} if action == "get_state" else {"ok": True}

    monkeypatch.setattr(previs, "call", fake)
    store = Store(tmp_path)
    app = FastAPI()
    app.include_router(previs.make_router(store))
    return TestClient(app), store, sent


def test_snapshot_is_a_grey_layout_in_the_shot_aspect(tmp_path, monkeypatch):
    c, store, sent = client(tmp_path, monkeypatch)
    assert c.post("/api/previs/snapshot", json={"name": "wp 1/shot", "time": 3}).status_code == 201
    assert store.saved["role"] == "Композиция" and store.saved["original_name"] == "previs-wp 1-shot.png"
    image = Image.open(store.saved["path"])
    assert image.size == (304, 540) and image.mode == "L"  # 9:16 cut from the 960×540 still, grey
    assert ("set_time", {"t": 3.0}) in sent
    view = Image.open(io.BytesIO(c.get("/api/previs/frame").content))
    assert view.size == (304, 540) and view.mode != "L"  # the agent sees the same frame, in colour


def test_only_composition_and_known_actions(tmp_path, monkeypatch):
    c, store, _ = client(tmp_path, monkeypatch)
    assert c.post("/api/previs/snapshot", json={"role": "Кадр сцены"}).status_code == 422  # grey dummies as a start frame
    assert c.post("/api/previs/call", json={"action": "set_reference", "params": {"videoPath": "/etc/passwd"}}).status_code == 422
    assert c.post("/api/previs/call", json={"action": "screenshot"}).json()["screenshot"] is True


def test_moving_camera_becomes_a_camera_video(tmp_path, monkeypatch):
    marks = [{"time": 0}, {"time": 2}]
    c, store, sent = client(tmp_path, monkeypatch, shot={"duration": 2, "cameraMarks": marks})
    got = c.post("/api/previs/motion", json={"name": "wp 1", "fps": 4})
    assert got.status_code == 201 and got.json()["duration_s"] == 2
    assert store.saved["kind"] == "video" and store.saved["role"] == "Камера" and store.saved["mime"] == "video/mp4"
    assert [p["t"] for a, p in sent if a == "set_time"] == [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2, 0]
    assert store.saved["path"].read_bytes()[4:8] == b"ftyp"


def test_a_still_camera_needs_no_video(tmp_path, monkeypatch):
    c, _, _ = client(tmp_path, monkeypatch, shot={"duration": 5, "cameraMarks": [{"time": 0}]})
    assert "хватит «Композиции»" in c.post("/api/previs/motion", json={}).json()["detail"]
    c, _, _ = client(tmp_path, monkeypatch, shot={"duration": 20, "cameraMarks": [{}, {}]})
    assert "1–15 с" in c.post("/api/previs/motion", json={}).json()["detail"]
