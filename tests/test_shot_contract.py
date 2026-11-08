import subprocess

from app.h3_ir import parse_ir
from local_studio import shot_contract as sc

ACTING = ("VEGA sits at screen left on the floor and watches the moving belt; his ears turn forward one after the other, "
          "he lowers his chest, lifts one forepaw and places it on the belt, then pulls it back when the belt carries it "
          "away. ADAM stands behind at the controls, smiles, and slowly lowers the speed while keeping his eyes on VEGA; "
          "VEGA tries again and walks three steps in place, ending with a puzzled look at ADAM.")


def body(prompt, roles=None, owners=None, duration=12):
    roles = roles or {"a1": "Лицо героя", "a2": "Персонаж целиком", "a3": "Лицо героя", "a4": "Окружение"}
    owners = owners or {"a1": "ADAM", "a2": "ADAM", "a3": "VEGA"}
    return {"prompt": prompt, "asset_ids": list(roles), "reference_roles": roles, "reference_owners": owners,
            "reference_numbers": {a: n for n, a in enumerate(roles, 1)}, "settings": {"duration": duration}}


def ir(description, subjects="<Picture 1> ADAM face; <Picture 2> ADAM body; <Picture 3> VEGA face; <Picture 4> apartment;"):
    return (f"subject_definitions:\n{subjects}\n\nsummary:\n[reference generation] Premium 3D. Preserve ADAM from Pictures 1-2.\n\n"
            f"retention_analysis:\n<Picture 1> fully_preserved face;\n\ndetailed_description:\n{description}\n\n"
            "overall_soundscape:\nSoft motor. Never cut to a reference picture.\n\nnon_diegetic_music:\nNone.")


def test_hand_written_picture_lines_become_subjects():
    b = body(ir("VEGA's fantasy begins with a push into VEGA's eyes. Never a split screen. [Shot 1] " + ACTING))
    changed = sc.normalize(b, {"framing": "wide", "angle": "eye", "lens_mm": 35, "camera_motion": "static"})
    sections = parse_ir(b["prompt"])
    assert "<Subject 2> is VEGA, whose face and identity come from <Picture 3>" in sections["subject_definitions"]
    assert "<Subject 3> is the environment in <Picture 4>" in sections["subject_definitions"]
    assert not any(line.startswith("<Picture") for line in sections["subject_definitions"].splitlines())
    assert "Pictures 1-2" not in sections["summary"] and sections["summary"].startswith("[reference generation]")
    assert "fantasy" not in sections["detailed_description"] and "split" not in sections["detailed_description"]
    assert sections["detailed_description"].split("[Shot 1] ")[1].startswith("A wide eye-level shot on a 35 mm lens")
    assert "Never" not in sections["overall_soundscape"] and changed
    assert sc.problems(b) == []


def test_fantasy_rule_stays_in_the_fantasy_shot():
    b = body(ir("The fantasy enters by a push into VEGA's eyes. [Shot 1] A close-up eye-level shot. " + ACTING
                + " The belt becomes an imagined endless road."))
    sc.normalize(b)
    assert "push into VEGA's eyes" in parse_ir(b["prompt"])["detailed_description"]


def test_montage_thin_acting_and_missing_hero_are_rejected():
    found = sc.problems(body(ir("[Shot 1] A wide shot. VEGA walks. The shot cuts to ADAM. [Shot 2] At 00:02.000 a close-up.")))
    assert not any("официальном виде" in p for p in found)  # [Shot 2] At 00:02.000 is the official form
    assert any("монтаж внутри дубля" in p for p in found)
    assert any("игра героев" in p for p in found)
    insert = sc.problems(body(ir("[Shot 1] A close-up of the cash on the desk; a cup lands on the stack and slides. " * 4)))
    assert any("нет ни одного героя" in p for p in insert)


def test_undescribed_picture_is_rejected():
    b = body(ir("[Shot 1] A wide shot. " + ACTING), roles={"a1": "Лицо героя", "a2": "Персонаж целиком",
                                                           "a3": "Лицо героя", "a4": "Окружение", "a5": "Окружение"})
    b["prompt"] = b["prompt"]  # the writer described four pictures, the job carries five
    assert any("картинка 5" in p for p in sc.problems(b))
    sc.normalize(b)
    assert sc.problems(b) == []  # the skeleton describes every attached picture


