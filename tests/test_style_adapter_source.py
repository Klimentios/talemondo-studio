"""Style LoRA availability on the Mac comes from GPU /api/capabilities, not a Mac env var."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

try:
    import httpx
    from local_studio import main as local_main
except ImportError:  # pragma: no cover
    httpx = None
    local_main = None

from local_studio.store import StudioStore

ROOT = Path(__file__).resolve().parents[1]


class SourceTests(unittest.TestCase):
    def test_mac_does_not_read_the_gpu_env_var(self):
        for name in ("local_studio/main.py", "local_studio/store.py"):
            self.assertNotIn("H3_ENABLED_STYLE_ADAPTERS", (ROOT / name).read_text(), name)


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class GpuStyleAdapterTests(unittest.TestCase):
    def _post(self, capabilities):
        with tempfile.TemporaryDirectory() as temp:
            original_store, original_url, original_transport = local_main.ctx.store, local_main.ctx.gpu_url, local_main.ctx.gpu_transport
            local_main.ctx.store = StudioStore(Path(temp))
            local_main.ctx.gpu_url = "http://gpu"
            local_main.ctx.gpu_transport = type("T", (), {"capabilities": capabilities})()
            try:
                async def exercise():
                    transport = httpx.ASGITransport(app=local_main.app)
                    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                        asset = (await client.post("/api/assets", files={
                            "file": ("hero.png", b"\x89PNG\r\n\x1a\n" + b"x" * 100, "image/png")})).json()
                        return await client.post("/api/jobs", json={
                            "prompt": "<Picture 1> waves", "asset_ids": [asset["asset_id"]],
                            "settings": {"style_adapter": "style3d", "mode": "animation3d"}})
                return asyncio.run(exercise())
            finally:
                local_main.ctx.store, local_main.ctx.gpu_url, local_main.ctx.gpu_transport = original_store, original_url, original_transport

    def test_lora_allowed_when_gpu_reports_it(self):
        response = self._post(AsyncMock(return_value={"style_adapters": ["style3d"]}))
        self.assertEqual(response.status_code, 202, response.text)

    def test_lora_refused_when_gpu_lacks_it_or_is_unreachable(self):
        with patch.dict("os.environ", {"H3_ENABLED_STYLE_ADAPTERS": "style3d"}):
            for capabilities in (AsyncMock(return_value={"style_adapters": []}),
                                 AsyncMock(side_effect=RuntimeError("offline"))):
                response = self._post(capabilities)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("недоступна", response.json()["detail"])

    def test_gpu_style_adapters_fallback(self):
        original_url, original_transport = local_main.ctx.gpu_url, local_main.ctx.gpu_transport
        try:
            local_main.ctx.gpu_url = ""
            self.assertEqual(asyncio.run(local_main.ctx.style_adapters()), set())
            local_main.ctx.gpu_url = "http://gpu"
            local_main.ctx.gpu_transport = type("T", (), {"capabilities": AsyncMock(return_value={"style_adapters": ["style3d", 5]})})()
            self.assertEqual(asyncio.run(local_main.ctx.style_adapters()), {"style3d"})
        finally:
            local_main.ctx.gpu_url, local_main.ctx.gpu_transport = original_url, original_transport


@unittest.skipIf(httpx is None, "FastAPI/httpx runtime dependencies not installed")
class AutoLoraTests(unittest.TestCase):
    """Audit 42.3C №28 / 42.3A №11: real LoRA list always reaches the store; offline "auto" stays "auto"."""

    def _create(self, capabilities, settings):
        from unittest.mock import MagicMock
        original = local_main.ctx.store, local_main.ctx.gpu_url, local_main.ctx.gpu_transport
        local_main.ctx.gpu_url = "http://gpu"
        local_main.ctx.gpu_transport = type("T", (), {"capabilities": capabilities})()
        local_main.ctx.store = MagicMock()
        local_main.ctx.store.create_job.return_value = {"job_id": "x"}
        try:
            async def exercise():
                transport = httpx.ASGITransport(app=local_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                    return await client.post("/api/jobs", json={"prompt": "p", "settings": settings})
            asyncio.run(exercise())
            return local_main.ctx.store.create_job.call_args.kwargs["style_adapters"]
        finally:
            local_main.ctx.store, local_main.ctx.gpu_url, local_main.ctx.gpu_transport = original

    def test_none_style_still_receives_gpu_adapters(self):
        adapters = self._create(AsyncMock(return_value={"style_adapters": ["character_swap"]}), {"style_adapter": "none"})
        self.assertEqual(adapters, {"character_swap"})
        self.assertIsNone(self._create(AsyncMock(side_effect=RuntimeError("offline")), {"style_adapter": "none"}))

    def test_offline_auto_is_kept_and_resolved_at_submit(self):
        import json
        from local_studio.worker import HttpGpuTransport
        from tests.gpu_caps import serving_caps
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "a.png"
            path.write_bytes(b"test")
            import hashlib
            asset = store.add_asset(kind="image", role="hero", original_name="a.png", mime="image/png", size=4,
                                    sha256=hashlib.sha256(b"test").hexdigest(), path=path)
            job = store.create_job(prompt="<Picture 1> waves", asset_ids=[asset["asset_id"]],
                                   settings={"style_adapter": "auto", "mode": "animation3d"}, style_adapters=None)
            self.assertEqual(json.loads(job["settings_json"])["style_adapter"], "auto")
            online = store.create_job(prompt="<Picture 1> waves", asset_ids=[asset["asset_id"]],
                                      settings={"style_adapter": "auto", "mode": "animation3d"}, style_adapters={"style3d"})
            self.assertEqual(json.loads(online["settings_json"])["style_adapter"], "style3d")
            sent = []

            def handler(request):
                if request.url.path == "/api/jobs":
                    sent.append(request.read())
                    return httpx.Response(202, json={"job_id": "r1"})
                return httpx.Response(404)
            original = httpx.AsyncClient

            def client(*args, **kwargs):
                kwargs["transport"] = httpx.MockTransport(serving_caps(handler, {"style_adapters": ["style3d"]}))
                return original(*args, **kwargs)
            with patch("httpx.AsyncClient", client):
                asyncio.run(HttpGpuTransport("http://gpu").submit(job, store))
            self.assertIn(b'name="style_adapter"\r\n\r\nstyle3d', sent[0])


if __name__ == "__main__":
    unittest.main()
