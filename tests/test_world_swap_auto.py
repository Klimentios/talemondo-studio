"""«Смена мира по видео» built by Studio: what it produces passes the production gates, so they have nothing
to refuse."""
import json

from local_studio import guide_cut_gate, world_swap_auto as world
from local_studio.production_gates import shot_timeline_problems
from local_studio.store import require_ab_request

KIT = {"cast": [{"name": "ADAM", "aliases": ["ADAM"], "line": "", "sheet": {"costume": "Black smiley T-shirt."}},
                {"name": "VEGA", "aliases": ["VEGA"], "line": "", "sheet": {"costume": "Blue sweater."}}]}
HEROES = [{"name": "ADAM", "performer": "the man at the litter box", "face_number": 1, "body_number": 2},
          {"name": "VEGA", "performer": "the cat on the sideboard", "face_number": 3, "body_number": 4}]


def test_settings_are_the_built_in_defaults_without_any_recipe():
    settings, origin = world.recipe_settings()
    assert origin == "default"
    assert settings["mode"] == "animation3d" and settings["style_adapter"] == "style3d"
    assert settings["cache_mode"] == "safe" and settings["steps"] == 24 and settings["profile"] == "bf16"
    settings["director_config"]["lens"]["parameters"]["aperture_f"] = 2  # a copy: the defaults stay as they are
    assert world.recipe_settings()[0]["director_config"]["lens"]["parameters"]["aperture_f"] == 8
    settings, _ = world.recipe_settings()
    assert settings["director_config"]["lens"]["parameters"]["aperture_f"] == 8
    assert not {"swap_lora", "reskin", "swap_mask", "seed", "guide_frames"} & set(settings)
    require_ab_request(dict(settings), settings["style_adapter"])  # no A/B option: nothing to ask the user for


def test_every_measured_shot_gets_its_own_scene_frame_on_its_cut():
    cuts = [0.4, 2.6, 5.33, 5.8, 8.92]  # a sliver at the head and one 0.47 s shot in the middle
    spans = world.part_shots(10.0, cuts)
    guides = world.guides_for(spans, first_picture=5)
    assert guides[0] == [5, 0]
    assert all(b - a >= 1.0 for a, b in spans)
    assert [g[0] for g in guides] == list(range(5, 5 + len(spans)))
    assert not guide_cut_gate.guide_cut_problems(guides, [2.6, 5.33, 8.92], 10.0)


def test_prompt_is_this_part_only_and_numbers_match_the_pictures():
    spans = [(0.0, 2.6), (2.6, 5.33), (5.33, 10.0)]
    actions = {1: "ADAM kneels at the litter box.", 2: "VEGA pulls a blue sweater over his head.",
               3: "VEGA jumps down from the sideboard."}
    prompt = world.compose(KIT, HEROES, 5, spans, actions, 1, 3)
    summary = prompt.split("summary:\n", 1)[1].split("\n\n", 1)[0]
    assert summary.count(".") <= 3 and "Part 1 of 3" in summary
    assert "<Picture 5> is the first frame of [Shot 1]" in prompt
    assert "<Picture 7> is the first frame of [Shot 3]" in prompt
    assert "face from <Picture 3>, full body and outfit from <Picture 4>" in prompt
    assert "blue sweater over his head" in prompt
    assert not shot_timeline_problems(prompt, 10)


def test_long_shots_get_scene_frames_inside_away_from_cuts():
    """sample clip part 1 (06.11): a 5 s shot with one frame lost the mapping after 2 s."""
    keys = world.frame_keys([(0, 1.75), (1.75, 4.042), (4.042, 9.0)], [1.75, 4.04, 8.25, 8.96], room=5)
    assert keys == [(1, 0), (2, 1.75), (3, 4.042), (3, 6.042)]  # 8.042 sits 0.21 s before the 8.25 cut
    assert world.guides_for_keys(keys, 5) == [[5, 0], [6, 42], [7, 97], [8, 145]]
    assert len(world.frame_keys([(0, 15.0)], [], room=3)) == 3