def test_tagged_prose_converts_to_the_official_format():
    prose = ("[Style] Ultra-photoreal live-action crime caper. Vertical 9:16. [Identity] <Picture 1> VEGA face. "
             "[Shot 1] At 00:00.000 " + ACTING + " [Camera] medium at eye level, 50 mm. [Sound] Room tone. "
             "One continuous take with no cuts and no inserted close-ups of the reference pictures.\n\n"
             "SUBJECT SCALE AND HEIGHT CONTINUITY: A domestic cat remains smaller than its adult owner; adult men and "
             "adult women share a ground plane.\n\nScene style, identical in every shot of this scene: Real cats.")
    b = body(prose, roles={"a1": "Лицо героя", "a2": "Лицо героя"}, owners={"a1": "VEGA", "a2": "ADAM"})
    sc.normalize(b, {"framing": "medium", "angle": "eye", "lens_mm": 50})
    sections = parse_ir(b["prompt"])
    assert list(sections) == ["subject_definitions", "summary", "retention_analysis", "detailed_description",
                              "overall_soundscape", "non_diegetic_music"]
    assert "adult owner" not in b["prompt"] and "inserted close-ups" not in b["prompt"]
    assert "Real cats." in sections["detailed_description"] and sections["overall_soundscape"].startswith("Room tone")
    assert sc.problems(b) == []


def _clip(path, filters):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *filters, "-pix_fmt", "yuv420p", str(path)], check=True)


def test_take_measure_finds_cuts_and_pasted_pictures(tmp_path):
    clean, cut, pasted, hero = (tmp_path / name for name in ("clean.mp4", "cut.mp4", "pasted.mp4", "hero.png"))
    _clip(clean, ["-f", "lavfi", "-i", "testsrc2=size=180x320:rate=24:duration=3"])
    _clip(cut, ["-f", "lavfi", "-i", "testsrc2=size=180x320:rate=24:duration=1.5", "-f", "lavfi", "-i",
                "mandelbrot=size=180x320:rate=24", "-filter_complex", "[1]trim=duration=1.5[b];[0][b]concat=n=2:v=1"])
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "mandelbrot=size=360x640", "-frames:v", "1",
                    str(hero)], check=True)
    _clip(pasted, ["-f", "lavfi", "-i", "testsrc2=size=180x320:rate=24:duration=1.5", "-loop", "1", "-t", "1.5", "-i",
                   str(hero), "-filter_complex", "[1]scale=180:320,fps=24[b];[0][b]concat=n=2:v=1"])
    assert sc.take_defects(str(clean), []) == []
    assert any(item.startswith("склейка") for item in sc.take_defects(str(cut), []))
    assert any("вставлена картинка-референс" in item
               for item in sc.take_defects(str(pasted), [("Персонаж целиком", str(hero))]))
    assert not any("вставлена" in item for item in sc.take_defects(str(pasted), [("Окружение", str(hero))]))


def test_short_takes_and_unofficial_cuts_are_rejected():
    assert any("дубль 5 с" in p for p in sc.problems(body(ir("[Shot 1] A wide shot. " + ACTING), duration=5)))
    official = body(ir("[Shot 1] A wide shot. " + ACTING + " [Shot 2] At 00:07.000, the shot cuts to a close-up. "
                       + ACTING), duration=15)
    sc.normalize(official)
    assert sc.problems(official) == [] and sc.planned_cuts(official["prompt"]) == [7.0]
    untimed = body(ir("[Shot 1] A wide shot. " + ACTING + " [Shot 2] A close-up. " + ACTING), duration=15)
    sc.normalize(untimed)
    assert any("официальном виде" in p for p in sc.problems(untimed))


def test_writer_words_about_heroes_and_place_survive_the_rebuild():
    subjects = ("<Subject 1> is ADAM, an adult man. He stands at screen right.\n"
                "<Subject 2> is VEGA, whose face comes from <Picture 3>. The cat is below Adam's knee.\n"
                "<Subject 3> is the environment of their apartment. Blue sofa on the left, window behind.")
    b = body(ir("[Shot 1] A wide shot. " + ACTING, subjects))
    sc.normalize(b)
    definitions = parse_ir(b["prompt"])["subject_definitions"]
    assert "He stands at screen right." in definitions and "The cat is below Adam's knee." in definitions
    assert "Blue sofa on the left, window behind." in definitions
    assert "<Subject 2> is VEGA, whose face and identity come from <Picture 3>." in definitions


def test_engine_writes_the_prompt_from_fields_and_the_passport():
    roles = [{"name": "IRIS", "kind": "character", "aliases": ["Ирис"], "prompt": "IRIS has a long narrow face"},
             {"name": "IRIS main costume", "kind": "costume", "owner": "IRIS", "prompt": "IRIS wears a yellow tulle skirt"}]
    take = {"summary": "Chapter 1 of 4: a shutter cuts Iris off from the stolen case.",
            "cast": [{"name": "IRIS", "position": "at screen left in mid-stride",
                      "notices": "sees the steel shutter start to drop ahead of him",
                      "does": "skids on the wet stone, wings folding forward, and catches two bars with both hands",
                      "ends": "stares between the bars at a driver trapped under a second lowering gate"}],
            "camera": "The camera tracks behind him and settles when he hits the bars",
            "end": "Iris's face between two bars", "lines": [{"speaker": "IRIS", "line": "Hold on!"}]}
    prompt = sc.compose(take, {"framing": "wide", "angle": "eye", "lens_mm": 28, "camera_motion": "tracking"},
                        style="Ultra-photoreal superhero comedy.", place="A wet market lane, stalls on the right.",
                        roles=roles)
    sections = parse_ir(prompt)
    assert "IRIS has a long narrow face. IRIS wears a yellow tulle skirt." in sections["subject_definitions"]
    assert "<Subject 2> is the environment: A wet market lane" in sections["subject_definitions"]
    shot_text = sections["detailed_description"].split("[Shot 1] ")[1]
    # The writer's camera sentence says where the tracking goes, so the move is written once — by the writer.
    assert shot_text.startswith("A wide eye-level shot on a 28 mm lens, one continuous take. IRIS")
    assert shot_text.count("track") == 1 and "The camera tracks behind him" in shot_text
    assert "IRIS (S1) says, <d>[English] Hold on!</d>" in prompt and "<Picture" not in prompt
    b = body(prompt, roles={"f": "Лицо героя", "e": "Окружение"}, owners={"f": "IRIS"})
    sc.normalize(b)
    assert "<Subject 1> is IRIS, whose face and identity come from <Picture 1>. IRIS has a long narrow face" in b["prompt"]
    assert sc.problems(b) == []


