"""Director presets reach IR prompts as plain sentences, once, inside [Shot 1] (audit 05.11)."""
import unittest

from app.director_config import director_config_sentences, place_in_first_shot

CONFIG = {
    "lens": {"id": "lens.portrait", "parameters": {"focal_length_mm": 50, "aperture_f": 2, "look": "portrait"}},
    "focus": {"id": "focus.eyes", "parameters": {"depth": "shallow", "target": "eyes"}},
    "lighting": {"id": "lighting.night", "parameters": {"time_of_day": "night", "source": "practical",
                                                        "direction": "side", "softness": "soft",
                                                        "temperature": "warm", "scheme": "low_key"}},
    "color": {"id": "color.warm", "parameters": {"temperature": "warm", "saturation": "low", "contrast": "high"}},
    "style": {"id": "style.film", "parameters": {"rendering": "film", "texture": "subtle_grain"}},
    "movement": {"id": "movement.dolly", "parameters": {"movement": "dolly"}},
}


class DirectorSentenceTests(unittest.TestCase):
    def test_settings_become_plain_sentences_without_key_value_lines(self):
        text = director_config_sentences(CONFIG, skip=frozenset({"camera", "movement", "shot", "acting"}))
        self.assertEqual(text, "Shot on a 50 mm portrait lens at f/2, shallow depth of field, focus on the eyes. "
                               "Night, soft warm practical-lamp light from the side, low-key lighting. "
                               "Warm colour grade, low saturation, high contrast. Film look with subtle grain texture.")
        self.assertNotIn("=", text)
        self.assertNotIn("dolly", text)  # the camera move belongs to the shot's own opening

    def test_grain_is_not_asked_from_the_model_when_the_film_look_node_adds_it(self):
        text = director_config_sentences(CONFIG, skip=frozenset({"camera", "movement", "shot", "acting"}), node_grain=True)
        self.assertNotIn("grain", text)
        self.assertIn("Film look.", text)

    def test_sentences_follow_the_shot_opening_sentence(self):
        prompt = "detailed_description:\nPhotoreal.\n[Shot 1] A medium shot of the deck. He grips the rope.\n"
        out = place_in_first_shot(prompt, "Night.")
        self.assertIn("[Shot 1] A medium shot of the deck. Night. He grips the rope.", out)
        self.assertEqual(place_in_first_shot(prompt, ""), prompt)

    def test_without_shot_header_the_section_start_is_used(self):
        out = place_in_first_shot("detailed_description:\nPhotoreal.\n", "Night.")
        self.assertTrue(out.startswith("detailed_description:\nNight. Photoreal."))
        with self.assertRaisesRegex(ValueError, "detailed_description"):
            place_in_first_shot("plain prompt", "Night.")


if __name__ == "__main__":
    unittest.main()
