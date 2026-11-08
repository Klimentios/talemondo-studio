"""Audit 42.3A №14/№25 and 42.4 №1/№4/№7: submit safety and contract-5 fields in local_studio/worker.py."""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from local_studio.store import StudioStore
from local_studio.worker import HttpGpuTransport, QueueWorker, SubmitNotSent, gpu_form_fields
from tests.gpu_caps import serving_caps

ROOT = Path(__file__).resolve().parents[1]


def setup_store(root: Path):
    store = StudioStore(root)
    path = store.assets_dir / "a.png"
    path.write_bytes(b"test")
    asset = store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png", size=4,
                            sha256=hashlib.sha256(b"test").hexdigest(), path=path)
    return store, asset


def run_with(handler, call):
    original = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)
    with patch("httpx.AsyncClient", client):
        return asyncio.run(call())


class SubmitRulesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store, self.asset = setup_store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def job(self, **settings):
        return self.store.create_job(prompt="<Picture 1> walks", asset_ids=[self.asset["asset_id"]], settings=settings)

    def test_capabilities_timeout_is_not_sent(self):
        def handler(request):
            raise httpx.ReadTimeout("slow tunnel")
        with self.assertRaises(SubmitNotSent):
            run_with(handler, lambda: HttpGpuTransport("http://gpu").capabilities())

    def test_capabilities_timeout_keeps_job_queued_without_upload(self):
        job = self.job()
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request.url.path)
            raise httpx.ReadTimeout("slow tunnel")
        transport = HttpGpuTransport("http://gpu")

        class Wrapped:
            lookup = staticmethod(lambda job_id: _none())
            capabilities = transport.capabilities
            submit = transport.submit
        run_with(handler, lambda: QueueWorker(self.store, Wrapped()).run_once())
        self.assertEqual(self.store.get_job(job["job_id"])["state"], "queued_local")
        self.assertEqual(posts, [])

    def test_4_3_job_reaches_a_gpu_that_lists_it_and_not_an_older_one(self):
        job = self.job(aspect_ratio="4:3", quality="quick")
        settings = json.loads(job["settings_json"])
        self.assertEqual((settings["aspect_ratio"], settings["width"], settings["height"]), ("4:3", 1056, 800))
        bodies = []

        def handler(request):
            if request.method == "POST":
                bodies.append(request.content)
                return httpx.Response(202, json={"job_id": "gpu-1", "state": "queued"})
            return httpx.Response(404)
        # an older GPU API (16:9 / 9:16 only) refuses it before any upload
        with self.assertRaisesRegex(ValueError, "4:3"):
            run_with(serving_caps(handler), lambda: HttpGpuTransport("http://gpu").submit(job, self.store))
        self.assertEqual(bodies, [])
        caps = {"aspect_ratios": ["16:9", "9:16", "4:3", "3:4", "1:1"]}
        try:
            run_with(serving_caps(handler, caps), lambda: HttpGpuTransport("http://gpu").submit(job, self.store))
        except Exception:
            pass  # only the form that left the Mac matters here
        self.assertEqual(len(bodies), 1)
        self.assertIn(b'name="aspect_ratio"\r\n\r\n4:3', bodies[0])

    def test_unparsable_accept_is_looked_up_not_failed(self):
        job = self.job()
        transport = HttpGpuTransport("http://gpu")

        def handler(request):
            if request.url.path == "/api/jobs" and request.method == "POST":
                return httpx.Response(202, content=b"<html>proxy</html>")
            return httpx.Response(404)
        with self.assertRaises(RuntimeError) as caught:
            run_with(serving_caps(handler), lambda: transport.submit(job, self.store))
        self.assertNotIsInstance(caught.exception, ValueError)

        class Gpu:
            async def lookup(self, job_id):
                return {"job_id": "remote-7"} if self.posted else None
            posted = False

            async def submit(self, job, store):
                self.posted = True
                raise RuntimeError("GPU принял запрос, но ответ не разобран")
        asyncio.run(QueueWorker(self.store, Gpu()).run_once())
        current = self.store.get_job(job["job_id"])
        self.assertEqual((current["state"], current["remote_job_id"]), ("queued_gpu", "remote-7"))

    def test_cache_mode_is_checked_against_gpu(self):
        job = self.job(cache_mode="fast")
        with self.assertRaises(ValueError) as caught:
            run_with(serving_caps(lambda r: httpx.Response(500), {"cache_modes": ["off"]}),
                     lambda: HttpGpuTransport("http://gpu").submit(job, self.store))
        self.assertIn("Режим кэша «fast» недоступен", str(caught.exception))

    def test_missing_baseline_weights_are_rejected_before_upload(self):
        from app.render_profiles import render_capability

        job = self.job()
        posts = []

        def handler(request):
            if request.method == "POST":
                posts.append(request.url.path)
            return httpx.Response(500)

        with self.assertRaisesRegex(ValueError, "Профиль bf16 не установлен"):
            run_with(serving_caps(handler, {"render_profiles_v1": render_capability({})}),
                     lambda: HttpGpuTransport("http://gpu").submit(job, self.store))
        self.assertEqual(posts, [])

    def test_custom_cache_is_never_sent_to_old_gpu(self):
        from app.cache_policy import SAFE
        job = self.job(cache_mode="custom", cache_config=dict(SAFE))
        posts = []
        def handler(request):
            posts.append(request.url.path)
            return httpx.Response(500)
        with self.assertRaisesRegex(ValueError, "CUSTOM"):
            run_with(serving_caps(handler, {"cache_modes": ["off", "safe", "fast"]}),
                     lambda: HttpGpuTransport("http://gpu").submit(job, self.store))
        self.assertEqual(posts, [])

    def test_allow_short_motion_reference_is_sent_only_when_confirmed(self):
        job = self.job()
        self.assertNotIn("allow_short_motion_reference", gpu_form_fields(job))
        confirmed = dict(job)
        settings = json.loads(job["settings_json"])
        settings["allow_short_motion_reference"] = True
        confirmed["settings_json"] = json.dumps(settings)
        self.assertEqual(gpu_form_fields(confirmed)["allow_short_motion_reference"], "true")

    def test_hints_point_to_current_installer(self):
        source = (ROOT / "local_studio/worker.py").read_text()
        self.assertNotIn("deploy_h3_new_server.sh", source)
        self.assertIn("scripts/h3_new_server.sh", source)