def test_alternative_costumes_stay_out_unless_the_scene_carries_them():
    roles = [{"name": "VEGA", "kind": "character", "prompt": "VEGA is a ruddy cat in a pink bunny suit"},
             {"name": "Wizard costume", "kind": "costume", "owner": "VEGA", "assets": [{"asset_id": "robe"}],
              "prompt": "VEGA wears the purple wizard robe instead of the pink bunny suit"},
             {"name": "VEGA main costume", "kind": "costume", "owner": "VEGA", "prompt": "VEGA wears the bunny suit"}]
    take = {"summary": "s", "cast": [{"name": "VEGA", "position": "left", "does": "sits", "ends": "sleeps"}], "end": "x"}
    daily = sc.compose(take, {}, roles=roles, pictures={"face"})
    assert "wizard" not in daily and "VEGA wears the bunny suit" in daily
    assert "wizard robe" in sc.compose(take, {}, roles=roles, pictures={"robe"})


def test_compose_uses_franchise_sheet_and_shot_specific_life():
    """Live compose 243003b put take.world in as a Python dict, dropped eyes/answers and the whole sheet with «never»
    into the subject; a take now reads as sentences: look in the subject, face and passport line in the acting."""
    kit = {"cast": [{"name": "ADAM", "aliases": ["ADAM", "Adam"], "line": "ADAM answers every cat event with a new tactic; "
                     "never a static observer.",
                     "sheet": {"body": "Tall and lean, never chibi", "face": "Brows lead every new thought",
                               "voice": "quick and playful", "never": "a realistic human"}}],
           "life": {"air": "dust moves across the room", "min_layers": 0}}
    take = {"cast": [{"name": "ADAM", "position": "sits at screen left", "eyes": "glances toward VEGA before moving",
                      "does": "catches the rolling bowl", "answers": "answers the cat's stare with a raised eyebrow",
                      "ends": "grinning"}],
            "world": {"physics": "The bowl rolls and stops against the table leg", "light": "Headlights sweep the ceiling"},
            "lines": [{"speaker": "ADAM", "line": "Got it."}]}
    prompt = sc.compose(take, {}, kit=kit)
    sections = parse_ir(prompt)
    action = sections["detailed_description"]
    assert "Tall and lean" in sections["subject_definitions"] and "chibi" not in prompt
    assert "never" not in prompt.lower() and "{" not in prompt and "a realistic human" not in prompt
    assert "ADAM glances toward VEGA" in action and "ADAM answers the cat's stare" in action
    assert "The bowl rolls and stops" in action and "Headlights sweep" in action
    # The sheet's face rules and the passport line stay out of the body: rules, not events.
    assert "brows lead every new thought" not in action and "new tactic" not in action
    assert "ADAM (S1), whose voice is quick and playful, says, <d>[English] Got it.</d>" in action
    assert "dust moves across the room" not in action  # the take's own world replaces the passport's general line
    assert sc.take_problems({"cast": take["cast"], "world": "a string"}, None) == [
        "take.world — объект {air, physics, light, background}, а не строка"]


