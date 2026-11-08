import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_studio.store import StudioStore
from local_studio.worker import PreEnqueueRejected, QueueWorker, gpu_director_submission_options


class FakeGpu:
    def __init__(self):
        self.submissions = []
        self.remote = {}
        self.states = {}

    async def lookup(self, job_id):
        return self.remote.get(job_id)

    async def submit(self, job, store):
        self.submissions.append(job["job_id"])
        result = {"job_id": f"remote-{job['job_id']}"}
        self.remote[job["job_id"]] = result
        self.states[result["job_id"]] = {"status": "queued"}
        return result

    async def status(self, remote_job_id):
        return self.states[remote_job_id]

    async def cancel(self, remote_job_id):
        if remote_job_id not in self.states:
            return False
        self.states[remote_job_id] = {"status": "error", "error": "interrupted"}
        return True

    async def download(self, remote_job_id, destination):
        destination.write_bytes(b"fake video" * 200)


class QueueWorkerTests(unittest.TestCase):
    def test_new_jobs_route_emotion_direction_through_qwen_but_legacy_jobs_stay_precompiled(self):
        self.assertEqual(gpu_director_submission_options({"emotional_direction": True}), {
            "precompiled_prompt": "false", "emotional_direction": "true",
        })
        self.assertEqual(gpu_director_submission_options({}), {
            "precompiled_prompt": "true", "emotional_direction": "false",
        })

    def test_agent_prepared_prompt_bypasses_gpu_director_even_with_saved_legacy_flag(self):
        self.assertEqual(gpu_director_submission_options({"agent_prepared_prompt": True,
                                                           "emotional_direction": True}), {
            "precompiled_prompt": "true", "emotional_direction": "false",
        })

    def _setup(self, root):
        store = StudioStore(root)
        path = store.assets_dir / "a.png"
        path.write_bytes(b"test")
        asset = store.add_asset(kind="image", role="hero", original_name="a.png",
                                mime="image/png", size=4, sha256="test", path=path)
        return store, asset

    def test_fifo_and_persistent_result(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            jobs = [store.create_job(prompt=f"<Picture 1> scene {n}", asset_ids=[asset["asset_id"]],
                                     settings={}) for n in (1, 2)]
            fake = FakeGpu()
            worker = QueueWorker(store, fake)
            asyncio.run(worker.run_once())
            self.assertEqual(fake.submissions, [jobs[0]["job_id"]])
            self.assertEqual(store.get_job(jobs[0]["job_id"])["state"], "queued_gpu")
            observed = {"schema_version": 1, "source": "comfyui_history", "prompt_id": f"remote-{jobs[0]['job_id']}",
                        "graph_sha256": "a" * 64, "weights_sha256": None}
            fake.states[f"remote-{jobs[0]['job_id']}"] = {"status": "success", "execution_observation": observed}
            asyncio.run(worker.run_once())
            with patch("local_studio.worker.validate_mp4"):
                asyncio.run(worker.run_once())
            self.assertEqual(StudioStore(Path(temp)).get_job(jobs[0]["job_id"])["state"], "completed")
            saved = StudioStore(Path(temp)).get_job(jobs[0]["job_id"])
            self.assertEqual(json.loads(saved["director_json"])["execution_observation"], observed)
            asyncio.run(worker.run_once())
            self.assertEqual(fake.submissions, [job["job_id"] for job in jobs])

    def test_finished_later_job_downloads_while_earlier_job_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            jobs = [store.create_job(prompt=f"<Picture 1> scene {n}", asset_ids=[asset['asset_id']],
                                     settings={}) for n in (1, 2)]
            fake = FakeGpu()
            for job in jobs:
                remote_id = f"remote-{job['job_id']}"
                store.transition(job['job_id'], 'queued_gpu', remote_job_id=remote_id)
                fake.states[remote_id] = {'status': 'running'}
            fake.states[f"remote-{jobs[1]['job_id']}"] = {'status': 'success'}
            worker = QueueWorker(store, fake)
            with patch('local_studio.worker.validate_mp4'):
                self.assertTrue(asyncio.run(worker.sync_ready_videos_once()))
            self.assertEqual(store.get_job(jobs[0]['job_id'])['state'], 'queued_gpu')
            result = store.get_job(jobs[1]['job_id'])
            self.assertEqual(result['state'], 'completed')
            self.assertTrue(Path(result['output_path']).is_file())
            self.assertEqual(fake.submissions, [])

    def test_restart_does_not_resubmit_ambiguous_upload(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "uploading")
            fake = FakeGpu()
            worker = QueueWorker(StudioStore(Path(temp)), fake)
            asyncio.run(worker.run_once())
            self.assertEqual(fake.submissions, [])
            self.assertEqual(store.get_job(job["job_id"])["state"], "needs_reconciliation")

    def test_verified_pre_enqueue_director_rejection_does_not_block_fifo(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            failed = store.create_job(prompt="<Picture 1> long director plan", asset_ids=[asset["asset_id"]], settings={})
            next_job = store.create_job(prompt="<Picture 1> next scene", asset_ids=[asset["asset_id"]], settings={})

            class RejectedDirectorGpu(FakeGpu):
                async def submit(self, job, store):
                    self.submissions.append(job["job_id"])
                    raise PreEnqueueRejected(
                        "GPU API HTTP 503: Автоматическая режиссура MiniMax H3 не завершена: "
                        "Qwen Director ответила не по JSON-контракту"
                    )

            fake = RejectedDirectorGpu()
            worker = QueueWorker(store, fake)
            self.assertTrue(asyncio.run(worker.run_once()))
            self.assertEqual(store.get_job(failed["job_id"])["state"], "failed")
            self.assertEqual(store.get_job(next_job["job_id"])["state"], "queued_local")
            self.assertTrue(asyncio.run(worker.run_once()))
            self.assertEqual(fake.submissions, [failed["job_id"], next_job["job_id"]])

    def test_pre_enqueue_503_with_unavailable_registry_stays_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1> director", asset_ids=[asset["asset_id"]], settings={})

            class OfflineLookupGpu(FakeGpu):
                def __init__(self):
                    super().__init__()
                    self.lookups = 0

                async def submit(self, job, store):
                    raise PreEnqueueRejected("GPU API HTTP 503: Automatic Director failed")

                async def lookup(self, job_id):
                    self.lookups += 1
                    if self.lookups == 1:
                        return None
                    raise ConnectionError("registry unavailable")

            self.assertTrue(asyncio.run(QueueWorker(store, OfflineLookupGpu()).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "needs_reconciliation")

    def test_real_sampling_steps_are_persisted_for_queue_ui(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            worker = QueueWorker(store, fake)
            asyncio.run(worker.run_once())
            fake.states[f"remote-{job['job_id']}"] = {
                "status": "running", "sampling_progress": {"value": 7, "max": 25},
            }
            asyncio.run(worker.run_once())
            current = StudioStore(Path(temp)).get_job(job["job_id"])
            self.assertEqual(current["state"], "running")
            self.assertEqual((current["sampling_value"], current["sampling_max"]), (7, 25))

    def test_offline_gpu_keeps_unsent_job_in_local_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})

            class OfflineGpu(FakeGpu):
                async def lookup(self, job_id):
                    raise ConnectionError("GPU offline")

            fake = OfflineGpu()
            self.assertFalse(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_local")
            self.assertEqual(fake.submissions, [])

    def test_pause_keeps_pending_job_until_resumed(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            store.set_queue_paused(True)
            self.assertFalse(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_local")
            store.set_queue_paused(False)
            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(len(fake.submissions), 1)

    def test_paused_staged_success_is_reconciled_without_submission(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1> staged", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            fake.remote[job["job_id"]] = {"job_id": "remote-staged"}
            fake.states["remote-staged"] = {"status": "success"}
            store.set_queue_paused(True)

            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_gpu")
            self.assertEqual(fake.submissions, [])

            worker = QueueWorker(store, fake)
            self.assertTrue(asyncio.run(worker.run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "downloading")
            with patch("local_studio.worker.validate_mp4"):
                self.assertTrue(asyncio.run(worker.run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "completed")

    def test_paused_cancelled_local_stage_does_not_resubmit(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1> canceled", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            fake.remote[job["job_id"]] = {"job_id": "remote-canceled"}
            fake.states["remote-canceled"] = {"status": "error", "error": "interrupted"}
            store.set_queue_paused(True)

            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "queued_gpu")
            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "failed")
            self.assertEqual(fake.submissions, [])

    def test_paused_cancelled_job_with_server_success_downloads_result(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1> finished before sync", asset_ids=[asset["asset_id"]], settings={})
            store.set_queue_paused(True)
            fake = FakeGpu()
            fake.remote[job["job_id"]] = {"job_id": "remote-finished"}
            fake.states["remote-finished"] = {"status": "success"}

            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "downloading")
            with patch("local_studio.worker.validate_mp4"):
                self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            self.assertEqual(store.get_job(job["job_id"])["state"], "completed")
            self.assertEqual(fake.submissions, [])

    def test_paused_queue_reconciles_ambiguous_job_without_resubmitting(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            store.transition(job["job_id"], "needs_reconciliation",
                             error="Previous HTTP response was ambiguous")
            store.set_queue_paused(True)
            fake = FakeGpu()
            fake.remote[job["job_id"]] = {
                "job_id": "server-job-123", "director": {"style": "realism"},
            }
            fake.states["server-job-123"] = {"status": "queued"}

            self.assertTrue(asyncio.run(QueueWorker(store, fake).run_once()))
            current = store.get_job(job["job_id"])
            self.assertEqual(current["state"], "queued_gpu")
            self.assertEqual(current["remote_job_id"], "server-job-123")
            self.assertEqual(fake.submissions, [])

    def test_stopping_waits_for_remote_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            job = store.create_job(prompt="<Picture 1>", asset_ids=[asset["asset_id"]], settings={})
            fake = FakeGpu()
            worker = QueueWorker(store, fake)
            asyncio.run(worker.run_once())
            remote_id = f"remote-{job['job_id']}"
            fake.states[remote_id] = {"status": "running"}
            asyncio.run(worker.run_once())
            store.transition(job["job_id"], "stopping")
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "stopping")
            asyncio.run(fake.cancel(remote_id))
            asyncio.run(worker.run_once())
            self.assertEqual(store.get_job(job["job_id"])["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()

class EagerServerQueueTests(unittest.TestCase):
    _setup = QueueWorkerTests._setup
    def test_uploads_next_h3_while_previous_is_rendering(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            jobs = [store.create_job(prompt=f'<Picture 1> scene {n}', asset_ids=[asset['asset_id']], settings={}) for n in (1, 2)]
            class ServerGpu(FakeGpu):
                async def capabilities(self):
                    return {'server_queue_v1': True}
            fake = ServerGpu()
            worker = QueueWorker(store, fake)
            asyncio.run(worker.run_once())
            store.transition(jobs[0]['job_id'], 'running')
            asyncio.run(worker.stage_once())
            self.assertEqual(fake.submissions, [j['job_id'] for j in jobs])
            self.assertEqual(store.get_job(jobs[0]['job_id'])['state'], 'running')
            self.assertEqual(store.get_job(jobs[1]['job_id'])['state'], 'queued_gpu')

    def test_pause_prevents_early_transfer(self):
        with tempfile.TemporaryDirectory() as temp:
            store, asset = self._setup(Path(temp))
            store.create_job(prompt='<Picture 1> waits', asset_ids=[asset['asset_id']], settings={})
            store.set_queue_paused(True)
            class ServerGpu(FakeGpu):
                async def capabilities(self):
                    return {'server_queue_v1': True}
            fake = ServerGpu()
            asyncio.run(QueueWorker(store, fake).stage_once())
            self.assertEqual(fake.submissions, [])
