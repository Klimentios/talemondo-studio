import unittest

from app.reference_manifest import compile_reference_manifest


class ReferenceManifestTests(unittest.TestCase):
    def test_editor_at_suffix_is_normalized_before_mapping(self):
        result = compile_reference_manifest(
            "<Picture 4@> moves",
            [{"asset_id": "hero", "kind": "image", "number": 4}],
        )
        self.assertEqual(result.prompt, "<Picture 1> moves")

    def test_gap_and_seven_images_remap_without_rebinding(self):
        assets = [{"asset_id": f"id-{n}", "kind": "image", "number": n} for n in range(2, 9)]
        result = compile_reference_manifest("<Picture 2> and <Picture 8>", assets)
        self.assertEqual(result.prompt, "<Picture 1> and <Picture 7>")
        self.assertEqual([a["asset_id"] for a in result.images], [f"id-{n}" for n in range(2, 9)])
        self.assertEqual(result.mapping["<Picture 8>"], "<Picture 7>")

    def test_missing_reference_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "<Picture 2>"):
            compile_reference_manifest("<Picture 2>", [{"asset_id": "a", "kind": "image", "number": 1}])

    def test_video_audio_precedes_standalone_audio(self):
        assets = [
            {"asset_id": "v", "kind": "video", "number": 3, "soundtrack_number": 5},
            {"asset_id": "a", "kind": "audio", "number": 7},
        ]
        result = compile_reference_manifest("<Video 3> <Audio 5> <Audio 7>", assets, use_video_audio=True)
        self.assertEqual(result.prompt, "<Video 1> <Audio 1> <Audio 2>")
        self.assertEqual(result.mapping["<Audio 7>"], "<Audio 2>")

    def test_rejects_duplicate_display_numbers_and_missing_soundtrack(self):
        with self.assertRaises(ValueError):
            compile_reference_manifest("hi", [
                {"asset_id": "a", "kind": "image", "number": 1},
                {"asset_id": "b", "kind": "image", "number": 1},
            ])
        with self.assertRaisesRegex(ValueError, "<Audio 5>"):
            compile_reference_manifest("<Audio 5>", [{"asset_id": "v", "kind": "video", "number": 1}])


if __name__ == "__main__":
    unittest.main()
