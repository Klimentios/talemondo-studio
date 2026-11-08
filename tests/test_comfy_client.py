import asyncio
import json
import unittest
from unittest import mock

import httpx

from app import comfy_client
from app.comfy_client import ComfyClient, _find_video


class VideoOutputTests(unittest.TestCase):
    def test_finds_mp4_reported_in_images_for_comfy_video_output(self):
        item = {"filename": "clip.mp4", "subfolder": "video", "type": "output"}
        self.assertEqual(_find_video({"92": {"images": [item]}}), item)

    def test_ignores_non_video_outputs(self):
        self.assertIsNone(_find_video({"1": {"images": [{"filename": "preview.png"}]}}))

    def test_prefers_generated_output_over_uploaded_control_video(self):
        control = {"filename": "control.mp4", "subfolder": "", "type": "input"}
        generated = {
            "filename": "generated.mp4",
            "subfolder": "video",
            "type": "output",
        }
        outputs = {
            "300": {"images": [control]},
            "92": {"images": [generated]},
        }

        self.assertEqual(_find_video(outputs), generated)

    def test_input_video_alone_is_not_a_finished_result(self):
        control = {"filename": "control.mp4", "type": "input"}
        self.assertIsNone(_find_video({"300": {"images": [control]}}))


class ObjectInfoCacheTests(unittest.TestCase):
    def setUp(self):
        self.reads = 0
        def handler(request):
            if request.url.path == "/object_info":
                self.reads += 1
                return httpx.Response(200, content=json.dumps({"KSampler": {"input": {}}}).encode())
            return httpx.Response(400, json={"error": "bad graph"})
        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        patcher = mock.patch.object(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = ComfyClient("http://comfy.test")

    def test_one_read_serves_a_whole_job_and_copies_are_independent(self):
        async def run():
            first, second = await asyncio.gather(self.client.object_info(), self.client.object_info())
            first["KSampler"]["input"]["changed"] = True
            third = await self.client.object_info()
            return second, third
        second, third = asyncio.run(run())
        self.assertEqual(self.reads, 1)
        self.assertEqual(second, {"KSampler": {"input": {}}})
        self.assertEqual(third, {"KSampler": {"input": {}}})

    def test_expires_after_ttl(self):
        async def run():
            await self.client.object_info()
            with mock.patch.object(comfy_client.time, "monotonic",
                                   return_value=comfy_client.time.monotonic() + comfy_client.OBJECT_INFO_TTL_S + 1):
                await self.client.object_info()
        asyncio.run(run())
        self.assertEqual(self.reads, 2)

    def test_refused_prompt_drops_the_cache(self):
        async def run():
            await self.client.object_info()
            async def no_websocket(client_id, ready):
                ready.set()
            with mock.patch.object(self.client, "_watch_progress", no_websocket):
                with self.assertRaises(httpx.HTTPStatusError):
                    await self.client.queue({"1": {"class_type": "Missing", "inputs": {}}})
            await self.client.object_info()
        asyncio.run(run())
        self.assertEqual(self.reads, 2)


if __name__ == "__main__":
    unittest.main()
