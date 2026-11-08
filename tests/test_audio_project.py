from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.audio_project import make_router
from local_studio.store import StudioStore


def _project() -> dict:
    return {
        "version": 1, "title": "Сцена 1", "mode": "video", "source": {"kind": "none", "id": ""},
        "duration": 15, "output_format": "wav", "lip_sync": False, "lip_sync_target": "",
        "lines": [{"id": "line-1", "speaker": "Герой", "text": "Привет", "start": 0,
                   "voice_mode": "preset", "voice_asset_id": "", "voice_description": "", "direction": "", "language": "ru"}],
        "mix": {"source_level": 1, "speech_level": 1, "music_level": .55,
                "effects_level": .7, "music_asset_id": "", "effects_asset_id": "",
                "music_prompt": "", "effects_prompt": "", "ducking": True},
    }


def test_audio_draft_survives_request_cycle_without_submitting_gpu_job(tmp_path):
    store = StudioStore(tmp_path)
    app = FastAPI()
    app.include_router(make_router(store))
    client = TestClient(app)
    assert client.get('/api/audio/draft').json() == {"project": None}
    project = _project()
    assert client.put('/api/audio/draft', json={"project": project}).json() == {"saved": True}
    assert client.get('/api/audio/draft').json() == {"project": project}
    assert client.get('/api/audio/capabilities').json()["tts"] is False


def test_audio_draft_rejects_missing_reference_and_audio_only_lip_sync(tmp_path):
    store = StudioStore(tmp_path)
    app = FastAPI()
    app.include_router(make_router(store))
    client = TestClient(app)
    project = _project()
    project["lines"][0]["voice_mode"] = "reference"
    project["lines"][0]["voice_asset_id"] = "missing"
    assert client.put('/api/audio/draft', json={"project": project}).status_code == 422
    project["lines"][0]["voice_asset_id"] = ""
    project["mode"] = "audio"
    project["lip_sync"] = True
    assert client.put('/api/audio/draft', json={"project": project}).status_code == 422


def test_audio_draft_saves_lyrics_before_style(tmp_path):
    store = StudioStore(tmp_path)
    app = FastAPI()
    app.include_router(make_router(store))
    client = TestClient(app)
    project = _project()
    project["mix"]["music_lyrics"] = "Первые строки песни"
    assert client.put('/api/audio/draft', json={"project": project}).status_code == 200
    assert client.get('/api/audio/draft').json()["project"]["mix"]["music_lyrics"] == "Первые строки песни"


def test_audio_draft_validates_music_controls(tmp_path):
    store = StudioStore(tmp_path)
    app = FastAPI()
    app.include_router(make_router(store))
    client = TestClient(app)
    project = _project()
    project["mix"].update(music_prompt="Piano", music_bpm=120, music_keyscale="Am",
                          music_timesignature="4", music_seed=42)
    assert client.put('/api/audio/draft', json={"project": project}).status_code == 200
    project["mix"]["music_bpm"] = 301
    assert client.put('/api/audio/draft', json={"project": project}).status_code == 422