def test_take_staging_is_checked_by_code():
    """31.10: Adam stood in the wrong place, VEGA & ORION came back as random cats, the fantasy did not enter through
    VEGA's eye — each is now a field the engine refuses to write without."""
    kit = {"cast": [{"name": "ADAM", "aliases": ["ADAM"], "sheet": {"scale": "adult man"}},
                    {"name": "VEGA", "aliases": ["VEGA"], "sheet": {"scale": ""}}]}
    passport = "Fantasy entry is a motivated camera move into VEGA's eye."
    real = {"summary": "Adam starts the treadmill.", "cast": [
        {"name": "ADAM", "position": "stands at the console at screen left", "does": "presses start"},
        {"name": "VEGA", "position": "sits behind the treadmill", "does": "taps the belt"},
        {"name": "ORION", "position": "screen right", "does": "watches"}]}
    found = sc.take_problems(real, kit, passport=passport)
    assert any(p.startswith("VEGA: в position нет стороны кадра") for p in found)
    assert not any(p.startswith("ADAM: в position") for p in found)
    assert any(p.startswith("VEGA: в паспорте пустой лист героя") for p in found)
    assert any(p.startswith("ORION: такого героя нет в паспорте") for p in found)
    dream = {"summary": "In his fantasy the belt becomes an endless road.",
             "cast": [{"name": "ADAM", "position": "screen left", "does": "laughs"}], "camera": "The camera trucks right"}
    entry = "фантазия входит только движением камеры в глаз VEGA"
    assert any(p.startswith(entry) for p in sc.take_problems(dream, kit, passport=passport, previous=real))
    pushed = {**dream, "camera": "The camera pushes slowly into VEGA's right eye and the reflection becomes the road"}
    assert not any(p.startswith(entry) for p in sc.take_problems(pushed, kit, passport=passport, previous=real))
    assert not any(p.startswith(entry) for p in sc.take_problems(dream, kit, passport=passport, previous=pushed))
    assert not any(p.startswith(entry) for p in sc.take_problems(dream, kit, passport=""))


def test_compose_reads_any_field_form_in_time_order():
    """Live Codex run on 61 (31.10): bare-verb fields, a name first, «text» for the line, formula answers, one passport
    sentence copied into every take — each came out broken or silently lost."""
    take = {"summary": "Vega makes Adam fetch.", "end": "Vega holds the ball and looks at the bowl",
            "cast": [{"name": "ADAM", "position": "screen right on the sofa", "notices": "Vega approaching with the ball",
                      "eyes": "follow the ball", "decides": "expects a handoff", "does": "extends one open hand",
                      "answers": "brows lift when Vega stops", "ends": "hand suspended, puzzled"},
                     {"name": "VEGA", "position": "screen left on the floor", "notices": "Adam's open hand",
                      "decides": "make Adam come farther", "does": "takes the ball and stops beyond reach"}],
            "lines": [{"speaker": "ADAM", "text": "Wait. I fetched it?"}]}
    action = parse_ir(sc.compose(take, {}))["detailed_description"]
    for sentence in ("ADAM notices Vega approaching with the ball.", "ADAM's eyes follow the ball.",
                     "ADAM expects a handoff.", "ADAM's brows lift when Vega stops.", "VEGA notices Adam's open hand.",
                     "VEGA decides to make Adam come farther.", 'ADAM (S1) says, <d>[English] Wait. I fetched it?</d>',
                     "At the end of the take, Vega holds the ball and looks at the bowl."):
        assert sentence in action
    assert action.index("VEGA takes the ball") < action.index("ADAM ends the take")
    formula = {"cast": [{"name": "ADAM", "position": "screen left", "answers": "He looks at his partner in response"}]}
    assert any("формулой" in p for p in sc.take_problems(formula))
    same = {"cast": [{"name": "VEGA", "eyes": "Both heroes blink at different moments and scan the room."}]}
    assert sc.repeated_sentences([same, same]) and not sc.repeated_sentences([same])


def test_compose_keeps_who_acts_when_the_field_names_the_partner():
    """Live Codex run 3 on 61: «Blinks, meets…» capitalised, notices «ADAM's open hand», decides «He was the fetcher»."""
    take = {"cast": [{"name": "VEGA", "position": "screen left", "notices": "ADAM's open hand",
                      "eyes": "Blinks, meets ADAM's eyes", "decides": "Withhold ball"},
                     {"name": "ADAM", "position": "screen right", "notices": "VEGA carries ball",
                      "decides": "He was the fetcher", "answers": "VEGA stops; ADAM raises brows"}]}
    action = parse_ir(sc.compose(take, {}))["detailed_description"]
    for sentence in ("VEGA notices ADAM's open hand.", "ADAM notices VEGA carries ball.", "VEGA blinks, meets ADAM's eyes.",
                     "VEGA decides to withhold ball.", "ADAM realises he was the fetcher.", "VEGA stops; ADAM raises brows."):
        assert sentence in action


def test_bare_name_cast_is_a_problem_not_a_crash():
    """«cast: ["ADAM", "VEGA"]» crashed scene_draft with HTTP 500 instead of a note."""
    take = {"summary": "Vega hears the tube.", "cast": ["ADAM", {"name": "VEGA", "position": "screen left", "does": "listens"}],
            "lines": ["Boo."], "camera": "static", "end": "Vega listens", "world": {}}
    problems = sc.take_problems(take, None)
    assert any("take.cast" in p for p in problems) and any("take.lines" in p for p in problems)
    assert "VEGA" in sc.compose(take, {"duration": 12}, style="", place="")


