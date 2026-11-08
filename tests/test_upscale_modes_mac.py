import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from local_studio.store import StudioStore
from local_studio.upscale import UpscaleQueue


class UpscaleModeQueueTests(unittest.TestCase):
    def _finished_job(self, store):
        clip = store.results_dir / "done.mp4"
        clip.write_bytes(b"mp4")
        path = store.assets_dir / "a.png"
        path.write_bytes(b"x")
        asset = store.add_asset(kind="image", role="Лицо героя", original_name="a.png", mime="image/png",
                                size=1, sha256="x", path=path)
        job = store.create_job(prompt="<Picture 1> walks", asset_ids=[asset["asset_id"]], settings={})
        store.transition(job["job_id"], "downloading", remote_job_id="r")
        store.transition(job["job_id"], "completed", output_path=str(clip))
        return job["job_id"]

    def test_existing_database_is_migrated_and_old_rows_are_upscales(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            with closing(sqlite3.connect(store.db_path)) as db:
                db.execute("CREATE TABLE upscale_jobs (upscale_id TEXT PRIMARY KEY, source_job_id TEXT NOT NULL, "
                           "state TEXT NOT NULL, remote_job_id TEXT, output_path TEXT, error TEXT, "
                           "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                db.execute("INSERT INTO upscale_jobs(upscale_id,source_job_id,state) VALUES ('old','src','completed')")
                db.commit()
            queue = UpscaleQueue(store, "")
            self.assertEqual(queue.list()[0]["mode"], "upscale")

    def test_mode_is_stored_and_validated(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            queue = UpscaleQueue(store, "")
            source = self._finished_job(store)
            self.assertEqual(queue.create(source, "smooth")["mode"], "smooth")
            with self.assertRaises(ValueError):
                queue.create(source, "sharpen")


if __name__ == "__main__":
    unittest.main()
