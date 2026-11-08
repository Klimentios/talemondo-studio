"""Pictures get a small JPEG thumbnail, not only videos (pages loaded full-size images)."""
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import local_studio.main as studio


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_image_asset_gets_a_small_thumbnail(tmp_path, monkeypatch):
    studio.ctx.store.assets_dir.mkdir(parents=True, exist_ok=True)
    picture = studio.ctx.store.assets_dir / "thumbnail-test.png"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=1600x900",
                    "-frames:v", "1", "-y", str(picture)], check=True)
    monkeypatch.setattr(studio.ctx.store, "get_asset", lambda asset_id: {"asset_id": asset_id, "kind": "image", "path": str(picture)})
    response = TestClient(studio.app, base_url="http://127.0.0.1").get("/api/assets/pic-1/thumbnail")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) < picture.stat().st_size
    picture.unlink()


def test_asset_outside_the_library_folder_is_not_served(tmp_path, monkeypatch):
    # A hand-edited row must not turn /api/assets/... into a reader of any file on the Mac.
    secret = tmp_path / "notes.txt"
    secret.write_text("private")
    monkeypatch.setattr(studio.ctx.store, "get_asset", lambda asset_id: {"asset_id": asset_id, "kind": "image",
                                                                          "mime": "text/plain", "path": str(secret)})
    client = TestClient(studio.app, base_url="http://127.0.0.1")
    assert client.get("/api/assets/x/file").status_code == 404
    assert client.get("/api/assets/x/thumbnail").status_code == 404


def test_audio_asset_has_no_thumbnail(tmp_path, monkeypatch):
    sound = tmp_path / "a.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setattr(studio.ctx.store, "get_asset", lambda asset_id: {"asset_id": asset_id, "kind": "audio", "path": str(sound)})
    assert TestClient(studio.app, base_url="http://127.0.0.1").get("/api/assets/snd-1/thumbnail").status_code == 404


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_finished_clip_has_a_poster(tmp_path, monkeypatch):
    results = studio.ctx.store.results_dir
    results.mkdir(parents=True, exist_ok=True)
    clip = results / "poster-test.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=320x568:d=1",
                    "-pix_fmt", "yuv420p", "-y", str(clip)], check=True)
    monkeypatch.setattr(studio.ctx.store, "get_job", lambda job_id: {"job_id": job_id, "state": "completed", "output_path": str(clip)})
    response = TestClient(studio.app, base_url="http://127.0.0.1").get("/api/jobs/poster-test/poster")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"


def test_studio_pages_are_always_revalidated():
    response = TestClient(studio.app, base_url="http://127.0.0.1").get("/audio-studio.html")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
