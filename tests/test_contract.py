import unittest

from app.contract import GenerationRequest, frame_count, reference_prompt, resolution


class ResolutionTests(unittest.TestCase):
    def test_landscape_native_resolution(self):
        self.assertEqual(resolution("16:9", "native"), (1344, 768))

    def test_portrait_native_resolution(self):
        self.assertEqual(resolution("9:16", "native"), (768, 1344))

    def test_rejects_unknown_quality(self):
        with self.assertRaises(ValueError):
            resolution("16:9", "4k")

    def test_node_backed_presets_and_custom_size(self):
        self.assertEqual(resolution("9:16", "compact"), (544, 960))
        self.assertEqual(resolution("16:9", "quick"), (1216, 672))
        self.assertEqual(resolution("16:9", "balanced"), (1280, 736))
        self.assertEqual(resolution("16:9", "max"), (1344, 768))
        self.assertEqual(resolution("9:16", "quick"), (672, 1216))
        self.assertEqual(resolution("16:9", "custom", 0.4), (864, 480))
        with self.assertRaises(ValueError):
            resolution("16:9", "custom", 1.0)


class DurationTests(unittest.TestCase):
    def test_five_seconds_snaps_to_h3_frame_grid(self):
        self.assertEqual(frame_count(5), 124)

    def test_four_seconds_is_the_official_minimum(self):
        # H3 README: 4–15 s; 4 s snaps to 107 frames on the 17k+5 grid, 5–15 s unchanged.
        self.assertEqual(frame_count(4), 107)
        self.assertEqual([frame_count(s) for s in (5, 10, 15)], [124, 243, 362])

    def test_rejects_duration_outside_supported_range(self):
        for value in (3, 16):
            with self.subTest(value=value), self.assertRaises(ValueError):
                frame_count(value)


class ReferencePromptTests(unittest.TestCase):
    def test_converts_all_reference_aliases_to_official_labels(self):
        self.assertEqual(
            reference_prompt(
                "@ref1 follows @video1; voice @audio1; soundtrack @video1audio",
                video_audio_indices=(1,),
            ),
            "<Picture 1> follows <Video 1>; voice <Audio 2>; soundtrack <Audio 1>",
        )

    def test_does_not_change_email_addresses(self):
        self.assertEqual(
            reference_prompt("mail user@ref1.com"),
            "mail user@ref1.com",
        )


class RequestTests(unittest.TestCase):
    def test_normalizes_user_request(self):
        request = GenerationRequest(
            prompt="  A cinematic scene  ",
            duration=5,
            aspect_ratio="9:16",
            quality="native",
            profile="bf16",
            steps=25,
            seed=1234,
        )
        self.assertEqual(request.prompt, "A cinematic scene")
        self.assertEqual((request.width, request.height), (768, 1344))
        self.assertEqual(request.length, 124)
        self.assertEqual(request.profile, "bf16")
        self.assertEqual(request.steps, 25)
        self.assertEqual(request.seed, 1234)

    def test_custom_geometry_and_reference_detail(self):
        request = GenerationRequest(
            "hero", 5, "16:9", "custom", megapixels=0.4,
            ref_image_size="match", steps=24, seed=42,
        )
        self.assertEqual((request.width, request.height), (864, 480))
        self.assertEqual(request.ref_image_size, "match")
        with self.assertRaises(ValueError):
            GenerationRequest("hero", 5, "16:9", "native", ref_image_size="large")

    def test_rejects_blank_prompt(self):
        with self.assertRaises(ValueError):
            GenerationRequest("  ", 5, "16:9", "480p")

    def test_steps_cover_turbo_and_quality(self):
        for steps in (4, 8, 24, 50):
            self.assertEqual(GenerationRequest("hero", 5, "16:9", "native", steps=steps).steps, steps)
        for steps in (3, 51):
            with self.subTest(steps=steps), self.assertRaisesRegex(ValueError, "от 4 до 50"):
                GenerationRequest("hero", 5, "16:9", "native", steps=steps)


if __name__ == "__main__":
    unittest.main()
