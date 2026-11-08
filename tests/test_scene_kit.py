"""«Комплект сцены»: one franchise's heroes, pictures, medium and limits; nothing else reaches a scene or the GPU."""
from local_studio import scene_kit
from local_studio.franchise_profile import Profile

ADAM = Profile.model_validate({
    "look": {"style": "Premium 3D animated feature look."},
    "director_defaults": {"style": {"rendering": "animation3d"}},
    "roles": [{"name": "ADAM", "kind": "character", "aliases": ["Адам", "Adam"], "assets": [{"asset_id": "adam-face", "role": "Лицо героя"}],
               "prompt": "ADAM reacts to every cat event.", "sheet": {"scale": "adult man, VEGA reaches his shin", "gait": "loose long stride"}},
              {"name": "VEGA", "kind": "character", "assets": [{"asset_id": "vega3d", "role": "Персонаж целиком"}]},
              {"name": "Apartment", "kind": "location", "assets": [{"asset_id": "flat", "role": "Окружение"}]},
              {"name": "Rooftop", "kind": "location", "aliases": ["roof"]},
              {"name": "Cardboard tube", "kind": "prop", "aliases": ["tube"], "assets": [{"asset_id": "tube", "role": "Предмет"}]}]})
ORION = Profile.model_validate({
    "director_defaults": {"style": {"rendering": "photoreal"}},
    "roles": [{"name": "VEGA", "kind": "character", "assets": [{"asset_id": "vega-photo"}]},
              {"name": "ORION", "kind": "character", "aliases": ["Orion"], "assets": [{"asset_id": "orion"}]}]})
CARDS = [{"id": "s57", "type": "scene", "asset_ids": ["shared-street"]}]
OTHER_CARDS = [{"id": "e1", "type": "location", "title": "Street", "asset_ids": ["shared-street"]}]


def kit():
    return scene_kit.assemble("f-adam", "ADAM & VEGA", ADAM, CARDS, [("f-am", ORION, OTHER_CARDS)], {})


def job(ids, owners, prompt="ADAM drops the tube.", mode="animation3d", duration=12):
    roles = {i: ("Лицо героя" if i in owners else "Окружение") for i in ids}
    return {"asset_ids": ids, "reference_owners": owners, "reference_roles": roles, "prompt": prompt,
            "settings": {"mode": mode, "duration": duration, "aspect_ratio": "9:16"}}


def test_kit_is_one_franchise():
    k = kit()
    assert k["mode"] == "animation3d" and [h["name"] for h in k["cast"]] == ["ADAM", "VEGA"]
    assert {"adam-face", "vega3d", "flat"} <= set(k["allowed"]) and "vega-photo" not in k["allowed"]
    assert "shared-street" not in k["allowed"] and k["shared_excluded"] == ["shared-street"]  # linked twice: nobody's
    assert "ORION" in k["foreign"] and "VEGA" not in k["foreign"]  # both franchises have an VEGA: not foreign
    assert "VEGA reaches his shin" in scene_kit.hero_brief(k, ["adam"])


def test_take_outside_the_kit_is_refused():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    assert scene_kit.body_findings(k, job(["adam-face", "flat"], {"adam-face": "ADAM"})) == []
    other_vega = scene_kit.body_findings(k, job(["vega-photo"], {"vega-photo": "VEGA"}))
    assert "не из комплекта" in other_vega[0]  # VEGA & ORION's photoreal VEGA in a ADAM & VEGA shot
    stolen = scene_kit.body_findings(k, job(["adam-face"], {"adam-face": "VEGA"}))
    assert "принадлежит ADAM" in stolen[0]
    alien = scene_kit.body_findings(k, job(["flat"], {}, prompt="ADAM hands the bag to ORION."))
    assert alien == ["герои другой франшизы в промпте: ORION"]
    assert "bell rings" not in str(scene_kit.body_findings(k, job(["flat"], {}, prompt="A door bell rings.")))
    medium = scene_kit.body_findings(k, job(["flat"], {}, mode="realistic", duration=5))
    assert any("режим" in m for m in medium) and any("вне 10–15" in m for m in medium)
    assert scene_kit.body_findings(k, job(["frame-1"], {}), {"frame-1"}) == []  # the scene's own start frame


