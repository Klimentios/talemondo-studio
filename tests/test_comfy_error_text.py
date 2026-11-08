import unittest

from app.comfy_client import ComfyClient


def _history(error: dict) -> dict:
    return {"p1": {"status": {"status_str": "error", "messages": [
        ["execution_start", {"prompt_id": "p1"}], ["execution_error", {"prompt_id": "p1", **error}]]}}}


class ComfyErrorTextTests(unittest.TestCase):
    """Studio shows this text as the reason a job failed; it must never be blank."""

    def status(self, error: dict) -> dict:
        return ComfyClient()._history_status("p1", _history(error))

    def test_message_is_passed_through(self):
        status = self.status({"exception_message": "Error while deserializing header: header too small\n",
                              "exception_type": "SafetensorError", "node_type": "UNETLoader"})
        self.assertEqual(status, {"status": "error", "error": "Error while deserializing header: header too small\n"})

    def test_blank_message_names_exception_and_node(self):
        # «Правка видео» keep-mode on the stand, 2026-10-08: torch.load of an empty SAM2 checkpoint.
        status = self.status({"exception_message": "\n", "exception_type": "EOFError", "node_type": "H3CharacterTrack"})
        self.assertEqual(status["error"], "EOFError в узле H3CharacterTrack")

    def test_nothing_known_keeps_generic_text(self):
        self.assertEqual(self.status({"exception_message": ""})["error"], "Ошибка генерации")


if __name__ == "__main__":
    unittest.main()
