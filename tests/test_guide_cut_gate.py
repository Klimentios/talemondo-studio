"""«Кадр сцены» on the measured source cuts (guides taken from another part's plan are refused).
Cuts and guides below are measurements of real well-placed parts and of a part whose guides came from another plan."""
import pytest

from local_studio import guide_cut_gate as gate


def test_guides_on_the_measured_cuts_pass():
    assert gate.guide_cut_problems([[1, 0], [6, 39], [7, 82]], [1.625, 3.417], 6) == []
    assert gate.guide_cut_problems([[1, 0], [2, 23], [3, 30], [4, 35], [5, 45], [6, 79], [7, 104], [8, 139]],
                                   [0.958, 4.333, 5.792], 7) == []  # mid-shot anchors
    assert gate.guide_cut_problems([[1, 0], [2, 19], [3, 54], [4, 90], [5, 115]], [2.25, 3.75, 4.792], 7) == []


def test_guides_from_another_parts_plan_are_refused():
    problems = gate.guide_cut_problems([[1, 0], [6, 46], [7, 91], [8, 110], [9, 128]], [4.583, 5.333, 7.75], 9)
    assert problems == ["шот исходника 7.75–9.00 с без своего «Кадра сцены»"]


def test_a_guide_just_before_a_cut_is_refused():
    problems = gate.guide_cut_problems([[1, 0], [2, 72]], [3.3], 6)  # 3.0 s, 0.3 s before the cut
    assert any("до склейки 3.30" in p for p in problems)


def test_world_swap_without_any_scene_frame_stops_before_gpu():
    manifest = {"videos": [{"asset_id": "source", "role": "Движение и камера",
                            "performers": [{"owner": "VEGA", "performer": "the cat"}]}]}
    with pytest.raises(ValueError, match="guide_frames"):
        gate.check(None, settings={"mode": "animation3d", "duration": 6}, manifest=manifest)
    with pytest.raises(ValueError, match="кадре 0"):
        gate.check(None, settings={"mode": "animation3d", "duration": 6,
                                   "guide_frames": [[1, 12]]}, manifest=manifest)