def test_location_picture_goes_only_into_its_place():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    balcony = job(["flat"], {}, prompt="<Subject 3> is the environment: Enclosed balcony: thin snow on screen right.")
    assert "картинка места «Apartment»" in scene_kit.place_findings(k, balcony)[0]  # 31.10 White Planet
    assert "Enclosed balcony" in scene_kit.body_findings(k, balcony)[0]
    home = job(["flat"], {}, prompt="<Subject 3> is the environment: ADAM's apartment, blue sofa.")
    assert scene_kit.place_findings(k, home) == []
    assert scene_kit.place_findings(k, job(["flat"], {}, prompt="ADAM drops the tube.")) == []  # no stated place
    assert scene_kit.place_findings(k, balcony, location="Apartment balcony") == []


def test_previs_of_the_scene_is_its_own_material():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    previs = [{"id": "pv", "kind": "image", "role": "Композиция", "name": "previs-wp shot 1.png"},
              {"id": "cam", "kind": "video", "role": "Камера", "name": "previs-wp shot 1.mp4"}]
    body = job(["flat", "pv", "cam"], {}, prompt="ADAM drops the tube.")
    body["reference_roles"].update({"pv": "Композиция", "cam": "Камера"})
    scene = scene_kit.findings(k, previs, {"c": {"body": body}}, [{"id": "c", "sceneShot": {}}])
    assert not [f for f in scene if "pv" in f or "cam" in f or "previs" in f]
    stranger = [{"id": "x", "kind": "image", "role": "Композиция", "name": "random.png"}]
    assert any("не привязан к паспорту" in f for f in scene_kit.findings(k, stranger, {}, []))


def test_breakdown_needs_a_passport_place_and_its_props():
    k = kit()
    balcony = job(["flat"], {}, prompt="<Subject 3> is the environment: Enclosed balcony: thin snow.")
    assert "место «Enclosed balcony» не описано в паспорте" in scene_kit.breakdown_findings(k, balcony)[0]
    roof = job([], {}, prompt="<Subject 3> is the environment: the roof at night.")
    assert "у места «Rooftop» в паспорте нет картинки" in scene_kit.breakdown_findings(k, roof)[0]
    home = job(["flat"], {}, prompt="<Subject 3> is the environment: ADAM's apartment. ADAM drops the tube.")
    assert "предмет «Cardboard tube»" in scene_kit.breakdown_findings(k, home)[0]
    assert scene_kit.breakdown_findings(k, job(["flat", "tube"], {}, prompt=home["prompt"])) == []
    assert scene_kit.breakdown_findings(k, job(["flat"], {}, prompt="ADAM waits."), {"location": "Apartment"}) == []
    scene = scene_kit.findings(k, [], {"cut-1": {"body": balcony}}, [{"id": "cut-1", "sceneShot": {"characters": ["ADAM"]}}])
    assert any("шот cut-1: место «Enclosed balcony»" in f for f in scene)


def test_scene_wide_text_and_static_camera_are_refused():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    push = "The camera pushes into VEGA's eyes and the real room transforms around him in the same frame."
    templates = {f"c{n}": {"body": job(["flat"], {}, prompt=f"{push} [Shot 1] ADAM does thing number {n} today.")} for n in range(4)}
    cuts = [{"id": f"c{n}", "sceneShot": {"camera_motion": "static", "characters": ["ADAM"]}} for n in range(4)]
    found = scene_kit.findings(k, [], templates, cuts)
    assert any("общий текст сцены" in f and "camera pushes" in f for f in found)
    assert any("камера стоит в 4 из 4" in f for f in found)
    cuts[0]["sceneShot"]["characters"] = ["ORION"]
    assert any("«ORION» нет среди героев" in f for f in scene_kit.findings(k, [], {}, cuts))


def test_studio_own_lines_and_fantasy_world_are_not_scene_text():
    """02.11 idea 38: scene_draft passed, «Собрать сцену» refused its own place, style opening and drawn world."""
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    place = ("<Subject 3> is the environment: ADAM's bright apartment. Screen right: coffee table, beyond it the desk "
             "with monitors and the kitchen island. Back wall: tall city windows with soft sunlight across the rug.\n"
             "The target video is in this style: premium theatrical stylized 3D animated feature with tactile materials.\n")
    world = [{"id": "w", "kind": "image", "role": "Окружение", "name": "fantasy-world.png"}]
    templates = {f"c{n}": {"body": job(["flat", "w"], {}, prompt=f"{place}[Shot 1] ADAM does thing number {n} today.")}
                 for n in range(4)}
    cuts = [{"id": f"c{n}", "sceneShot": {"camera_motion": "dolly", "characters": ["ADAM"]}} for n in range(4)]
    found = scene_kit.findings(k, world, templates, cuts)
    assert not [f for f in found if "общий текст сцены" in f or "комплекта" in f or "паспорту" in f], found