def test_interleaved_cast_never_leaves_a_bare_pronoun_or_doubled_name():
    """«He catches…» after VEGA's clause, «VEGA's eyes one ear folds», «ADAM his brows turn»,
    «ADAM screen right background, ADAM pauses»."""
    take = {"cast": [{"name": "ADAM", "position": "screen right", "answers": "He catches VEGA's look",
                      "does": "Screen right background, ADAM pauses"},
                     {"name": "VEGA", "position": "screen left", "eyes": "One ear folds; pupils widen",
                      "answers": "His tail lashes once"}]}
    action = parse_ir(sc.compose(take, {}))["detailed_description"]
    for sentence in ("ADAM catches VEGA's look.", "Screen right background, ADAM pauses.", "One of VEGA's ears folds; pupils widen.",
                     "VEGA's tail lashes once."):
        assert sentence in action
    assert " He " not in action and "eyes one" not in action


def test_place_carries_no_blocking():
    kit = {"cast": [{"name": "ADAM", "aliases": ["Adam"]}, {"name": "VEGA", "aliases": ["Vega"]}]}
    assert sc.place_problems("Adam's apartment: blue sofa at left, warm lamp.", kit) == []
    assert "ADAM, VEGA" in sc.place_problems("Adam's apartment. Vega begins screen left; Adam stands right.", kit)[0]


def test_take_reaches_h3_in_the_official_reference_format():
    """ref-en.txt: pictures bound to subjects, the first frame declared, <Subject N> in the body, (S1) and <d>[English],
    a style opening without Studio notes. A bare engine prompt lacked all of it."""
    kit = {"language": "English", "cast": [
        {"name": "ADAM", "aliases": ["Adam"], "sheet": {"scale": "Adult young man.", "costume": "Black T-shirt.",
                                                        "voice": "Young adult male, English, quick and playful."}},
        {"name": "VEGA", "aliases": ["Vega"], "sheet": {"scale": "Small house cat.", "body": "Tawny ticked fur."}}]}
    take = {"summary": "Adam says boo through the tube.",
            "cast": [{"name": "VEGA", "position": "screen left by the tube", "does": "pins the tube"},
                     {"name": "ADAM", "position": "screen right, crouching", "does": "lifts the tube"}],
            "world": {"physics": "The tube rolls once", "light": "The lamp glints on the rim"},
            "camera": "The camera holds a static side shot.", "end": "Vega stares at Adam",
            "lines": [{"speaker": "ADAM", "line": "Boo"}], "sound": "A hollow cardboard knock.", "music": "None"}
    style = "Premium 3D animated feature look (visual benchmark job 0c31262c): soft fur. Never plastic realism."
    prompt = sc.compose(take, {"framing": "medium", "angle": "high"}, style=style, place="Adam's living room.", kit=kit)
    ids = ["k-face", "k-body", "a-face", "a-body", "room", "start-frame"]
    roles = ["Лицо героя", "Персонаж целиком", "Лицо героя", "Персонаж целиком", "Окружение", "Кадр сцены"]
    body = {"prompt": prompt, "asset_ids": ids, "reference_roles": dict(zip(ids, roles)),
            "reference_owners": {"k-face": "ADAM", "k-body": "ADAM", "a-face": "VEGA", "a-body": "VEGA"},
            "reference_numbers": {i: n for n, i in enumerate(ids, 1)}, "settings": {"duration": 12}}
    sc.normalize(body, {"framing": "medium", "angle": "high"})
    ir = parse_ir(body["prompt"])
    assert ir["subject_definitions"].startswith(
        "<Subject 1> is ADAM, whose face and identity come from <Picture 1> and whose full-body appearance")
    assert "<Picture 6> is the first frame of [Shot 1]." in ir["subject_definitions"]
    assert ir["summary"].startswith("[reference generation + keyframe completion]")
    assert "<Subject 1> (appears in [Shot 1]): fully_preserved" in ir["retention_analysis"]
    action = ir["detailed_description"]
    assert action.startswith("The target video is in a premium 3D animated feature style: soft fur.")
    assert "0c31262c" not in action and "Never" not in action
    assert "VEGA (<Subject 2>) is screen left" in action and "ADAM (<Subject 1>) is screen right" in action
    assert "<Subject 1> (S1), whose voice is young adult male, quick and playful, says, <d>[English] Boo.</d>" in action
    assert sc.take_problems({**take, "sound": "Adam whispers boo."}, None)[-1].startswith("реплика «boo»")
    assert sc.thin_description(body["prompt"])[0].startswith("в описании дубля")


