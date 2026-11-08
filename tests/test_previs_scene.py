from local_studio.previs_scene import SceneCheck, cast_bodies, plan_scene

CAST = [
    {"name": "ADAM", "aliases": ["Adam"], "sheet": {"scale": "Adult young man of average height; standing, VEGA's whole body reaches only his shin"}},
    {"name": "VEGA", "sheet": {"scale": "Small house cat: his whole body is shorter than ADAM's lower leg"}},
    {"name": "Quinn", "sheet": {"scale": "Adult woman a little shorter than ADAM; VEGA's whole body reaches only her shin"}},
]


def test_body_is_read_from_the_heroes_own_words_not_from_who_they_mention():
    assert cast_bodies(CAST, ["ADAM", "VEGA", "Quinn", "Stranger"]) == {"ADAM": "man", "VEGA": "cat", "Quinn": "woman", "Stranger": "man"}


def test_one_floor_for_the_scene_and_a_camera_per_card():
    check = SceneCheck(aspect="9:16", shots=[
        {"duration": 11, "framing": "wide", "angle": "eye", "lens_mm": 24, "camera_motion": "static", "characters": ["ADAM", "VEGA"]},
        {"duration": 11, "framing": "medium", "angle": "low", "lens_mm": 75, "camera_motion": "dolly", "characters": ["VEGA"]},
    ])
    plan = plan_scene(check, CAST)
    assert [(h["name"], h["asset"]) for h in plan["heroes"]] == [("ADAM", "person.man"), ("VEGA", "animal.cat")]
    wide, medium = plan["shots"]
    assert wide["start"] == 0 and medium["start"] == 11
    # the wide shot of a man on 24 mm stands metres away at his eye level; the cat's low shot sits near the floor
    assert wide["marks"][0]["plan_y"] < -5 and 1.5 < wide["marks"][0]["y"] < 1.8
    assert medium["marks"][0]["y"] < 0.15
    # a dolly is a push in: it starts further away than it ends; every camera stays on the front side (one axis)
    assert medium["marks"][0]["plan_y"] < medium["marks"][1]["plan_y"]
    assert all(m["plan_y"] < 3.0 for shot in plan["shots"] for m in shot["marks"])


def test_a_framing_the_lens_cannot_give_is_reported():
    check = SceneCheck(shots=[{"duration": 5, "framing": "extreme_close", "lens_mm": 16, "characters": ["VEGA"]}])
    assert any("длиннее объектив" in note for note in plan_scene(check, CAST)["shots"][0]["notes"])


def test_findings_name_what_the_frame_gets_wrong_in_plain_words():
    check = SceneCheck(aspect="9:16", place="Enclosed balcony with an open doorway", shots=[
        {"duration": 11, "framing": "medium", "angle": "low", "lens_mm": 75, "camera_motion": "dolly", "characters": ["VEGA", "ADAM"]},
        {"duration": 12, "framing": "wide", "angle": "low", "lens_mm": 35, "camera_motion": "tracking", "characters": ["VEGA", "ADAM"]},
    ])
    plan = plan_scene(check, CAST)
    tight, moving = plan["shots"]
    assert any("ADAM не в кадре" in note for note in tight["notes"])  # a medium of a cat on 75 mm cannot hold the man
    assert not any("VEGA" in note for note in moving["notes"])         # a tracking camera keeps its subject in frame
    assert {p["asset"] for p in plan["set"]} >= {"furniture.doorOpen", "furniture.window"}


def test_a_saved_scene_is_read_like_the_canvas_reads_it():
    from local_studio.previs_scene import check_from_scene
    scene = {"references": [{"owner": "ADAM"}, {"owner": "VEGA"}, {"owner": ""}], "cuts": [
        {"duration": 12, "prompt": "VEGA by the door. is the environment: hallway with a glazed door\nmore",
         "sceneShot": {"framing": "close", "angle": "eye", "lens_mm": 50, "camera_motion": "dolly"}},
        {"duration": 20, "prompt": "ADAM opens; VEGA rings. is the environment: hallway with a glazed door", "sceneShot": {}},
    ]}
    check = check_from_scene(scene, "9:16", CAST)
    first, second = check.shots
    assert first.characters == ["VEGA"] and first.framing == "close" and first.lens_mm == 50
    assert second.characters == ["ADAM", "VEGA"] and second.duration == 15 and second.framing == "medium"
    assert check.place == "hallway with a glazed door"


def test_a_hero_the_shot_sets_in_the_background_is_judged_there():
    from local_studio.previs_scene import check_from_scene
    scene = {"references": [{"owner": "ADAM"}, {"owner": "VEGA"}], "cuts": [{"duration": 12, "prompt":
             "[Shot 1] A medium low shot. ADAM is screen left background, inside by the door. VEGA is screen right foreground.",
             "sceneShot": {"framing": "medium", "angle": "low", "lens_mm": 35, "camera_motion": "static", "characters": ["VEGA", "ADAM"]}}]}
    check = check_from_scene(scene, "9:16", CAST)
    assert check.shots[0].background == ["ADAM"]
    # a medium at cat height cannot hold the man's face even a few metres back; a full shot from low holds both
    assert any("ADAM не в кадре" in note for note in plan_scene(check, CAST)["shots"][0]["notes"])
    full = SceneCheck(aspect="9:16", shots=[check.shots[0].model_copy(update={"framing": "full"})])
    assert plan_scene(full, CAST)["shots"][0]["notes"] == []
