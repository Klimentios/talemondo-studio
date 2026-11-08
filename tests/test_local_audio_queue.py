from __future__ import annotations

import hashlib
import json

import pytest

from local_studio.audio_jobs import AudioQueue
from local_studio.store import StudioStore


def _project(asset_id):
    return {"version": 1, "title": "Звуковая сцена", "mode": "audio",
            "source": {"kind": "none", "id": ""}, "duration": 10,
            "output_format": "wav", "lip_sync": False, "lip_sync_target": "",
            "lines": [], "mix": {"source_level": 0, "speech_level": 1,
                               "music_level": .5, "effects_level": .5,
                               "music_asset_id": asset_id, "effects_asset_id": "",
                               "music_prompt": "", "effects_prompt": "", "ducking": False}}


def test_audio_queue_freezes_asset_hash_and_preserves_source(tmp_path):
    store = StudioStore(tmp_path)
    source = store.assets_dir / "sample.wav"
    source.write_bytes(b"original sound bytes")
    asset = store.add_asset(kind="audio", role="Музыка", original_name="sample.wav",
                            mime="audio/wav", size=source.stat().st_size,
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), path=source)
    queue = AudioQueue(store, "http://127.0.0.1:18189")
    job = queue.create(_project(asset["asset_id"]))
    assert job["state"] == "queued_local"
    manifest = json.loads(job["manifest_json"])
    assert queue._path(manifest["music"]) == source
    assert source.read_bytes() == b"original sound bytes"
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменён"):
        queue._path(manifest["music"])


def test_music_prompt_only_is_accepted_into_local_queue(tmp_path):
    store = StudioStore(tmp_path)
    queue = AudioQueue(store, "http://127.0.0.1:18189")
    project = _project("")
    project["mix"]["music_prompt"] = "Инструментальная музыка для сцены"
    job = queue.create(project)
    assert job["state"] == "queued_local"
    assert json.loads(job["manifest_json"]) == {}
