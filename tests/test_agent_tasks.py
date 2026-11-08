"""Studio writing/planning tasks done by the user's agent instead of the Qwen director (local_studio/agent_tasks.py)."""
import asyncio
import base64
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

from local_studio import agent_brain, agent_tasks
from local_studio.agent_brain import BrainError, BrainRequest
from app.scene_edits import SceneBoardEditRequest

JPEG = b"\xff\xd8\xff\xe0fake"


def test_inline_pictures_become_files_the_agent_opens(tmp_path):
    messages = [{"role": "system", "content": "Plan the scene."},
                {"role": "user", "content": [{"type": "text", "text": "Frames:"},
                                             {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()}}]}]
    prompt, images = agent_tasks.flatten(messages, tmp_path)
    assert prompt.startswith("SYSTEM:\nPlan the scene.") and "USER:\nFrames:" in prompt
    assert Path(images[0]).read_bytes() == JPEG and f"[image 1: {images[0]}]" in prompt
    assert "open and look at every one" in prompt


def test_json_answer_survives_prose_and_fences():
    assert agent_tasks.json_answer('Вот план:\n```json\n{"a": 1}\n```') == {"a": 1}
    assert agent_tasks.json_answer('ok {"b": [1, 2]} done') == {"b": [1, 2]}
    with pytest.raises(BrainError):
        agent_tasks.json_answer("без JSON")


def test_both_clis_get_the_pictures():
    req = BrainRequest(provider="codex", prompt="look", images=["/tmp/x/a.jpg", "/tmp/x/b.jpg"])
    codex = agent_brain.codex_command("codex", req, Path("/tmp/w"))
    assert codex[-3:] == ["--image=/tmp/x/a.jpg", "--image=/tmp/x/b.jpg", "look"]
    claude = agent_brain.claude_command("claude", req.model_copy(update={"provider": "claude"}))
    assert claude[claude.index("--add-dir") + 1] == "/tmp/x" and "Read" in claude


def test_board_edit_answer_goes_through_the_same_validator():
    payload = SceneBoardEditRequest(command="Добавь реакцию Орион после шота 1", shots=[
        {"id": "s1", "title": "Вход", "action": "VEGA enters", "duration": 5, "framing": "wide", "lens_mm": 24,
         "camera_motion": "static", "start_frame": "new_keyframe"}])
    answer = {"summary": "Добавлена реакция", "operations": [{"op": "add_after", "after_shot_id": "s1", "shot": {
        "title": "Реакция", "action": "ORION freezes", "duration": 10, "framing": "close", "lens_mm": 85,
        "camera_motion": "static", "start_frame": "new_keyframe"}}]}

    async def fake_ask(prompt, images):
        assert "add_after" in prompt and '"s1"' in prompt
        return "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"

    with patch.object(agent_tasks, "_ask", fake_ask):
        try:
            result = asyncio.run(agent_tasks.write_board_edit(payload))
        except ValueError as exc:  # the answer must satisfy the old schema; report what the validator wants
            pytest.fail(f"validator rejected a well-formed edit: {exc}")
    assert result["operations"][0]["op"] == "add_after"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_contact_sheet_of_a_finished_shot(tmp_path):
    from local_studio.scene_qc import contact_sheet
    clip = tmp_path / "shot.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x568:d=4:r=24",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    sheet, times = contact_sheet(clip, 8)
    assert sheet.startswith(b"\xff\xd8") and len(times) == 8 and times[0] == 0.25 and times[-1] == 3.75


def test_helper_configuration_is_payload_only_and_preserves_explicit_choice(monkeypatch):
    monkeypatch.setenv("H3_BRAIN_PROVIDER", "codex")
    monkeypatch.setenv("H3_BRAIN_MODEL", "configured-model")
    monkeypatch.setenv("H3_BRAIN_EFFORT", "low")
    fake = AsyncMock(return_value={"text": "ok"})
    with patch.object(agent_tasks, "ask", fake):
        assert asyncio.run(agent_tasks._ask("complete task", ["/tmp/ref.jpg"])) == "ok"
    req = fake.call_args.args[0]
    assert (req.provider, req.model, req.effort, req.read_repo) == ("codex", "configured-model", "low", False)
    assert req.images == ["/tmp/ref.jpg"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_contact_sheet_of_a_long_source_video(tmp_path):
    from local_studio.scene_qc import contact_sheet
    clip = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=320x568:d=30:r=12",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    with pytest.raises(ValueError):
        contact_sheet(clip, 8)  # a shot review stays within 20 s
    sheet, times = contact_sheet(clip, 12, 600)
    assert sheet.startswith(b"\xff\xd8") and times[-1] == 28.75
    late_sheet, late_times = contact_sheet(clip, 8, 600, 20, 30)
    assert late_sheet.startswith(b"\xff\xd8") and late_times == [20.62, 21.88, 23.12, 24.38, 25.62, 26.88, 28.12, 29.38]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_sound_is_read_from_the_file(tmp_path):
    from local_studio.scene_qc import has_audio
    silent, loud = tmp_path / "silent.mp4", tmp_path / "loud.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=160x284:d=1:r=12", "-pix_fmt", "yuv420p", str(silent)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=160x284:d=1:r=12", "-f", "lavfi", "-i",
                    "sine=duration=1", "-shortest", "-pix_fmt", "yuv420p", "-c:a", "aac", str(loud)], check=True)
    assert has_audio(silent) is False and has_audio(loud) is True

