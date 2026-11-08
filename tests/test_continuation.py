"""«Продолжение» (continuous take): last 22 frames + sound of a clip anchored at frame 0, not a Ref2VA video."""
import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.continuation import (CLIP_FRAMES, LEGACY_SENTENCE, NODES, prepare_continuation_video,
                              split_continuation)
from app.contract import GenerationRequest
from app.graph import build_prompt
from app.h3_ir import Reference, build_skeleton, scenarist_messages
from local_studio.store import StudioStore
from local_studio.worker import gpu_form_fields

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII=")
CLIP = {"file": "continuation.mp4", "frames": 72, "audio": True}


def request(prompt="<Picture 1> keeps walking down the corridor."):
    return GenerationRequest(prompt, 5, "9:16", "compact", steps=24, seed=1)


def clip(path: Path, seconds: float, audio: bool = True) -> bytes:
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=s=320x240:r=30:d={seconds}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", *(["-c:a", "aac"] if audio else []), str(path)]
    subprocess.run(command, check=True)
    return path.read_bytes()


class ContinuationGraphTests(unittest.TestCase):
    def test_official_chain_last_22_frames_and_their_sound_at_frame_0(self):
        graph = build_prompt(request(), ["hero.png"], continuation=CLIP)
        self.assertEqual(graph["350"], {"inputs": {"file": "continuation.mp4"}, "class_type": "LoadVideo",
                                        "_meta": {"title": "@continuation"}})
        self.assertEqual(graph["351"]["inputs"]["video"], ["350", 0])
        self.assertEqual(graph["352"]["inputs"], {"image": ["351", 0], "batch_index": -22, "length": 22})
        self.assertEqual(graph["353"]["class_type"], "TrimAudioDuration")
        self.assertEqual(graph["353"]["inputs"]["audio"], ["351", 1])
        self.assertAlmostEqual(graph["353"]["inputs"]["start_index"], (72 - 22) / 24, places=3)
        self.assertAlmostEqual(graph["353"]["inputs"]["duration"], 22 / 24, places=3)
        guide = graph["354"]["inputs"]
        self.assertEqual(graph["354"]["class_type"], "MiniMaxH3AddGuide")
        self.assertEqual((guide["frame_idx"], guide["image"], guide["audio"], guide["latent"], guide["positive"]),
                         (0, ["352", 0], ["353", 0], ["136", 1], ["136", 0]))
        self.assertEqual((guide["vae"], guide["audio_vae"]), (["119", 0], ["120", 0]))
        self.assertEqual(graph["126"]["inputs"]["conditioning"], ["354", 0])
        ref = graph["136"]["inputs"]
        self.assertFalse(any(key.startswith("ref_videos") for key in ref), "not a Ref2VA <Video N>")
        self.assertTrue(ref["prompt"].startswith(LEGACY_SENTENCE))
        self.assertNotIn("<Video", ref["prompt"])
        self.assertEqual({graph[key]["class_type"] for key in ("350", "351", "352", "353", "354")}, set(NODES))

    def test_scene_guides_chain_after_it_and_cannot_overlap_it(self):
        graph = build_prompt(request("<Picture 1> walks into <Picture 2>."), ["hero.png", "scene.png"],
                             continuation={**CLIP, "audio": False}, guide_frames=[(2, -1)])
        self.assertNotIn("353", graph)
        self.assertNotIn("audio", graph["354"]["inputs"])
        self.assertEqual(graph["320"]["inputs"]["positive"], ["354", 0])
        self.assertEqual(graph["126"]["inputs"]["conditioning"], ["320", 0])
        with self.assertRaisesRegex(ValueError, "продолжение дубля"):
            build_prompt(request(), ["hero.png"], continuation=CLIP, guide_frames=[(1, 10)])

    def test_official_prompt_carries_its_own_wording(self):
        ir = ("subject_definitions:\n<Subject 1> is the hero in <Picture 1>.\n\nsummary:\n[video continuation] x\n\n"
              "retention_analysis:\nx\n\ndetailed_description:\n[Shot 1] x\n\noverall_soundscape:\nx\n\n"
              "non_diegetic_music:\nN/A")
        graph = build_prompt(request(ir), ["hero.png"], continuation=CLIP)
        self.assertTrue(graph["136"]["inputs"]["prompt"].startswith("subject_definitions:"))
        self.assertNotIn(LEGACY_SENTENCE, graph["136"]["inputs"]["prompt"])

    def test_without_it_the_graph_is_unchanged(self):
        self.assertEqual(build_prompt(request(), ["hero.png"]), build_prompt(request(), ["hero.png"], continuation=None))

    def test_scenarist_skeleton_has_task_type_and_no_label(self):
        sk = build_skeleton([Reference("image", 1, "Лицо героя", "VEGA"), Reference("video", 1, "Продолжение")])
        self.assertIn("video continuation", sk.task_types)
        self.assertNotIn("<Video 1>", sk.labels)
        user = scenarist_messages("brief", sk, ["[Shot 1]"])[1]["content"]
        self.assertIn("CONTINUATION:", user)