def test_every_shot_is_alive():
    k = kit()
    dead = "[Shot 1] ADAM stands by the table. VEGA sits on the floor. ADAM drops the tube."
    found = scene_kit.life_findings(k, dead)
    assert any("глаза" in f for f in found) and any("не взаимодействуют" in f for f in found)
    assert any("мир шота мёртвый" in f for f in found)
    alive = ("[Shot 1] VEGA blinks, ears twitch toward the tube; ADAM glances down and meets his eyes, then looks into "
             "the lens. Dust drifts in the window light, a passing shadow crosses the floor; the tube rolls, wobbles "
             "and settles against the table leg. VEGA answers with a slow tail flick.")
    assert scene_kit.life_findings(k, alive) == []
    assert scene_kit.life_layers(alive) >= {"eyes", "body", "air", "physics", "light"}


def test_take_fields_write_the_life_of_one_take():
    k = kit()
    take = {"cast": [{"name": "ADAM", "eyes": "ADAM blinks twice and glances from the tube to VEGA",
                      "answers": "ADAM answers VEGA's stare with a slow nod"},
                     {"name": "VEGA", "eyes": "VEGA's pupils widen and he looks up at ADAM, then into the lens"}],
            "world": {"physics": "The tube rolls, wobbles and settles against the table leg",
                      "light": "A passing car's headlights sweep a shadow across the ceiling",
                      "air": "Dust drifts in the window light"}}
    sentences, problems = scene_kit.take_life(k, take)
    assert problems == [] and sentences[0].startswith("ADAM blinks") and sentences[-1].endswith(".")
    flat = {"cast": [{"name": "ADAM"}, {"name": "VEGA", "eyes": "looks at ADAM"}], "world": {"light": "warm"}}
    _, problems = scene_kit.take_life(k, flat)
    assert any("ADAM: нет поля eyes" in p for p in problems)
    assert any("не отвечают" in p for p in problems) and any("мир дубля не живёт" in p for p in problems)
    assert scene_kit.sheet_words(k, "Адам", scene_kit.PLAY_KEYS) == "ADAM — gait: loose long stride."


def test_sheet_sentences_repeat_in_every_shot_without_alarm():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    look = scene_kit.sheet_words(k, "ADAM", scene_kit.LOOK_KEYS)
    play = scene_kit.sheet_words(k, "ADAM", scene_kit.PLAY_KEYS)
    templates = {f"c{n}": {"body": job(["flat"], {}, prompt=f"{look} {play} [Shot 1] ADAM does thing number {n} today.")}
                 for n in range(4)}
    assert not [f for f in scene_kit.findings(k, [], templates, []) if "общий текст" in f]


def test_a_take_in_the_wrong_frame_shape_is_refused():
    k = kit()
    k["budget"]["min_prompt_words"] = 0
    k["life"]["min_layers"] = 0
    k["aspect_ratio"] = "9:16"
    wide = job(["flat"], {})
    wide["settings"]["aspect_ratio"] = "16:9"
    assert scene_kit.body_findings(k, wide) == ["кадр 16:9, паспорт франшизы — 9:16"]
    wide["settings"]["aspect_ratio"] = "9:16"
    assert scene_kit.body_findings(k, wide) == []
    del wide["settings"]["mode"], wide["settings"]["duration"]  # the store would render «replace», a 5 s take
    assert scene_kit.body_findings(k, wide) == ["режим «replace», паспорт франшизы — «animation3d»",
                                                "дубль 5 с вне 10–15 с"]
    del wide["settings"]["aspect_ratio"]  # and 9:16, as every STUDIO video
    assert scene_kit.body_findings(k, wide) == ["режим «replace», паспорт франшизы — «animation3d»",
                                                "дубль 5 с вне 10–15 с"]
    k["aspect_ratio"] = "16:9"
    assert "кадр 9:16, паспорт франшизы — 16:9" in scene_kit.body_findings(k, wide)
