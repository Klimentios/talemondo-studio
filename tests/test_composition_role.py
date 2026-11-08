"""«Композиция»: a picture that guides the general layout only (community ReferenceFacial workflow)."""
import json
from pathlib import Path

from app.director import DIRECTOR_ROLE_MAP, ROLE_INSTRUCTION, openai_director_payload
from app.h3_ir import Reference, build_skeleton
from local_studio.store import REFERENCE_ROLE_PROMPTS

ROOT = Path(__file__).resolve().parents[1]


def _payload(role_override):
    refs = [{"asset_id": "hero", "kind": "image", "tag": "<Picture 1>", "role_override": "identity", "frames": []},
            {"asset_id": "layout", "kind": "image", "tag": "<Picture 2>", "role_override": role_override, "frames": []}]
    return openai_director_payload("<Picture 1> walks.", refs, model="m", frame_count=124, requested_style="realism")


def _role_enum(payload):
    return payload["response_format"]["json_schema"]["schema"]["properties"]["references"]["items"]["properties"]["role"]["enum"]


def test_role_is_known_everywhere_with_the_same_wording():
    assert DIRECTOR_ROLE_MAP["Композиция"] == "composition"
    kind, text = ROLE_INSTRUCTION["composition"]
    assert kind == "image" and REFERENCE_ROLE_PROMPTS["Композиция"] == ("image", text)
    assert f"'Композиция': '{text}'" in (ROOT / "preview/h3-director/model.mjs").read_text()


def test_qwen_schema_gets_the_role_only_when_the_user_chose_it():
    assert "composition" in _role_enum(_payload("composition"))
    assert "composition" not in _role_enum(_payload("scene"))
    assert "composition" not in _role_enum(_payload(None))


def test_requests_without_the_role_match_the_pinned_golden_payload():
    golden = json.loads((ROOT / "tests/golden/pictures_qwen_request.json").read_text())
    payload = golden.get("payload", golden)
    assert "composition" not in _role_enum(payload)


def test_official_ir_states_a_weak_composition_guide():
    sk = build_skeleton([Reference("image", 1, "Персонаж целиком", "ADAM"), Reference("image", 2, "Композиция")])
    subjects = "\n".join(sk.subject_lines)
    retention = "\n".join(sk.retention_lines)
    assert "Let <Picture 2> be a guide for the general composition of the video only" in subjects
    assert "weak_reference - only the general composition" in retention
    assert " not " not in retention and "not copied" not in retention  # no negatives: H3 has no negative prompt


def test_composition_picture_is_sent_as_a_grey_layout_sketch(tmp_path):
    # 2026-10-08: a sharp «Композиция» photo put its people and clothes into the video instead of the heroes.
    import shutil, subprocess
    import pytest
    from local_studio.worker import composition_sketch
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg needed")
    photo = tmp_path / "people.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=1024x768", "-frames:v", "1",
                    str(photo)], check=True)
    sketch = composition_sketch(photo)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,pix_fmt", "-of", "csv=p=0",
                            str(sketch)], capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "1024,768,gray"
    assert photo.read_bytes() != sketch.read_bytes()
