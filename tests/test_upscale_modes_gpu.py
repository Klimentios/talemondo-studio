import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.upscale import make_router
from app.upscale_graph import RIFE_CKPT_PATH, required_nodes


class FakeComfy:
    def __init__(self, nodes):
        self.nodes = nodes

    async def object_info(self):
        return {name: {} for name in self.nodes}


def capabilities_route(nodes):
    return next(r for r in make_router(FakeComfy(nodes), lambda: None).routes if r.path.endswith("/capabilities")).endpoint


class UpscaleModeCapabilityTests(unittest.TestCase):
    def test_each_mode_needs_its_nodes_and_weights(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"H3_COMFY_ROOT": root}):
            route = capabilities_route(required_nodes("upscale_smooth"))
            missing = asyncio.run(route(mode="smooth"))
            self.assertFalse(missing["available"])
            self.assertIn("rife417.pth", missing["missing"])
            rife = Path(root) / RIFE_CKPT_PATH
            rife.parent.mkdir(parents=True)
            rife.write_bytes(b"w")
            self.assertTrue(asyncio.run(route(mode="smooth"))["available"])
            both = asyncio.run(route(mode="upscale_smooth"))
            self.assertIn("seedvr2_3b_fp16.safetensors", both["missing"])  # SeedVR2 weights still absent

    def test_missing_rife_node_is_reported(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"H3_COMFY_ROOT": root}):
            route = capabilities_route(required_nodes("upscale"))
            self.assertIn("RIFE VFI", asyncio.run(route(mode="smooth"))["missing"])

    def test_unknown_mode_is_422(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(capabilities_route(set())(mode="sharpen"))
        self.assertEqual(caught.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
