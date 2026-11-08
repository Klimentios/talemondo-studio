import pytest
from pydantic import ValidationError

from local_studio.staging import Staging, compile_staging

# VEGA on the kitchen floor looking at ADAM, ADAM standing 1.5 m to his right looking back.
ACTORS = [{"name": "VEGA", "x": 0, "y": 0, "facing": 90, "height_cm": 25, "width_cm": 20, "eye_height_cm": 20},
          {"name": "ADAM", "x": 1.5, "y": 0, "facing": 270, "height_cm": 180},
          {"name": "fridge", "kind": "set", "x": 0.75, "y": 1.2, "width_cm": 70, "height_cm": 180}]


def plan(setups, shots):
    return Staging.model_validate({"actors": ACTORS, "axis": ["VEGA", "ADAM"], "setups": setups, "shots": shots})


def test_two_shot_sees_both_on_their_sides_with_their_eyelines():
    result = compile_staging(plan([{"id": "W", "x": 0.75, "y": -4, "aim": [0.75, 0], "height_cm": 100, "lens_mm": 18}],
                                  [{"setup": "W", "characters": ["VEGA", "ADAM"], "framing": "wide", "subject": "ADAM"}]))
    frame = {item["name"]: item for item in result["shots"][0]["frame"]}
    assert result["ok"], result["findings"]
    assert (frame["VEGA"]["side"], frame["VEGA"]["looking"]) == ("left", "screen_right")
    assert (frame["ADAM"]["side"], frame["ADAM"]["looking"]) == ("right", "screen_left")
    assert "VEGA: screen-left" in result["shots"][0]["staging_line"] and result["previz_svg"].startswith("<svg")


def test_declared_close_up_from_too_far_and_wrong_eye_level_are_caught():
    setup = {"id": "C", "x": 0, "y": -2.2, "aim": "VEGA", "height_cm": 60, "lens_mm": 50, "eye_level_of": "VEGA"}
    result = compile_staging(plan([setup], [{"setup": "C", "characters": ["VEGA"], "framing": "close"}]))
    rules = {f["rule"] for f in result["findings"]}
    assert {"staging_framing", "staging_eye_level"} <= rules and not result["ok"]
    near = compile_staging(plan([{**setup, "y": -0.45, "height_cm": 20, "lens_mm": 100}], [{"setup": "C", "characters": ["VEGA"], "framing": "close"}]))
    assert near["ok"] and near["shots"][0]["frame"][0]["framing"] in {"medium_close", "close", "extreme_close"}


def test_crossing_the_line_and_missing_heroes():
    setups = [{"id": "A", "x": 0.75, "y": -3, "aim": [0.75, 0], "height_cm": 100, "lens_mm": 18},
              {"id": "B", "x": 0.75, "y": 3, "aim": [0.75, 0], "height_cm": 100, "lens_mm": 18},
              {"id": "T", "x": 1.5, "y": -2, "aim": "ADAM", "height_cm": 160, "lens_mm": 85}]
    shots = [{"setup": "A", "characters": ["VEGA", "ADAM"]}, {"setup": "T", "characters": ["VEGA"]},
             {"setup": "B", "characters": ["VEGA", "ADAM"]}]
    rules = [(f["shot"], f["rule"]) for f in compile_staging(plan(setups, shots))["findings"]]
    assert (2, "staging_not_in_frame") in rules and (3, "staging_axis") in rules and (3, "staging_screen_swap") in rules
    shots[2]["crossing"] = True
    assert (3, "staging_axis") not in [(f["shot"], f["rule"]) for f in compile_staging(plan(setups, shots))["findings"]]


def test_hidden_hero_and_unknown_names():
    behind = compile_staging(plan([{"id": "K", "x": 3.5, "y": 0, "aim": "VEGA", "height_cm": 120, "lens_mm": 35}],
                                  [{"setup": "K", "characters": ["VEGA"]}]))
    assert any(f["rule"] == "staging_hidden" for f in behind["findings"])
    with pytest.raises(ValidationError):
        plan([{"id": "K", "x": 0, "y": -2, "aim": "ORION", "height_cm": 20, "lens_mm": 35}], [{"setup": "K"}])


def test_cut_to_the_same_subject_from_almost_the_same_place_jumps():
    setups = [{"id": "A", "x": 0, "y": -1.2, "aim": "VEGA", "height_cm": 20, "lens_mm": 35},
              {"id": "B", "x": 0.2, "y": -1.1, "aim": "VEGA", "height_cm": 20, "lens_mm": 35},
              {"id": "C", "x": -0.4, "y": -0.3, "aim": "VEGA", "height_cm": 20, "lens_mm": 85}]
    shots = [{"setup": "A", "characters": ["VEGA"]}, {"setup": "B", "characters": ["VEGA"]}, {"setup": "C", "characters": ["VEGA"]}]
    rules = [(f["shot"], f["rule"]) for f in compile_staging(plan(setups, shots))["findings"]]
    assert (2, "staging_jump_cut") in rules and (3, "staging_jump_cut") not in rules


def test_prompt_line_names_only_the_shot_cast_with_side_depth_and_size():
    """05.11: the staging line reached only the agent's reply. The take's prompt gets who is where, without the lens
    (the opening says it) and without a hero the shot does not cast (no pictures: H3 would draw a stranger)."""
    result = compile_staging(plan([{"id": "W", "x": 0.75, "y": -4, "aim": [0.75, 0], "height_cm": 100, "lens_mm": 18}],
                                  [{"setup": "W", "characters": ["VEGA", "ADAM"], "framing": "wide", "subject": "ADAM"},
                                   {"setup": "W", "characters": ["ADAM"], "framing": "wide"}]))
    both, alone = result["shots"]
    assert both["prompt_line"].startswith("VEGA is screen left in the foreground, tiny in the frame, looking toward "
                                          "screen right.")
    assert "ADAM is screen right in the background, small in the frame" in both["prompt_line"]
    assert "mm" not in both["prompt_line"] and "VEGA" not in alone["prompt_line"]
    assert alone["prompt_line"].startswith("ADAM is screen right 4 m from the camera")
    assert (both["lens_mm"], both["framing"], both["subject_framing"]) == (18, "wide", "wide")