class SplitTests(unittest.TestCase):
    def test_split_and_refusals(self):
        manifest = {"version": 1, "images": [], "audios": [],
                    "videos": [{"asset_id": "m", "role": "Движение"}, {"asset_id": "c", "role": "Продолжение"}]}
        rest, refs, item = split_continuation(manifest, ["motion", "cont"])
        self.assertEqual((rest["videos"], refs, item), ([{"asset_id": "m", "role": "Движение"}], ["motion"], "cont"))
        self.assertEqual(split_continuation({"videos": []}, [])[2], None)
        with self.assertRaisesRegex(ValueError, "последним"):
            split_continuation({"videos": manifest["videos"][::-1]}, ["a", "b"])
        with self.assertRaisesRegex(ValueError, "одного видео"):
            split_continuation({"videos": [manifest["videos"][1]] * 2}, ["a", "b"])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class PrepareTests(unittest.TestCase):
    def test_tail_at_24_fps_with_aligned_audio_and_short_clip_refusal(self):
        with tempfile.TemporaryDirectory() as temp:
            data = clip(Path(temp, "a.mp4"), 4.0)
            result = prepare_continuation_video(("a.mp4", data, "video/mp4"), request())
            self.assertTrue(result["audio"])
            self.assertTrue(70 <= result["frames"] <= 74, result["frames"])  # last ~3 s at 24 fps
            name, payload, mime = result["upload"]
            self.assertEqual(mime, "video/mp4")
            out = Path(temp, name)
            out.write_bytes(payload)
            probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                               "stream=codec_type,width,height,duration", "-of", "json", str(out)],
                                              capture_output=True, text=True, check=True).stdout)["streams"]
            video = next(s for s in probe if s["codec_type"] == "video")
            audio = next(s for s in probe if s["codec_type"] == "audio")
            self.assertGreaterEqual(video["height"], request().height)  # covers the 9:16 canvas
            self.assertLess(abs(float(audio["duration"]) - result["frames"] / 24), 0.05)
            silent = prepare_continuation_video(("b.mp4", clip(Path(temp, "b.mp4"), 2.0, audio=False), "video/mp4"),
                                                request())
            self.assertFalse(silent["audio"])
            with self.assertRaisesRegex(ValueError, "нужно не меньше 22"):
                prepare_continuation_video(("c.mp4", clip(Path(temp, "c.mp4"), 0.5), "video/mp4"), request())
        self.assertEqual(CLIP_FRAMES, 22)


