from fastapi.testclient import TestClient

from app.shot_lint import lint_shots


def shot(**kw):
    base = {"location": "kitchen", "characters": ["ADAM"], "duration": 5, "framing": "medium", "angle": "eye",
            "movement": "static"}
    return {**base, **kw}


def rules(shots, **kw):
    return {f["rule"] for f in lint_shots({"shots": shots, **kw})["findings"]}


GOOD = [
    shot(framing="wide", purpose="establishing", characters=["ADAM", "VEGA"], duration=6, lens_mm=24),
    shot(framing="medium", angle="high", characters=["ADAM"], key_action=True, duration=5),
    shot(framing="close", angle="low", characters=["VEGA"], purpose="reaction", duration=3, lens_mm=85),
    shot(framing="medium_full", movement="dolly", motivation="follows Vega to the lamp", characters=["VEGA"],
         duration=4, angle="eye"),
]


def test_clean_scene_passes():
    result = lint_shots({"shots": GOOD, "target_seconds": 18})
    assert result["ok"] and result["counts"]["error"] == 0, result["findings"]
    assert result["ladder"] == [1, 4, 6, 3]


def test_same_setup_twice_is_an_error():
    assert "same_setup" in rules([shot(framing="wide", purpose="establishing"), shot(), shot()])


def test_neighbouring_size_same_angle_warns():
    assert "thirty_degree" in rules([shot(framing="wide", purpose="establishing"), shot(framing="medium"),
                                     shot(framing="medium_close")])


def test_intent_or_intentional_cut_silences_jump_rules():
    found = rules([shot(framing="wide", purpose="establishing"), shot(framing="medium"),
                   shot(framing="medium", transition_in="jump_cut")])
    assert "same_setup" not in found


def test_axis_and_screen_direction():
    found = rules([shot(framing="wide", purpose="establishing", axis_side="A", screen_direction="left_to_right"),
                   shot(framing="close", axis_side="B", screen_direction="right_to_left")])
    assert {"axis_jump", "screen_direction"} <= found


def test_shot_reverse_eyelines_must_oppose():
    found = rules([shot(framing="wide", purpose="establishing", characters=["ADAM", "FINN"]),
                   shot(framing="medium_close", characters=["ADAM"], eyeline="right", subject_side="left"),
                   shot(framing="medium_close", angle="high", characters=["FINN"], eyeline="right", subject_side="left")])
    assert "eyeline" in found


def test_new_location_needs_establishing():
    assert "no_establishing" in rules([shot(framing="close", location="street")])
    assert "no_establishing" not in rules([shot(framing="close", location="street", purpose="reveal")])


def test_camera_moves_need_reason_and_variety():
    plan = [shot(framing="wide", purpose="establishing", movement="dolly", motivation="m")] + [
        shot(framing=f, angle=a, movement="dolly", motivation="m") for f, a in
        (("close", "low"), ("wide", "high"), ("close", "eye"))]
    found = rules(plan)
    assert {"repeated_move", "monotone_camera"} <= found
    assert "unmotivated_move" in rules([shot(framing="wide", purpose="establishing", movement="orbit")])


def test_scene_planner_shots_are_accepted():
    planner = [{"title": "A", "duration": 6, "framing": "wide", "lens_mm": 24, "camera_motion": "slow push in",
                "purpose": "establishing"},
               {"title": "B", "duration": 20, "framing": "close", "lens_mm": 18, "camera_motion": "static"}]
    found = rules(planner)
    assert {"too_long", "wide_lens_face", "unmotivated_move"} <= found


def test_transition_devices_must_match():
    found = rules([shot(framing="wide", purpose="establishing", transition_out="whip_pan"),
                   shot(framing="close", angle="low", transition_in="cut")])
    assert "transition_pair" in found


def test_emotion_reaction_and_look_room():
    found = rules([shot(framing="wide", purpose="establishing", key_action=True),
                   shot(framing="wide", angle="high", purpose="reaction", eyeline="left", subject_side="left")])
    assert {"emotion_on_wide", "look_room"} <= found
    empty_payoff = [shot(framing="wide", purpose="establishing"),
                    shot(framing="wide", location="corridor", purpose="payoff", characters=[])]
    assert "emotion_on_wide" not in rules(empty_payoff)
    assert "reaction_after_action" in rules([shot(framing="wide", purpose="establishing", key_action=True),
                                             shot(framing="wide", angle="high")])


def test_unknown_schema_value():
    assert "unknown_value" in rules([shot(framing="mega_close", purpose="establishing")])


def test_studio_route():
    from local_studio.main import app
    response = TestClient(app, base_url="http://127.0.0.1").post("/api/shot-lint", json={"shots": GOOD})
    assert response.status_code == 200 and response.json()["ok"]