async def _none():
    return None


if __name__ == "__main__":
    unittest.main()


def test_keep_source_audio_puts_the_original_track_under_the_render(tmp_path):
    # «Правка видео» → «Звук исходника без изменений» (2026-10-08): H3 only imitates a reference soundtrack.
    import hashlib, json, shutil, subprocess
    import pytest
    from local_studio.store import StudioStore
    from local_studio.worker import keep_source_audio_path, replace_audio
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    store = StudioStore(tmp_path / "studio")
    source = store.assets_dir / "source.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:r=24", "-f", "lavfi",
                    "-i", "sine=f=1000:sample_rate=48000", "-t", "6", "-shortest", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(source)], check=True)
    render = tmp_path / "render.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24", "-f", "lavfi",
                    "-i", "sine=f=200:sample_rate=48000", "-t", "4", "-shortest", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", str(render)], check=True)
    asset = store.add_asset(kind="video", role="Движение и камера", original_name="source.mp4", mime="video/mp4",
                            size=source.stat().st_size, sha256=hashlib.sha256(source.read_bytes()).hexdigest(), path=source)
    job = {"settings_json": json.dumps({"keep_source_audio": True}),
           "manifest_json": json.dumps({"videos": [{"asset_id": asset["asset_id"], "role": "Движение и камера"}]})}
    assert keep_source_audio_path({**job, "settings_json": "{}"}, store) is None
    replace_audio(render, keep_source_audio_path(job, store))
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(render)], capture_output=True, text=True).stdout)
    assert 3.8 < duration < 4.3  # cut to the picture
    # 1 kHz tone of the source, not H3's 200 Hz: count zero crossings of the first second
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(render), "-t", "1", "-ac", "1", "-f", "s16le", "-"],
                         capture_output=True).stdout
    import array
    samples = array.array("h", raw)
    crossings = sum(1 for a, b in zip(samples, samples[1:]) if (a < 0) != (b < 0))
    assert 1700 < crossings < 2300