class StoreTests(unittest.TestCase):
    def _store(self, temp, *assets):
        store = StudioStore(Path(temp))
        ids = []
        for name, kind, role in assets:
            path = store.assets_dir / name
            path.write_bytes(name.encode())
            ids.append(store.add_asset(kind=kind, role=role, original_name=name,
                                       mime="image/png" if kind == "image" else "video/mp4", size=len(name),
                                       sha256=hashlib.sha256(name.encode()).hexdigest(), path=path)["asset_id"])
        return store, ids

    def test_continuation_job_is_accepted_without_a_video_label(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids = self._store(temp, ("hero.png", "image", "Персонаж целиком"), ("prev.mp4", "video", "Продолжение"))
            job = store.create_job(prompt="<Picture 1> keeps walking.", asset_ids=ids, settings={"quality": "quick"},
                                   reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Продолжение"})
            self.assertNotIn("<Video", job["compiled_prompt"])
            self.assertNotIn("VIDEO PERFORMANCE DIRECTION", job["compiled_prompt"])
            self.assertEqual(json.loads(job["manifest_json"])["videos"][0]["role"], "Продолжение")
            self.assertEqual(json.loads(gpu_form_fields(job)["manifest_json"])["videos"][0]["role"], "Продолжение")

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as temp:
            store, ids = self._store(temp, ("hero.png", "image", "Персонаж целиком"), ("prev.mp4", "video", "Продолжение"),
                                     ("move.mp4", "video", "Движение и камера"), ("prev2.mp4", "video", "Продолжение"),
                                     ("frame.png", "image", "Кадр сцены"))
            roles = {ids[0]: "Персонаж целиком", ids[1]: "Продолжение", ids[2]: "Движение и камера", ids[3]: "Продолжение",
                     ids[4]: "Кадр сцены"}
            cases = [
                ([ids[0], ids[1]], "<Picture 1> walks after <Video 1>.", {}, "не упоминай"),
                ([ids[0], ids[1], ids[2]], "<Picture 1> walks.", {}, "последним"),
                ([ids[0], ids[1], ids[3]], "<Picture 1> walks.", {}, "одного видео"),
                ([ids[1]], "The corridor continues.", {}, "фото героя"),
                ([ids[0], ids[4], ids[1]], "<Picture 1> walks.", {"guide_frames": [[2, 5]]}, "продолжение дубля"),
            ]
            for asset_ids, prompt, extra, message in cases:
                with self.subTest(message):
                    with self.assertRaisesRegex(ValueError, message):
                        store.create_job(prompt=prompt, asset_ids=asset_ids, settings={"quality": "quick", **extra},
                                         reference_roles={key: roles[key] for key in asset_ids})
            job = store.create_job(prompt="<Picture 1> walks like <Video 1>.", asset_ids=[ids[0], ids[2], ids[1]],
                                   settings={"quality": "quick"},
                                   reference_numbers={ids[0]: 1, ids[2]: 1, ids[1]: 2},
                                   reference_roles={ids[0]: roles[ids[0]], ids[2]: roles[ids[2]], ids[1]: roles[ids[1]]},
                                   style_adapters={"character_swap"})
            self.assertIn("<Video 1>", job["compiled_prompt"])
            self.assertNotIn("<Video 2>", job["compiled_prompt"])


try:
    import httpx
    from app import main as remote_main
except ImportError:  # pragma: no cover
    httpx = None


class FakeComfy:
    def __init__(self):
        self.graph = None
        self.uploads = []

    async def release_models_if_idle(self):
        return None

    async def upload_file(self, filename, data, content_type):
        self.uploads.append(filename)
        return filename

    async def queue(self, graph, prompt_id=None):
        self.graph = graph
        return "comfy-job-1"

    async def object_info(self):
        return {name: {} for name in NODES | {"ApplyMiniMaxH3FirstBlockCache"}}


@unittest.skipUnless(httpx and shutil.which("ffmpeg"), "runtime deps / ffmpeg required")
class GpuApiTests(unittest.TestCase):
    def test_continuation_video_becomes_a_guide_and_the_rest_is_planned_without_it(self):
        with tempfile.TemporaryDirectory() as temp:
            video = clip(Path(temp, "prev.mp4"), 3.0)
            short = clip(Path(temp, "short.mp4"), 0.5)
            fake = FakeComfy()

            def form(data):
                manifest = {"version": 1, "images": [{"asset_id": "hero", "sha256": hashlib.sha256(PNG).hexdigest()}],
                            "videos": [{"asset_id": "prev", "sha256": hashlib.sha256(data).hexdigest(),
                                        "role": "Продолжение"}], "audios": []}
                return {"client_job_id": str(uuid.uuid4()), "manifest_json": json.dumps(manifest),
                        "prompt": "<Picture 1> keeps walking.", "precompiled_prompt": "true", "duration": "5",
                        "quality": "quick", "steps": "24", "seed": "1"}

            async def exercise():
                transport = httpx.ASGITransport(app=remote_main.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.post("/api/jobs", data=form(video), files=[
                        ("images", ("hero.png", PNG, "image/png")), ("videos", ("prev.mp4", video, "video/mp4"))])
                    self.assertEqual(response.status_code, 202, response.text)
                    self.assertEqual(response.json()["director"]["continuation"]["audio"], True)
                    response = await client.post("/api/jobs", data=form(short), files=[
                        ("images", ("hero.png", PNG, "image/png")), ("videos", ("short.mp4", short, "video/mp4"))])
                    self.assertEqual(response.status_code, 422)
                    self.assertIn("22", response.json()["detail"])

            with patch.dict(os.environ, {"H3_JOB_REGISTRY_PATH": f"{temp}/registry.sqlite3"}), \
                    patch.object(remote_main, "comfy", fake):
                asyncio.run(exercise())
            graph = fake.graph
            self.assertEqual(graph["354"]["inputs"]["frame_idx"], 0)
            self.assertTrue(graph["350"]["inputs"]["file"].startswith("continuation-"))
            self.assertFalse(any(key.startswith("ref_videos") for key in graph["136"]["inputs"]))
            self.assertTrue(graph["136"]["inputs"]["prompt"].startswith(LEGACY_SENTENCE))
            caps = remote_main.capabilities_from_nodes({name: {} for name in NODES})
            self.assertTrue(caps["continuation_v1"])
            self.assertFalse(remote_main.capabilities_from_nodes({})["continuation_v1"])


if __name__ == "__main__":
    unittest.main()
