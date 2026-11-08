import unittest
from app.voice_contract import normalize_voice_cast


class VoiceContractTests(unittest.TestCase):
    def test_saved_cast_cannot_override_explicit_scene_voice(self):
        story = '<Picture 2> defines ORION.\nORION speaks in a low, controlled male voice.\nORION: "Wait."'
        prompt = story + '\n\nVOICE CAST FOR Орион: Use a child voice, never an adult voice.'
        manifest = {"images": [{"owner": ""}, {"owner": "Орион"}]}
        fixed = normalize_voice_cast(prompt, manifest)
        self.assertTrue(fixed.startswith(story))
        self.assertNotIn('child voice', fixed)
        self.assertIn('VOICE CAST FOR ORION:', fixed)
        self.assertEqual(normalize_voice_cast(fixed, manifest), fixed)

    def test_voice_without_scene_description_remains_selected(self):
        prompt = 'VEGA: "Hello."\nVOICE CAST FOR VEGA: Use a child voice.'
        self.assertEqual(normalize_voice_cast(prompt, {"images": []}), prompt)