def test_hero_voice_file_names_the_hero_who_speaks_and_moves_no_picture():
    """A voice file is <Audio N>, not a picture, and its (S1) is the hero who says the (S1) lines.
    Until 01.11 the scene engine counted the voice file as <Picture 2> and gave IRIS's voice the (S1) of MIRA's line."""
    kit = {"language": "English", "cast": [{"name": "IRIS", "aliases": ["Iris"], "sheet": {"voice": "Bright tenor."}},
                                           {"name": "MIRA", "aliases": ["Mira"], "sheet": {"voice": "Low calm baritone."}}]}
    take = {"summary": "Mira and Iris talk at the gate.",
            "cast": [{"name": "IRIS", "position": "screen left", "does": "grips a bar"},
                     {"name": "MIRA", "position": "screen right", "does": "nods"}],
            "camera": "The camera holds a static two shot.", "end": "Both look at the exit",
            "lines": [{"speaker": "MIRA", "line": "We found the rehearsal"}, {"speaker": "IRIS", "line": "We lost the money"}],
            "sound": "Keys jingle.", "music": "None"}
    prompt = sc.compose(take, {"framing": "two_shot"}, style="Ultra-photoreal live action.", place="A market lane.", kit=kit)
    ids = ["b-face", "b-voice", "o-face", "o-voice", "lane"]
    roles = ["Лицо героя", "Голос", "Лицо героя", "Голос", "Окружение"]
    owners = {"b-face": "IRIS", "b-voice": "IRIS", "o-face": "MIRA", "o-voice": "MIRA"}
    body = {"prompt": prompt, "asset_ids": ids, "reference_roles": dict(zip(ids, roles)), "reference_owners": owners,
            "reference_numbers": {i: n for n, i in enumerate(ids, 1)}, "settings": {"duration": 12}}
    sc.normalize(body, {"framing": "two_shot"})
    definitions = parse_ir(body["prompt"])["subject_definitions"]
    assert "<Subject 1> is IRIS, whose face and identity come from <Picture 1>" in definitions
    assert "<Subject 2> is MIRA, whose face and identity come from <Picture 2>" in definitions
    assert "<Audio 1> is the voice-timbre reference for <Subject 1> (S2)." in definitions
    assert "<Audio 2> is the voice-timbre reference for <Subject 2> (S1)." in definitions
    assert "<Picture 3>" in definitions and "<Picture 4>" not in definitions
    action = parse_ir(body["prompt"])["detailed_description"]
    assert "<Subject 2> (S1), whose voice is low calm baritone, says, <d>[English] We found the rehearsal.</d>" in action
    assert not [p for p in sc.problems(body) if "картинка" in p]
    from local_studio.scene_runs import ir_binding_problems
    assert ir_binding_problems(body) == []


def test_a_hero_named_in_a_take_they_are_not_in_is_a_note():
    kit = {"cast": [{"name": "IRIS", "aliases": ["Iris"]}, {"name": "MIRA", "aliases": ["Mira"]}]}
    take = {"cast": [{"name": "MIRA", "ends": "glancing back toward the shutter where Iris is"}]}
    assert sc.absent_hero_mentions(take, {"characters": ["MIRA"]}, kit)[0].startswith("IRIS назван в дубле")
    assert sc.absent_hero_mentions(take, {"characters": ["Mira", "Iris"]}, kit) == []
    assert sc.absent_hero_mentions(take, {}, kit) == []


def test_the_scene_style_names_no_hero():
    kit = {"cast": [{"name": "IRIS", "aliases": ["Iris"]}, {"name": "MIRA", "aliases": ["Mira"]}]}
    assert sc.style_problems("Live action. Iris and Mira are real performers.", kit)[0].startswith("style: IRIS, MIRA")
    assert sc.style_problems("Live action with real performers in physical costumes.", kit) == []


def test_a_shot_style_keeps_the_medium_and_drops_the_absent_hero():
    kit = {"cast": [{"name": "IRIS", "aliases": ["Iris"]}, {"name": "MIRA", "aliases": ["Mira"]}]}
    style = ("Ultra-photoreal live action. Iris, Mira and the surrounding freaks are living performers in physical costumes. "
             "Iris is always loud.")
    assert sc.without_absent_heroes(style, {"characters": ["MIRA"]}, kit) == (
        "Ultra-photoreal live action. Mira and the surrounding freaks are living performers in physical costumes.")
    assert sc.without_absent_heroes("Iris and Mira are real.", {"characters": ["MIRA"]}, kit) == "Mira are real."
    assert sc.without_absent_heroes(style, {"characters": ["Mira", "Iris"]}, kit) == style


def test_more_lines_than_the_take_can_say_is_a_note():
    take = {"lines": [{"speaker": "IRIS", "line": " ".join(["word"] * 30)}]}
    assert sc.speech_problems(take, 12)[0].startswith("реплик 30 слов на 12 с — помещается около 24")
    assert sc.speech_problems({"lines": [{"speaker": "MIRA", "line": "We found the rehearsal."}]}, 12) == []
    assert sc.speech_problems(take, None) == []


