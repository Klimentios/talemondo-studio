import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from starlette.websockets import WebSocketDisconnect

from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.compute.manager import ComputeManager
from local_studio.compute.router import make_router


class Keychain:
    value = None
    def has(self, _): return self.value is not None
    def get(self, _): return self.value
    def set(self, _, value): self.value = value
    def delete(self, _): self.value = None


class ClientStub:
    async def get_account(self): return {"id":1,"email":"x@example.test","balance":10}


class ComputeRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.keychain = Keychain()
        manager = ComputeManager(Path(self.temp.name), keychain=self.keychain, client_factory=lambda _: ClientStub())
        self.manager = manager
        app = FastAPI()
        app.include_router(make_router(manager))
        self.client = TestClient(app)

    def tearDown(self): self.temp.cleanup()

    def test_status_never_includes_provider_secret(self):
        self.keychain.value = "never-serialize-this"
        response = self.client.get("/api/compute/status", headers={"host":"127.0.0.1:18187"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("never-serialize-this", response.text)
        self.assertEqual(response.json()["balance"], 10)

    def test_status_without_key_is_disconnected(self):
        response = self.client.get("/api/compute/status", headers={"host":"127.0.0.1:18187"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["connected"])

    def test_key_writes_require_same_origin(self):
        headers = {"host":"127.0.0.1:18187", "origin":"http://evil.example"}
        response = self.client.post("/api/compute/provider-key", json={"api_key":"valid-api-key"}, headers=headers)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.keychain.value)

    def test_key_write_requires_origin(self):
        response = self.client.post("/api/compute/provider-key", json={"api_key":"valid-api-key"},
                                    headers={"host":"127.0.0.1:18187"})
        self.assertEqual(response.status_code, 403)

    def test_key_write_rejects_oversized_secret_and_wrong_host(self):
        headers = {"host":"127.0.0.1:18187", "origin":"http://127.0.0.1:18187"}
        too_big = self.client.post("/api/compute/provider-key", json={"api_key":"x" * 8193}, headers=headers)
        self.assertEqual(too_big.status_code, 422)
        wrong_host = self.client.post("/api/compute/provider-key", json={"api_key":"valid-api-key"},
                                      headers={"host":"evil.example", "origin":"http://evil.example"})
        self.assertEqual(wrong_host.status_code, 403)
        self.assertIsNone(self.keychain.value)

    def test_operation_id_is_validated(self):
        response = self.client.get("/api/compute/operations/../../secret", headers={"host":"127.0.0.1:18187"})
        self.assertIn(response.status_code, {404, 422})

    def test_operator_plan_requires_same_origin_and_never_runs_for_invalid_host(self):
        valid = {"host":"127.0.0.1:18187", "origin":"http://127.0.0.1:18187"}
        with patch("local_studio.compute.operator._probe", return_value={"ssh":False}) as probe:
            denied = self.client.post("/api/compute/operator/plan", json={"host":"127.0.0.1", "port":22},
                                      headers={"host":"127.0.0.1:18187", "origin":"http://evil.test"})
            self.assertEqual(denied.status_code, 403)
            invalid = self.client.post("/api/compute/operator/plan", json={"host":"bad;whoami", "port":22}, headers=valid)
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(probe.call_count, 0)
            allowed = self.client.post("/api/compute/operator/plan", json={"host":"127.0.0.1", "port":22}, headers=valid)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(probe.call_count, 1)

    def test_operator_run_uses_only_a_named_runbook(self):
        headers = {"host":"127.0.0.1:18187", "origin":"http://127.0.0.1:18187"}
        self.manager.start_operator_action = AsyncMock(return_value={"operation_id":"a" * 32, "state":"running"})
        denied = self.client.post("/api/compute/operator/run", json={"host":"example.com", "port":22,
                                   "action":"rm -rf /"}, headers=headers)
        self.assertEqual(denied.status_code, 422)
        self.manager.start_operator_action.assert_not_called()
        allowed = self.client.post("/api/compute/operator/run", json={"host":"example.com", "port":22,
                                    "action":"verify"}, headers=headers)
        self.assertEqual(allowed.status_code, 200)
        self.manager.start_operator_action.assert_awaited_once_with("verify", "example.com", 22)

    def test_terminal_rejects_foreign_web_page(self):
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/api/compute/operator/terminal?host=example.com&port=22",
                                                headers={"host":"127.0.0.1:18187", "origin":"http://evil.test"}):
                pass


if __name__ == "__main__": unittest.main()
