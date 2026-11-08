"""Guide frames: negative frame_idx (MiniMaxH3AddGuide semantics) and the official frame-anchor wording."""
import json
import tempfile
import unittest
from pathlib import Path

from app.contract import GenerationRequest
from app.graph import build_prompt, guide_frame_sentence, resolve_guide_frame
from local_studio.store import StudioStore


def request(prompt="<Picture 1> and <Picture 2> in scene"):
    return GenerationRequest(prompt, 5, "9:16", "compact", steps=24)  # 124 frames


class GuideFrameGraphTests(unittest.TestCase):
    def test_negative_index_counts_from_the_end_like_the_node(self):
        self.assertEqual(resolve_guide_frame(-1, 124), 123)
        self.assertEqual(resolve_guide_frame(0, 124), 0)
        self.assertEqual(resolve_guide_frame(-124, 124), 0)
        self.assertEqual(resolve_guide_frame(102, 124, guide_frames=22), 102)
        for bad, frames in ((124, 1), (-125, 1), (103, 22)):
            with self.assertRaisesRegex(ValueError, "вне ролика"):
                resolve_guide_frame(bad, 124, frames)

    def test_negative_index_reaches_the_node_unchanged(self):
        graph = build_prompt(request(), ["face.png", "scene.png"], guide_frames=[(2, -1)])
        self.assertEqual(graph["320"]["inputs"]["frame_idx"], -1)
        self.assertIn("The shot ends on <Picture 2>.", graph["136"]["inputs"]["prompt"])
        with self.assertRaisesRegex(ValueError, "вне ролика"):
            build_prompt(request(), ["face.png", "scene.png"], guide_frames=[(2, -200)])

    def test_official_wording_replaces_the_legacy_anchor_sentence(self):
        self.assertEqual(guide_frame_sentence(1, 0, 124), "The shot begins from <Picture 1>.")
        self.assertEqual(guide_frame_sentence(2, 60, 124), "At 00:02.500, the shot's keyframe corresponds to <Picture 2>.")
        self.assertEqual(guide_frame_sentence(3, 123, 124), "The shot ends on <Picture 3>.")
        prompt = build_prompt(request(), ["face.png", "scene.png"], guide_frames=[(2, 60)])["136"]["inputs"]["prompt"]
        self.assertNotIn("anchors the scene composition", prompt)

    def test_jobs_without_guides_get_no_sentence(self):
        prompt = build_prompt(request(), ["face.png", "scene.png"])["136"]["inputs"]["prompt"]
        self.assertEqual(prompt, "<Picture 1> and <Picture 2> in scene")


class GuideFrameStoreTests(unittest.TestCase):
    def test_store_accepts_last_frame_and_rejects_outside(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StudioStore(Path(temp))
            path = store.assets_dir / "scene.png"
            path.write_bytes(b"frame")
            image = store.add_asset(kind="image", role="Кадр сцены", original_name="scene.png",
                                    mime="image/png", size=5, sha256="frame", path=path)
            args = dict(prompt="<Picture 1> shows the room", asset_ids=[image["asset_id"]],
                        reference_numbers={image["asset_id"]: 1}, reference_roles={image["asset_id"]: "Кадр сцены"})
            job = store.create_job(**args, settings={"quality": "quick", "guide_frames": [[1, -1]]})
            self.assertEqual(json.loads(job["settings_json"])["guide_frames"], [[1, -1]])
            with self.assertRaisesRegex(ValueError, "вне ролика"):
                store.create_job(**args, settings={"quality": "quick", "guide_frames": [[1, -500]]})


if __name__ == "__main__":
    unittest.main()