def test_opening_speaks_h3_camera_grammar_with_speed_direction_and_every_angle():
    """Until 05.11 every move was «one slow {value} move», point_of_view fell out and «worm» read «a wide worm shot»."""
    assert sc.opening({"framing": "wide", "angle": "eye", "lens_mm": 35, "camera_motion": "dolly",
                       "camera_direction": "forward"}) == (
        "A wide eye-level shot on a 35 mm lens, one continuous take. The camera pushes in at slow speed.")
    assert sc.opening({"framing": "wide", "angle": "worm", "lens_mm": 24, "camera_motion": "pan",
                       "camera_direction": "left", "camera_speed": "fast"}) == (
        "A wide worm's-eye shot on a 24 mm lens, one continuous take. The camera pans left at fast speed.")
    assert sc.opening({"framing": "point_of_view", "angle": "dutch", "camera_motion": "handheld"}) == (
        "A Dutch-angle point-of-view shot, one continuous take. The camera shakes slightly.")
    assert sc.opening({"framing": "close", "angle": "overhead", "lens_mm": 85, "camera_motion": "static"}) == (
        "An overhead close-up on an 85 mm lens, one continuous take. The camera holds a static shot.")
    assert sc.opening({"framing": "two_shot", "angle": "low", "camera_motion": "orbit", "camera_direction": "right",
                       "camera_speed": "medium"}).endswith("low-angle two-shot, one continuous take. The camera arcs "
                                                           "right around the subject.")
    assert sc.opening({"framing": "medium", "angle": "high", "camera_motion": "dolly",
                       "camera_direction": "backward"}).endswith("The camera pulls out at slow speed.")
    # A schema move without the side camera_words needs: no guessed side.
    assert sc.opening({"framing": "insert", "camera_motion": "pan"}).endswith("The camera pans across the scene at slow speed.")
    assert sc.opening({"framing": "full", "camera_motion": "dolly"}, camera=False) == "A full shot, one continuous take."
    for angle in ("eye", "low", "high", "overhead", "dutch", "worm"):
        for framing in sc.FRAMING_WORDS:
            text = sc.opening({"framing": framing, "angle": angle, "camera_motion": "tracking"})
            assert sc.FRAMING.match(text) and "slow tracking move" not in text, text
            assert text.count("The camera") == 1


def test_one_camera_move_once_and_the_staging_line_once():
    """The writer's restated move goes (trained words say it); a camera sentence with a target keeps the move instead;
    the staging line sits right after the opening and normalize at «Собрать сцену» never adds it twice."""
    take = {"summary": "Adam offers the ball.",
            "cast": [{"name": "ADAM", "position": "screen right, kneeling", "does": "rolls the ball", "ends": "waits"}],
            "camera": "The camera holds a static side shot. Focus stays on the ball.", "end": "The ball stops"}
    line = "ADAM is screen right 2 m from the camera, framed from the waist up, looking toward screen left."
    shot = {"framing": "medium", "angle": "eye", "lens_mm": 50, "camera_motion": "static", "staging_line": line}
    action = parse_ir(sc.compose(take, shot))["detailed_description"].split("[Shot 1] ")[1]
    assert action.startswith("A medium eye-level shot on a 50 mm lens, one continuous take. The camera holds a static "
                             "shot. " + line)
    assert "static side shot" not in action and "Focus stays on the ball." in action and action.count(line) == 1
    pushed = {**take, "camera": "The camera pushes slowly into ADAM's eyes as he waits"}
    action = parse_ir(sc.compose(pushed, {**shot, "camera_motion": "dolly"}))["detailed_description"]
    assert action.count("push") == 1 and "into ADAM's eyes" in action
    b = body(sc.compose(take, shot), roles={"k": "Лицо героя", "e": "Окружение"}, owners={"k": "ADAM"})
    sc.normalize(b, shot)
    assert parse_ir(b["prompt"])["detailed_description"].count("ADAM (<Subject 1>) is screen right 2 m") == 1
    # A hand-written take whose shot carries a staging line gets it after the opening.
    b = body(ir("[Shot 1] A wide eye-level shot. The camera holds a static shot. " + ACTING))
    changed = sc.normalize(b, {"framing": "wide", "staging_line": "VEGA is screen left in the foreground, small in the frame."})
    shot_text = parse_ir(b["prompt"])["detailed_description"].split("[Shot 1] ")[1]
    assert shot_text.startswith("A wide eye-level shot. The camera holds a static shot. VEGA (<Subject 2>) is screen left")
    assert "мизансцена шота (кто где в кадре) из постановки" in changed


def test_take_camera_text_against_camera_motion():
    assert sc.camera_text_conflicts({"camera": "The camera holds still on Vega."}, {"camera_motion": "dolly"})[0].startswith(
        "take.camera говорит, что камера стоит, а camera_motion шота — dolly")
    assert "(pan)" in sc.camera_text_conflicts({"camera": "The camera pans left to the door."}, {"camera_motion": "static"})[0]
    assert "orbit" in sc.camera_text_conflicts({"camera": "The camera tilts up to the shelf."}, {"camera_motion": "orbit"})[0]
    assert sc.camera_text_conflicts({"camera": "The camera pushes in slowly."}, {"camera_motion": "dolly"}) == []
    assert sc.camera_text_conflicts({"camera": "It tracks behind him."}, {"camera_motion": "truck"}) == []
    assert sc.camera_text_conflicts({"camera": "Handheld, it follows Adam."}, {"camera_motion": "tracking"}) == []
    assert sc.camera_text_conflicts({"camera": "Focus racks to the door."}, {"camera_motion": "static"}) == []
    assert sc.camera_text_conflicts({"camera": "The camera pans right."}, {}) == []


