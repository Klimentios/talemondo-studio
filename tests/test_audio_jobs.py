from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.audio_jobs import make_router


def _audio(path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=400:duration=1", "-c:a", "pcm_s16le", str(path)],
                   check=True, capture_output=True, timeout=30)


def _video(path):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=24:d=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-t", "1", "-c:v", "mpeg4", "-c:a", "aac", str(path)],
                   check=True, capture_output=True, timeout=30)


def test_audio_model_uses_virtual_environment_interpreter(tmp_path, monkeypatch):
    import venv
    from app.audio_models import _configured_path, _python_imports
    environment = tmp_path / "audio-venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / "bin/python"
    site = subprocess.check_output([str(python), "-c",
        "import site; print(site.getsitepackages()[0])"], text=True).strip()
    Path(site, "studio_audio_environment_probe.py").write_text("READY = True\n")
    monkeypatch.setenv("H3_COSYVOICE_PYTHON", str(python))
    configured = _configured_path("H3_COSYVOICE_PYTHON")
    assert configured == python
    assert _python_imports(configured, "studio_audio_environment_probe")


def test_server_mixes_audio_only_and_recovers_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("H3_AUDIO_JOBS_DIR", str(tmp_path / "jobs"))
    source = tmp_path / "music.wav"
    _audio(source)
    project = {"version": 1, "mode": "audio", "source": {"kind": "none", "id": ""},
               "duration": 1, "output_format": "wav", "lip_sync": False,
               "lines": [], "mix": {"source_level": 1, "speech_level": 1, "music_level": .5,
                                    "effects_level": .5, "music_asset_id": "m1", "effects_asset_id": "",
                                    "music_prompt": "", "effects_prompt": "", "ducking": False}}
    app = FastAPI()
    app.include_router(make_router(object()))
    client = TestClient(app)
    uid = str(uuid.uuid4())
    with source.open("rb") as stream:
        response = client.post("/api/audio/jobs", data={"client_job_id": uid,
                               "project_json": json.dumps(project), "media_ids_json": '["music"]'},
                               files=[("media", ("music.wav", stream, "audio/wav"))])
    assert response.status_code == 202, response.text
    assert client.get(f"/api/audio/by-client/{uid}").json()["job_id"] == uid
    assert client.get(f"/api/audio/jobs/{uid}").json()["status"] == "success"
    output = client.get(f"/api/audio/jobs/{uid}/file")
    assert output.status_code == 200 and len(output.content) > 1000
    with source.open("rb") as stream:
        duplicate = client.post("/api/audio/jobs", data={"client_job_id": uid,
                                "project_json": json.dumps(project), "media_ids_json": '["music"]'},
                                files=[("media", ("music.wav", stream, "audio/wav"))])
    assert duplicate.status_code == 409


def test_server_rejects_uninstalled_model_before_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("H3_AUDIO_JOBS_DIR", str(tmp_path / "jobs"))
    app = FastAPI()
    app.include_router(make_router(object()))
    project = {"version": 1, "mode": "audio", "source": {"kind": "none", "id": ""},
               "duration": 1, "output_format": "wav", "lip_sync": False,
               "lines": [{"text": "Привет", "start": 0, "voice_mode": "preset"}],
               "mix": {"source_level": 0, "speech_level": 1, "music_level": 0,
                       "effects_level": 0, "music_prompt": "", "effects_prompt": "", "ducking": False}}
    response = TestClient(app).post("/api/audio/jobs", data={"client_job_id": str(uuid.uuid4()),
                                     "project_json": json.dumps(project), "media_ids_json": "[]"})
    assert response.status_code == 422
    assert "не установлена" in response.json()["detail"]


def test_generated_music_is_mixed_without_uploaded_music(tmp_path, monkeypatch):
    from app import audio_jobs
    generated = tmp_path / "generated-music.wav"
    _audio(generated)
    calls = []
    def fake_music(*, prompt, lyrics, duration, output, bpm, keyscale, timesignature, seed):
        calls.append((prompt, lyrics, duration, output, bpm, keyscale, timesignature, seed))
        output.write_bytes(generated.read_bytes())
        return output
    monkeypatch.setattr(audio_jobs, "generate_music", fake_music)
    project = {"version": 1, "mode": "audio", "source": {"kind": "none", "id": ""},
               "duration": 10, "output_format": "wav", "lip_sync": False, "lines": [],
               "mix": {"source_level": 0, "speech_level": 1, "music_level": 1,
                       "effects_level": 0, "music_asset_id": "", "effects_asset_id": "",
                       "music_prompt": "Мягкий русский синтпоп", "music_lyrics": "[Припев]\nМой новый день",
                       "music_bpm": 96, "music_keyscale": "Am", "music_timesignature": "4", "music_seed": 42,
                       "effects_prompt": "", "ducking": False}}
    audio_jobs.validate_render(project, {"audio_mix": True, "tts": False,
                         "lip_sync": False, "music_generation": True, "music_controls": True,
                         "effects_generation": False})
    output = audio_jobs._render(project, {}, tmp_path)
    assert calls[0][0] == project["mix"]["music_prompt"]
    assert calls[0][1] == project["mix"]["music_lyrics"]
    assert calls[0][2] == 10 and output.is_file()
    assert calls[0][4:] == (96, "Am", "4", 42)
    assert audio_jobs.has_audio(output)


def test_music_controls_are_rejected_by_older_gpu():
    from app.audio_jobs import validate_render
    project = {"version": 1, "mode": "audio", "source": {"kind": "none", "id": ""},
               "duration": 10, "output_format": "wav", "lip_sync": False, "lines": [],
               "mix": {"source_level": 0, "speech_level": 0, "music_level": 1,
                       "effects_level": 0, "music_prompt": "Piano", "music_bpm": 96,
                       "effects_prompt": "", "ducking": False}}
    available = {"audio_mix": True, "tts": False, "lip_sync": False,
                 "music_generation": True, "music_controls": False, "effects_generation": False}
    try:
        validate_render(project, available)
    except ValueError as exc:
        assert "Настройки музыки" in str(exc)
    else:
        raise AssertionError("Older GPU must not silently ignore the requested BPM")


def test_server_returns_result_and_separate_original_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("H3_AUDIO_JOBS_DIR", str(tmp_path / "jobs"))
    source = tmp_path / "source.mp4"
    _video(source)
    project = {"version": 1, "mode": "video", "source": {"kind": "asset", "id": "v1"},
               "duration": 1, "output_format": "wav", "lip_sync": False, "lines": [],
               "mix": {"source_level": 1, "speech_level": 1, "music_level": 0,
                       "effects_level": 0, "music_asset_id": "", "effects_asset_id": "",
                       "music_prompt": "", "effects_prompt": "", "ducking": False}}
    original_bytes = source.read_bytes()
    app = FastAPI()
    app.include_router(make_router(object()))
    client = TestClient(app)
    uid = str(uuid.uuid4())
    with source.open("rb") as stream:
        response = client.post("/api/audio/jobs", data={"client_job_id": uid,
                               "project_json": json.dumps(project), "media_ids_json": '["source"]'},
                               files=[("media", ("source.mp4", stream, "video/mp4"))])
    assert response.status_code == 202, response.text
    assert client.get(f"/api/audio/jobs/{uid}").json()["status"] == "success"
    assert client.get(f"/api/audio/jobs/{uid}/file").status_code == 200
    audio = client.get(f"/api/audio/jobs/{uid}/original-audio")
    assert audio.status_code == 200 and len(audio.content) > 1000
    assert source.read_bytes() == original_bytes
