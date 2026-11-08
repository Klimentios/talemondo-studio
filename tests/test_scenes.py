import asyncio
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_studio.scenes import make_router
from local_studio.store import StudioStore


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class LastFrameTests(unittest.TestCase):
    def test_last_frame_of_a_finished_clip_becomes_a_scene_frame(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            clip = store.results_dir / "done.mp4"
            # 2 s: first second red, last second blue -> the extracted frame must be blue.
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=320x240:d=1:r=24",
                            "-f", "lavfi", "-i", "color=blue:s=320x240:d=1:r=24", "-filter_complex", "[0][1]concat=n=2:v=1",
                            "-pix_fmt", "yuv420p", str(clip)], check=True)
            path = store.assets_dir / "hero.png"
            path.write_bytes(b"x")
            hero = store.add_asset(kind="image", role="Лицо героя", original_name="hero.png", mime="image/png",
                                   size=1, sha256="x", path=path)
            job = store.create_job(prompt="<Picture 1> walks", asset_ids=[hero["asset_id"]], settings={})
            store.transition(job["job_id"], "downloading", remote_job_id="r1")
            store.transition(job["job_id"], "completed", output_path=str(clip))
            route = next(r for r in make_router(store).routes if r.path.endswith("/last-frame"))
            asset = asyncio.run(route.endpoint(job["job_id"]))
            self.assertEqual(asset["role"], "Кадр сцены")
            frame = Path(asset["path"])
            rgb = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(frame), "-vf", "scale=1:1",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
            self.assertGreater(rgb[2], 150)   # blue
            self.assertLess(rgb[0], 80)       # not red
            again = asyncio.run(route.endpoint(job["job_id"]))
            self.assertEqual(again["asset_id"], asset["asset_id"])  # same clip -> same asset (sha256)
            self.assertEqual(len([a for a in store.list_assets() if a["role"] == "Кадр сцены"]), 1)
            self.assertEqual(list(store.assets_dir.glob(".scene-frame-*")), [])

    def test_unfinished_job_is_refused(self):
        from fastapi import HTTPException
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            route = next(r for r in make_router(store).routes if r.path.endswith("/last-frame"))
            with self.assertRaises(HTTPException):
                asyncio.run(route.endpoint("missing"))


class SceneFrameGraphTests(unittest.TestCase):
    def test_official_prompt_gets_no_extra_anchor_sentence(self):
        from app.contract import GenerationRequest
        from app.graph import build_prompt
        from app.h3_ir import Reference, build_skeleton, shot_starts
        from tests.test_h3_ir import good_ir
        sk = build_skeleton([Reference("image", 1, "Кадр сцены"), Reference("image", 2, "Персонаж целиком", "ADAM")])
        prompt = good_ir(sk, shot_starts([5, 5])).replace("<Subject 2>", "<Subject 1>").replace("<Subject 3>", "<Subject 1>")
        graph = build_prompt(GenerationRequest(prompt, 10, "16:9", "quick", steps=24, seed=1), ["frame.png", "adam.png"],
                             guide_frames=[(1, 0)])
        text = next(v for n in graph.values() for v in n.get("inputs", {}).values() if isinstance(v, str) and "subject_definitions" in v)
        self.assertTrue(text.startswith("subject_definitions:"))
        self.assertTrue(any(n["class_type"] == "MiniMaxH3AddGuide" for n in graph.values()))


if __name__ == "__main__":
    unittest.main()


class SceneFrameJobTests(unittest.TestCase):
    def test_first_cut_starts_from_scene_frame_and_next_cut_without_it(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            ids = {}
            for name, role in (("frame.png", "Кадр сцены"), ("vega.png", "Персонаж целиком")):
                path = store.assets_dir / name
                path.write_bytes(name.encode())
                ids[role] = store.add_asset(kind="image", role=role, original_name=name, mime="image/png",
                                            size=len(name), sha256=name, path=path)["asset_id"]
            first = store.create_job(
                prompt="<Picture 2> walks to the sofa. REFERENCE ROLE CONTRACT: <Picture 1> anchors this scene composition at its requested frame.",
                asset_ids=[ids["Кадр сцены"], ids["Персонаж целиком"]],
                reference_roles={v: k for k, v in ids.items()}, reference_owners={ids["Персонаж целиком"]: "VEGA"},
                reference_numbers={ids["Кадр сцены"]: 1, ids["Персонаж целиком"]: 2},
                settings={"quality": "quick", "guide_frames": [[1, 0]]})
            self.assertEqual(json.loads(first["settings_json"])["guide_frames"], [[1, 0]])
            # The next cut: the same frame becomes the location reference, no frame-0 anchor.
            second = store.create_job(
                prompt="<Picture 2> sits down. REFERENCE ROLE CONTRACT: <Picture 1> defines the location and set dressing only.",
                asset_ids=[ids["Кадр сцены"], ids["Персонаж целиком"]],
                reference_roles={ids["Кадр сцены"]: "Окружение", ids["Персонаж целиком"]: "Персонаж целиком"},
                reference_owners={ids["Персонаж целиком"]: "VEGA"},
                reference_numbers={ids["Кадр сцены"]: 1, ids["Персонаж целиком"]: 2},
                settings={"quality": "quick", "guide_frames": []})
            self.assertEqual([i["role"] for i in json.loads(second["manifest_json"])["images"]], ["Окружение", "Персонаж целиком"])
            self.assertEqual(json.loads(second["settings_json"])["guide_frames"], [])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg required")
class DarkEndTests(unittest.TestCase):
    def test_a_take_that_fades_to_black_gives_no_start_frame(self):
        from local_studio.scenes import last_frame_png
        with tempfile.TemporaryDirectory() as temp:
            clip, frame = Path(temp) / "fade.mp4", Path(temp) / "last.png"
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=0x202838:s=320x240:d=1:r=24",
                            "-f", "lavfi", "-i", "color=black:s=320x240:d=1:r=24", "-filter_complex", "[0][1]concat=n=2:v=1",
                            "-pix_fmt", "yuv420p", str(clip)], check=True)
            with self.assertRaisesRegex(ValueError, "почти чёрный"):
                last_frame_png(clip, frame)
            night = Path(temp) / "night.mp4"  # a dim night room is not a fade
            subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=0x202838:s=320x240:d=1:r=24",
                            "-pix_fmt", "yuv420p", str(night)], check=True)
            last_frame_png(night, frame)
