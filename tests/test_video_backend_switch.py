"""«Свой GPU / MiniMax API»: jobs without an explicit route follow the Studio switch."""
import asyncio
import tempfile
import unittest
from pathlib import Path

from local_studio.minimax_cloud import MiniMaxCloudTransport


class VideoBackendSwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cloud = MiniMaxCloudTransport(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_follows_key_until_switched(self):
        self.assertEqual(self.cloud.default_backend(), "gpu")
        with self.assertRaisesRegex(ValueError, "Ключ"):
            self.cloud.set_default_backend("minimax_api")
        (self.root / "minimax-api-key").write_text("k")
        self.assertEqual(self.cloud.default_backend(), "minimax_api")
        self.cloud.set_default_backend("gpu")
        self.assertEqual(self.cloud.default_backend(), "gpu")
        with self.assertRaises(ValueError):
            self.cloud.set_default_backend("turbo")

    def test_gpu_route_keeps_lora_and_drops_cloud_keys(self):
        (self.root / "minimax-api-key").write_text("k")
        self.cloud.set_default_backend("gpu")
        settings = self.cloud.resolve_settings({"style_adapter": "auto", "megapixels": 0.6, "quality": "custom",
                                                "cloud_model": "MiniMax-H3"})
        self.assertEqual(settings, {"style_adapter": "auto", "megapixels": 0.6, "quality": "custom"})
        explicit = self.cloud.resolve_settings({"backend": "gpu", "cloud_resolution": "768P"})
        self.assertNotIn("backend", explicit)

    def test_api_route_forces_768p_without_lora(self):
        (self.root / "minimax-api-key").write_text("k")
        settings = self.cloud.resolve_settings({"style_adapter": "auto", "megapixels": 0.6})
        self.assertEqual(settings["backend"], "minimax_api")
        self.assertEqual((settings["cloud_resolution"], settings["style_adapter"]), ("768P", "none"))
        self.cloud.set_default_backend("gpu")
        self.assertEqual(self.cloud.resolve_settings({"backend": "minimax_api"})["backend"], "minimax_api")


class CloudBypassesGpuFifoTests(unittest.TestCase):
    def test_cloud_job_is_submitted_behind_a_stuck_gpu_job(self):
        from tests.test_queue_worker import FakeGpu
        from local_studio.store import StudioStore
        from local_studio.worker import QueueWorker
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "a.png"
            path.write_bytes(b"test")
            asset = store.add_asset(kind="image", role="hero", original_name="a.png",
                                    mime="image/png", size=4, sha256="test", path=path)
            gpu_job = store.create_job(prompt="<Picture 1> gpu", asset_ids=[asset["asset_id"]], settings={})
            store.transition(gpu_job["job_id"], "running", remote_job_id="remote-dead")
            cloud_job = store.create_job(prompt="<Picture 1> cloud", asset_ids=[asset["asset_id"]],
                                         settings={"backend": "minimax_api", "cloud_model": "MiniMax-H3",
                                                   "cloud_resolution": "768P", "aspect_ratio": "9:16"})
            gpu_only = store.create_job(prompt="<Picture 1> gpu 2", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            worker = QueueWorker(store, fake)
            self.assertTrue(asyncio.run(worker.stage_cloud_once()))
            self.assertEqual(fake.submissions, [cloud_job["job_id"]])
            self.assertEqual(store.get_job(cloud_job["job_id"])["state"], "queued_gpu")
            self.assertEqual(store.get_job(gpu_only["job_id"])["state"], "queued_local")
            self.assertFalse(asyncio.run(worker.stage_cloud_once()))


if __name__ == "__main__":
    unittest.main()
