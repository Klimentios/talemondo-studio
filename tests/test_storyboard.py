"""«Раскадровка»: concat of finished shots, «В монтаж» probe, per-shot start frame in the store."""
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from local_studio.storyboard import ShotsInput, make_router
from local_studio.store import StudioStore

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_clip(path: Path, colour: str, seconds: float, *, audio_hz: int | None = 440, size: str = "320x240") -> None:
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color={colour}:s={size}:d={seconds}:r=24"]
    if audio_hz:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={audio_hz}:sample_rate=48000:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac", "-ac", "2"] if audio_hz else []), "-shortest", str(path)]
    subprocess.run(cmd, check=True)


def finished_job(store: StudioStore, clip: Path) -> str:
    path = store.assets_dir / f"hero-{clip.stem}.png"
    path.write_bytes(clip.stem.encode())
    hero = store.add_asset(kind="image", role="Лицо героя", original_name=path.name, mime="image/png",
                           size=1, sha256=clip.stem, path=path)
    job = store.create_job(prompt=f"<Picture 1> {clip.stem}", asset_ids=[hero["asset_id"]], settings={},
                           reference_numbers={hero["asset_id"]: 1})
    store.transition(job["job_id"], "downloading", remote_job_id=f"r-{clip.stem}")
    store.transition(job["job_id"], "completed", output_path=str(clip))
    return job["job_id"]


def route(store, suffix, method="POST"):
    return next(r for r in make_router(store).routes if r.path.endswith(suffix) and method in r.methods)


def frame_rgb(video: Path, at: float) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
                           "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout


def streams(video: Path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return out.split()


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg required")
class ConcatTests(unittest.TestCase):
    def test_same_streams_are_copied_in_order_with_sound(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            red, blue = store.results_dir / "red.mp4", store.results_dir / "blue.mp4"
            make_clip(red, "red", 1)
            make_clip(blue, "blue", 2, audio_hz=880)
            ids = [finished_job(store, blue), finished_job(store, red)]  # order = blue, then red
            result = asyncio.run(route(store, "/concat").endpoint(ShotsInput(job_ids=ids)))
            self.assertEqual(result["mode"], "copy")
            self.assertTrue(result["has_audio"])
            self.assertAlmostEqual(result["duration"], 3, delta=0.25)
            video = Path(temp) / "storyboards" / f"{result['concat_id']}.mp4"
            self.assertEqual(sorted(streams(video)), ["audio", "video"])
            first, last = frame_rgb(video, 0.5), frame_rgb(video, 2.6)
            self.assertGreater(first[2], 150)  # blue shot comes first
            self.assertGreater(last[0], 150)   # red shot last
            self.assertEqual(result["url"], f"/api/storyboard/concat/{result['concat_id']}/file")
            served = asyncio.run(route(store, "/file", "GET").endpoint(result["concat_id"], False))
            self.assertEqual(Path(served.path).resolve(), video.resolve())
            meta = json.loads((Path(temp) / "storyboards" / f"{result['concat_id']}.json").read_text())
            self.assertEqual(meta["job_ids"], ids)
            self.assertEqual(meta["output_sha256"], hashlib.sha256(video.read_bytes()).hexdigest())
            self.assertEqual(len(meta["snapshot"]["sources"]), 2)

    def test_different_streams_are_reencoded_and_silent_shot_gets_silence(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            a, b = store.results_dir / "a.mp4", store.results_dir / "b.mp4"
            make_clip(a, "green", 1)
            make_clip(b, "white", 1, audio_hz=None, size="640x480")
            result = asyncio.run(route(store, "/concat").endpoint(ShotsInput(job_ids=[finished_job(store, a), finished_job(store, b)])))
            self.assertEqual(result["mode"], "reencode")
            self.assertTrue(result["has_audio"])
            self.assertAlmostEqual(result["duration"], 2, delta=0.25)

    def test_unfinished_or_unknown_jobs_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            clip = store.results_dir / "done.mp4"
            make_clip(clip, "red", 1)
            done = finished_job(store, clip)
            path = store.assets_dir / "x.png"
            path.write_bytes(b"x")
            asset = store.add_asset(kind="image", role="Лицо героя", original_name="x.png", mime="image/png", size=1, sha256="x", path=path)
            queued = store.create_job(prompt="<Picture 1> waits", asset_ids=[asset["asset_id"]], settings={},
                                      reference_numbers={asset["asset_id"]: 1})["job_id"]
            endpoint = route(store, "/concat").endpoint
            for ids, message in (([done, queued], "Шот 2 ещё не готов"), ([done, "nope"], "Шот 2: задание не найдено"), ([], "Нет шотов")):
                with self.subTest(ids=ids), self.assertRaises(HTTPException) as caught:
                    asyncio.run(endpoint(ShotsInput(job_ids=ids)))
                self.assertEqual(caught.exception.status_code, 422)
                self.assertIn(message, caught.exception.detail)
            self.assertEqual(list((Path(temp) / "storyboards").glob("*.mp4")), [])

    def test_probe_reports_duration_and_sound_for_the_editor(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            a, b = store.results_dir / "a.mp4", store.results_dir / "b.mp4"
            make_clip(a, "red", 1)
            make_clip(b, "blue", 2, audio_hz=None)
            ids = [finished_job(store, a), finished_job(store, b)]
            shots = asyncio.run(route(store, "/probe").endpoint(ShotsInput(job_ids=ids)))
            self.assertEqual([s["job_id"] for s in shots], ids)
            self.assertEqual([s["has_audio"] for s in shots], [True, False])
            self.assertAlmostEqual(shots[1]["duration"], 2, delta=0.1)

    def test_bad_concat_id_is_404(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(route(StudioStore(Path(temp)), "/file", "GET").endpoint("../../etc/passwd", False))
            self.assertEqual(caught.exception.status_code, 404)


class PerShotStartFrameStoreTests(unittest.TestCase):
    def _assets(self, store, names):
        ids = {}
        for name, role in names:
            path = store.assets_dir / name
            path.write_bytes(name.encode())
            ids[name] = store.add_asset(kind="image", role=role, original_name=name, mime="image/png",
                                        size=len(name), sha256=hashlib.sha256(name.encode()).hexdigest(), path=path)["asset_id"]
        return ids

    def test_start_frame_appended_as_next_picture_is_anchored_and_colour_matched(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = self._assets(store, [("hero.png", "Персонаж целиком"), ("room.png", "Окружение"),
                                       ("frame.png", "Кадр сцены"), ("ref1.png", "Кадр сцены")])
            order = ["hero.png", "room.png", "frame.png", "ref1.png"]
            job = store.create_job(
                prompt="<Picture 1> walks into <Picture 2>. REFERENCE ROLE CONTRACT: <Picture 3> anchors this scene composition at its requested frame.",
                asset_ids=[ids[n] for n in order],
                reference_numbers={ids[n]: i for i, n in enumerate(order, 1)},
                reference_roles={ids["hero.png"]: "Персонаж целиком", ids["room.png"]: "Окружение",
                                 ids["frame.png"]: "Кадр сцены", ids["ref1.png"]: "Окружение"},
                reference_owners={ids["hero.png"]: "VEGA"},
                settings={"quality": "quick", "duration": 4, "guide_frames": [[3, 0]],
                          "color_match": {"picture": 4, "strength": 0.6, "method": "mkl"}})
            settings = json.loads(job["settings_json"])
            self.assertEqual(settings["guide_frames"], [[3, 0]])
            self.assertEqual(settings["color_match"], {"picture": 4, "strength": 0.6, "method": "mkl"})
            self.assertEqual(settings["length"], 107)  # 4 s, official H3 minimum
            self.assertEqual([i["role"] for i in json.loads(job["manifest_json"])["images"]],
                             ["Персонаж целиком", "Окружение", "Кадр сцены", "Окружение"])

    def test_numbering_must_stay_consecutive(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = self._assets(store, [("hero.png", "Персонаж целиком"), ("frame.png", "Кадр сцены")])
            with self.assertRaisesRegex(ValueError, "подряд"):
                store.create_job(prompt="<Picture 1> walks.", asset_ids=[ids["hero.png"], ids["frame.png"]],
                                 reference_numbers={ids["hero.png"]: 1, ids["frame.png"]: 3},
                                 reference_roles={ids["frame.png"]: "Кадр сцены"},
                                 settings={"quality": "quick", "guide_frames": [[3, 0]]})

    def test_guide_frame_must_point_at_the_start_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = self._assets(store, [("hero.png", "Персонаж целиком"), ("frame.png", "Кадр сцены")])
            with self.assertRaisesRegex(ValueError, "Кадр сцены"):
                store.create_job(prompt="<Picture 1> walks.", asset_ids=[ids["hero.png"], ids["frame.png"]],
                                 reference_numbers={ids["hero.png"]: 1, ids["frame.png"]: 2},
                                 reference_roles={ids["hero.png"]: "Персонаж целиком", ids["frame.png"]: "Кадр сцены"},
                                 settings={"quality": "quick", "guide_frames": [[1, 0]]})



class FourSecondCapabilityTests(unittest.TestCase):
    def test_old_gpu_api_refuses_four_seconds_before_upload(self):
        from unittest.mock import patch
        import httpx
        from local_studio.worker import HttpGpuTransport, QueueWorker
        from tests.gpu_caps import CONTRACT_CAPS
        from tests.test_gpu_contract import _client_with
        posted = []

        def handler(request):
            if request.url.path == "/api/capabilities":
                return httpx.Response(200, json={**CONTRACT_CAPS, "duration_seconds": [5, 15]})
            if request.method == "POST":
                posted.append(request)
            return httpx.Response(404, json={"detail": "not found"})

        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = PerShotStartFrameStoreTests()._assets(store, [("hero.png", "Персонаж целиком")])
            job = store.create_job(prompt="<Picture 1> walks.", asset_ids=[ids["hero.png"]],
                                   reference_numbers={ids["hero.png"]: 1}, settings={"quality": "quick", "duration": 4})
            with patch("httpx.AsyncClient", _client_with(handler)):
                asyncio.run(QueueWorker(store, HttpGpuTransport("http://gpu")).run_once())
            record = store.get_job(job["job_id"])
            self.assertEqual(record["state"], "failed")
            self.assertIn("от 5 с", record["error"])
            self.assertFalse(posted)


if __name__ == "__main__":
    unittest.main()


def test_concat_api_refuses_rejected_scene_take(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    run = {"cuts": [{"id": "cut-1", "shotJobId": "job-1", "shotQcReview": "rejected",
                     "shotQc": {"job_id": "job-1", "decision": "manual_review"}}]}
    app = FastAPI()
    app.include_router(make_router(SimpleNamespace(root=tmp_path), SimpleNamespace(all=lambda: [run])))
    response = TestClient(app).post("/api/storyboard/concat", json={"job_ids": ["job-1"]})
    assert response.status_code == 422
    assert "не принят" in response.json()["detail"]
    run["cuts"][0].update(shotJobId="job-new", shotQcReview="accepted",
                          shotQc={"job_id": "job-new", "decision": "accept"},
                          takes=[{"jobId": "job-1"}, {"jobId": "job-new"}])
    old = TestClient(app).post("/api/storyboard/concat", json={"job_ids": ["job-1"]})
    assert old.status_code == 422
    assert "старый дубль" in old.json()["detail"]


def test_concat_rejects_modified_output_before_invoking_ffmpeg(tmp_path):
    from types import SimpleNamespace
    import pytest
    from local_studio.storyboard import shot_paths

    output = tmp_path / 'take.mp4'
    output.write_bytes(b'original')
    job = {'state': 'completed', 'output_path': str(output),
           'output_sha256': hashlib.sha256(b'original').hexdigest()}
    store = SimpleNamespace(results_dir=tmp_path, get_job=lambda _: job)
    assert shot_paths(store, ['j1']) == [output.resolve()]
    output.write_bytes(b'replaced')
    with pytest.raises(ValueError, match='изменён'):
        shot_paths(store, ['j1'])
