import asyncio
import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_studio.compute.manager import ComputeConflict, ComputeManager


class FakeKeychain:
    def __init__(self): self.value = None
    def get(self, _): return self.value
    def has(self, _): return self.value is not None
    def set(self, _, value): self.value = value
    def delete(self, _): self.value = None


class FakeClient:
    host = "gpu.example"
    port = 2201
    create_calls = 0
    ambiguous = False
    offers = [{"id": 12, "gpu_name": "GPU 96GB", "gpu_vram_mb": 96000,
               "cpu_ram_mb": 256000, "disk_space_gb": 500, "cuda_max_good": 12.8,
               "total_usd_per_hour": 1.5}]
    def __init__(self, key):
        if key == "invalid": raise ValueError("bad key")
        self.key = key
    async def get_account(self): return {"id": 1, "email": "user@example.test", "balance": 20}
    async def search_h3_offers(self, max_hourly_rate=None):
        return [row for row in self.offers if max_hourly_rate is None or row["total_usd_per_hour"] <= max_hourly_rate]
    async def create_instance(self, offer_id, disk, label):
        type(self).create_calls += 1
        if type(self).ambiguous: raise TimeoutError("network timeout")
        return {"instance_id": 81}
    async def get_instances(self): return [{"id":81,"ssh_host":self.host,"ssh_port":self.port,"actual_status":"running"}]
    async def get_instance(self, _): return {"id":81,"ssh_host":"vast-host.example","ssh_port":2222,"actual_status":"running"}
    async def stop_instance(self, _): pass
    async def destroy_instance(self, _): pass


class ComputeManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.keychain = FakeKeychain()
        self.busy = False
        self.manager = ComputeManager(Path(self.temp.name), keychain=self.keychain,
                                      client_factory=FakeClient, busy=lambda: self.busy)
        FakeClient.create_calls = 0
        FakeClient.ambiguous = False

    def tearDown(self): self.temp.cleanup()

    def run_async(self, awaitable): return asyncio.run(awaitable)

    def test_key_must_validate_before_storage_and_is_never_returned(self):
        with self.assertRaises(ValueError): self.run_async(self.manager.save_key("invalid"))
        self.assertIsNone(self.keychain.value)
        result = self.run_async(self.manager.save_key("valid-secret-key"))
        self.assertEqual(result["email"], "user@example.test")
        self.assertNotIn("key", result)
        self.assertNotIn("valid-secret-key", repr(result))

    def test_rent_rechecks_price_against_explicit_ceiling(self):
        self.keychain.value = "valid"
        with self.assertRaises(Exception): self.run_async(self.manager.rent(12, 1.0))
        result = self.run_async(self.manager.rent(12, 1.5))
        self.assertEqual(result["instance_id"], 81)
        self.assertEqual(self.manager.operation(result["operation_id"])["state"], "created")

    def test_stop_and_destroy_are_blocked_while_studio_has_jobs(self):
        self.keychain.value = "valid"
        self.busy = True
        with self.assertRaises(ComputeConflict): self.run_async(self.manager.stop(81))
        with self.assertRaises(ComputeConflict): self.run_async(self.manager.destroy(81, 81))

    def test_destroy_requires_exact_id_confirmation(self):
        self.keychain.value = "valid"
        with self.assertRaises(ValueError): self.run_async(self.manager.destroy(81, 82))

    def test_deploy_uses_validated_ssh_fields_as_argument_array(self):
        self.keychain.value = "valid"
        repo = Path(self.temp.name) / "repo"
        (repo / "infra").mkdir(parents=True)
        (repo / "infra" / "h3_up.sh").write_text("#!/bin/sh\n")
        self.manager.repo_root = repo
        ssh = Path(self.temp.name) / ".ssh"
        ssh.mkdir()
        (ssh / "id_ed25519").write_text("dummy")
        class Process: pid = 321; stdout = None
        with patch("pathlib.Path.home", return_value=Path(self.temp.name)), \
             patch("local_studio.compute.manager.ComputeManager._r2_configured", return_value=True), \
             patch("local_studio.compute.manager.subprocess.Popen", return_value=Process()) as popen, \
             patch("local_studio.compute.manager.asyncio.create_task", side_effect=lambda coro: coro.close()):
            result = self.run_async(self.manager.start_deploy(81))
        args = popen.call_args.args[0]
        self.assertEqual(args[:5], ["bash", str(repo / "infra" / "h3_up.sh"), "--host", "root@gpu.example", "--port"])
        self.assertEqual(args[5], "2201")
        self.assertEqual(args[6:], ["--identity", str(ssh / "id_ed25519"), "--qwen", "disabled"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertNotIn("HF_TOKEN", popen.call_args.kwargs["env"])
        self.assertNotIn("VAST_API_KEY", popen.call_args.kwargs["env"])
        self.assertEqual(result["state"], "running")

    def test_deploy_without_r2_snapshot_installs_from_scratch(self):
        # A new Studio owner has no R2 snapshot and no tokens: the open weights come from Hugging Face.
        self.keychain.value = "valid"
        repo = Path(self.temp.name) / "repo"
        (repo / "scripts").mkdir(parents=True)
        (repo / "scripts" / "h3_new_server.sh").write_text("#!/bin/sh\n")
        self.manager.repo_root = repo
        ssh = Path(self.temp.name) / ".ssh"
        ssh.mkdir()
        (ssh / "id_ed25519").write_text("dummy")
        class Process: pid = 322; stdout = None
        with patch("pathlib.Path.home", return_value=Path(self.temp.name)), \
             patch("local_studio.compute.manager.ComputeManager._r2_configured", return_value=False), \
             patch("local_studio.compute.manager.subprocess.Popen", return_value=Process()) as popen, \
             patch("local_studio.compute.manager.asyncio.create_task", side_effect=lambda coro: coro.close()):
            result = self.run_async(self.manager.start_deploy(81))
        self.assertEqual(popen.call_args.args[0][1], str(repo / "scripts" / "h3_new_server.sh"))
        self.assertEqual(result["state"], "running")

    def test_current_tunnel_endpoint_can_be_stopped_explicitly(self):
        self.keychain.value = "valid"
        tunnel = Path(self.temp.name) / "Library" / "Application Support" / "MiniMax H3" / "tunnel.sh"
        tunnel.parent.mkdir(parents=True)
        tunnel.write_text('SERVER_HOST="vast-host.example"\nSERVER_PORT="2222"\n')
        FakeClient.host = "vast-host.example"
        FakeClient.port = 2222
        with patch("pathlib.Path.home", return_value=Path(self.temp.name)):
            result = self.run_async(self.manager.stop(81))
            self.assertEqual(result["state"], "stopping")
        FakeClient.host = "gpu.example"
        FakeClient.port = 2201

    def test_current_tunnel_server_must_be_stopped_before_destroy(self):
        self.keychain.value = "valid"
        tunnel = Path(self.temp.name) / "Library" / "Application Support" / "MiniMax H3" / "tunnel.sh"
        tunnel.parent.mkdir(parents=True)
        tunnel.write_text('SERVER_HOST="vast-host.example"\nSERVER_PORT="2222"\n')
        FakeClient.host = "vast-host.example"
        FakeClient.port = 2222
        with patch("pathlib.Path.home", return_value=Path(self.temp.name)):
            with self.assertRaises(ComputeConflict): self.run_async(self.manager.destroy(81, 81))
        FakeClient.host = "gpu.example"
        FakeClient.port = 2201

    def test_start_does_not_issue_provider_call_for_running_server(self):
        self.keychain.value = "valid"
        with self.assertRaises(ComputeConflict): self.run_async(self.manager.start(81))

    def test_ambiguous_rent_is_reconciled_and_never_automatically_retried(self):
        self.keychain.value = "valid"
        FakeClient.ambiguous = True
        with self.assertRaises(ComputeConflict): self.run_async(self.manager.rent(12, 1.5))
        self.assertEqual(FakeClient.create_calls, 1)
        FakeClient.ambiguous = False
        with self.assertRaises(ComputeConflict): self.run_async(self.manager.rent(12, 1.5))
        self.assertEqual(FakeClient.create_calls, 1)

    def test_deployment_logs_are_redacted_before_persistence(self):
        log = Path(self.temp.name) / "deploy.log"
        with self.manager._connect() as db:
            db.execute("INSERT INTO compute_operations(id,kind,state,created_at,updated_at,log_path) VALUES(?,?,?,?,?,?)",
                       ("a" * 32, "deploy", "running", "now", "now", str(log)))
        self.manager._persist_log_stream("a" * 32, io.BytesIO(b"starting hf_abcdefghijklmnopqrstuvwxyz123456\nBearer private-token\n"))
        contents = log.read_text()
        self.assertNotIn("hf_abcdefghijklmnopqrstuvwxyz123456", contents)
        self.assertNotIn("private-token", contents)
        self.assertIn("[redacted]", contents)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


class WatchDeployTests(unittest.TestCase):
    """_watch_deploy must accept the GPU API health shape {"status":"ok","engine":"ready"}."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = ComputeManager(Path(self.temp.name), keychain=FakeKeychain(),
                                      client_factory=FakeClient, gpu_url="http://gpu.test")
        self.manager.HEALTH_RETRY_S = 0

    def tearDown(self): self.temp.cleanup()

    def _deploy_row(self, op_id="b" * 32, state="running", instance_id=81):
        with self.manager._connect() as db:
            db.execute("INSERT INTO compute_operations(id,kind,state,instance_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (op_id, "deploy", state, instance_id, "now", "now"))
            db.execute("INSERT OR IGNORE INTO compute_instances(id,state,updated_at,deployment_state) VALUES(?,?,?,?)",
                       (instance_id, "running", "now", "installing"))
        return op_id

    def _watch(self, handler, exit_code=0):
        import httpx
        op_id = self._deploy_row()
        class Process:
            def wait(self): return exit_code
        real = httpx.AsyncClient
        factory = lambda *a, **k: real(transport=httpx.MockTransport(handler), timeout=k.get("timeout"))
        with patch("local_studio.compute.manager.httpx.AsyncClient", side_effect=factory):
            asyncio.run(self.manager._watch_deploy(op_id, Process()))
        with self.manager._connect() as db:
            instance = db.execute("SELECT deployment_state FROM compute_instances WHERE id=81").fetchone()[0]
        return self.manager.operation(op_id)["state"], instance

    def test_engine_ready_marks_deploy_ready(self):
        import httpx
        state, instance = self._watch(lambda request: httpx.Response(200, json={"status": "ok", "engine": "ready"}))
        self.assertEqual((state, instance), ("ready", "ready"))

    def test_health_is_retried_until_engine_ready(self):
        import httpx
        calls = []
        def handler(request):
            calls.append(1)
            return httpx.Response(503 if len(calls) < 3 else 200, json={"status": "ok", "engine": "ready"})
        state, _ = self._watch(handler)
        self.assertEqual(state, "ready")
        self.assertEqual(len(calls), 3)

    def test_unhealthy_engine_needs_reconciliation_and_failed_exit_fails(self):
        import httpx
        state, _ = self._watch(lambda request: httpx.Response(503, json={"detail": "down"}))
        self.assertEqual(state, "needs_reconciliation")
        self.temp.cleanup(); self.setUp()
        state, _ = self._watch(lambda request: httpx.Response(200, json={"engine": "ready"}), exit_code=5)
        self.assertEqual(state, "failed")

    def test_needs_reconciliation_deploy_does_not_block_stop_destroy_or_redeploy(self):
        self.manager.keychain.value = "valid"
        self._deploy_row(state="needs_reconciliation")
        self.assertFalse(self.manager._deployment_in_progress())
        self.assertEqual(asyncio.run(self.manager.stop(81))["state"], "stopping")
        self.assertEqual(asyncio.run(self.manager.destroy(81, 81))["state"], "destroyed")

    def test_running_deploy_with_dead_installer_does_not_block_stop(self):
        self.manager.keychain.value = "valid"
        self._deploy_row(state="running")
        with self.manager._connect() as db:
            db.execute("UPDATE compute_operations SET pid=? WHERE kind='deploy'", (2 ** 22 + 12345,))
        self.assertFalse(self.manager._deployment_in_progress())
        self.assertEqual(asyncio.run(self.manager.stop(81))["state"], "stopping")

    def test_live_deploy_still_blocks_stop(self):
        self.manager.keychain.value = "valid"
        self._deploy_row(state="starting")
        with self.assertRaises(ComputeConflict): asyncio.run(self.manager.stop(81))


if __name__ == "__main__": unittest.main()