def test_size_action_conflicts_from_the_06_11_control_map():
    """Measured with one seed: the static size holds only when the shot's actions read in it."""
    from local_studio.shot_contract import size_action_conflicts
    close_with_hands = ("[Shot 1] An extreme close-up of his eyes and wet brows; the camera holds still. He grips "
                        "the wet rope with both hands and wipes rain from his face with his sleeve.")
    close_eyes_only = ("[Shot 1] An extreme close-up of his eyes and wet brows; the camera holds still. Rain runs "
                       "down his brows. His eyes widen and soften.")
    wide_with_face = ("[Shot 1] A wide establishing shot from the bow, he is small at the rail. His eyes widen, his "
                      "breath catches, fear turns into a shaky smile.")
    wide_with_gesture = ("[Shot 1] A wide establishing shot from the bow, a small sailor at the far rail. "
                         "He straightens up, raises one arm toward the light and waves.")
    push_in = "[Shot 1] A medium shot; he grips the rope. The camera pushes in slowly to a close-up of his face."
    assert size_action_conflicts(close_with_hands)
    assert size_action_conflicts(wide_with_face)
    assert not size_action_conflicts(close_eyes_only)
    assert not size_action_conflicts(wide_with_gesture)
    assert not size_action_conflicts(push_in)


def test_size_action_conflicts_ignore_face_acting_and_hero_moves():
    """Review 06.11: no warning for face acting or medium sizes; a hero moving is not a camera move; «Wide from»."""
    from local_studio.shot_contract import size_action_conflicts as c
    assert not c("[Shot 1] Extreme close-up of VEGA's face, the camera holds still. He raises his brows and blinks.")
    assert not c("[Shot 1] Close-up of his face. His hands stay out of frame; his eyes narrow.")
    assert not c("[Shot 1] Medium close-up of ADAM's face; he holds the cup and sips.")
    assert not c("[Shot 1] Close-up of his face; he holds the gaze.")
    assert c("[Shot 1] Wide from the bow. His eyes widen, his breath catches, a smile.")
    assert c("[Shot 1] Extreme close-up of his eyes. He moves closer and grips the rail.")


def test_size_action_conflicts_are_warnings_not_blockers():
    """Measured on one synthetic scene only: advice in preflight, never a reason to stop the take."""
    import inspect
    from local_studio import shot_contract as sc
    assert "size_action_conflicts" not in inspect.getsource(sc.problems)
    assert sc.prompt_warnings("not an IR prompt") == []


def test_optics_and_repeated_light_are_warnings_from_the_06_11_map():
    """Measured with one seed: H3 keeps everything sharp and puts one light event in a shot."""
    from local_studio.shot_contract import optics_light_warnings as w
    assert w("[Shot 1] Shot on an 85 mm lens with shallow depth of field: the lantern melts into soft bokeh.")
    assert w("[Shot 1] A medium shot; focus racks from the lantern to his face as he sees the light.")
    assert w("[Shot 1] Lightning flashes light up the deck at 0:01, 0:02.5 and 0:04.")
    assert w("[Shot 1] He is lit only by bright flashes of lightning: total darkness between flashes.")
    assert not w("[Shot 1] A static medium-wide shot with deep focus: his face, the mast and the lighthouse are sharp.")
    assert not w("[Shot 1] A wide shot. At 0:02.5 one bolt of lightning lights up the whole deck.")
    assert not w("[Shot 1] A close-up. His eyes flash with anger.")


def test_fantasy_take_environment_is_the_imagined_world_not_the_room():
    # JOURNAL 01.11 п.3: the ice-planet shots said «Enclosed balcony» and H3 slid back into the apartment.
    from local_studio.shot_contract import compose
    take = {"summary": "VEGA imagines the balcony as a white ice planet.",
            "cast": [{"name": "VEGA", "does": "VEGA walks across the ice ridge."}], "camera": "Wide shot."}
    real = {"summary": "VEGA sits on the balcony.", "cast": [{"name": "VEGA", "does": "VEGA sits."}], "camera": "Wide shot."}
    place = "An enclosed balcony with plants."
    world = "A white ice planet where the snowdrift is a mountain ridge."
    assert "ice planet where the snowdrift" in compose(take, {}, place=place, fantasy_world=world)
    assert "enclosed balcony" not in compose(take, {}, place=place, fantasy_world=world).lower()
    assert "is the environment" not in compose(take, {}, place=place)  # no world yet: the picture alone
    assert "enclosed balcony" in compose(real, {}, place=place, fantasy_world=world).lower()
    assert "ice planet" in compose(real, {"fantasy": True}, place=place, fantasy_world=world)
