"""«Замена по видео» без ручных промптов: карта шотов от агента, промпт из паспорта, все части в очередь."""
import asyncio
from pathlib import Path

from fastapi import FastAPI

from local_studio import video_swap_auto as auto
from local_studio.routes.jobs import JobInput, validate_source_video_cast

PART = {"index": 1, "label": "часть 1/2", "start": 0.0, "duration": 8, "source_cuts": [3.4, 7.0],
        "segment_asset_id": "seg-1", "form": {"settings": {"mode": "replace", "duration": 8, "cache_mode": "safe"}}}
KIT = {"cast": [{"name": "ADAM", "aliases": ["ADAM"], "line": "", "sheet": {"costume": "Black smiley T-shirt."}},
                {"name": "VEGA", "aliases": ["VEGA"], "line": "", "sheet": {"costume": "Teal collar with VEGA tag."}}]}
HEROES = [{"name": "ADAM", "performer": "the man in a teal beanie", "body_number": 1, "face_number": 2},
          {"name": "VEGA", "performer": "the sphynx cat in a blue shirt", "body_number": 3, "face_number": 4}]
PICTURES = [("adam-body", "Персонаж целиком", "ADAM"), ("adam-face", "Лицо героя", "ADAM"),
            ("vega-body", "Персонаж целиком", "VEGA"), ("vega-face", "Лицо героя", "VEGA"), ("flat", "Окружение", "")]


def test_auto_swap_prepares_draft_without_implicit_queue():
    request = auto.AutoInput(asset_id="source", franchise_id="ka",
                             pairs=[{"hero": "ADAM", "performer": "the man on the left"}])
    assert request.enqueue is False


def test_source_swap_rejects_unmapped_added_hero_before_queue():
    assets = {"video": {"kind": "video", "role": "Исходное видео"},
              "vega": {"kind": "image", "role": "Персонаж целиком"},
              "orion": {"kind": "image", "role": "Персонаж целиком"}}
    payload = JobInput(prompt="[Shot 1] VEGA drives and ORION rides beside him.",
                       asset_ids=list(assets), settings={"mode": "replace"},
                       reference_owners={"vega": "VEGA", "orion": "ORION"},
                       reference_performers={"video": {"pairs": [
                           {"owner": "VEGA", "performer": "the source driver"}]}})
    import pytest
    with pytest.raises(ValueError, match="orion"):
        validate_source_video_cast(payload, assets.get)
    payload.reference_performers["video"]["pairs"].append(
        {"owner": "ORION", "performer": "the source passenger"})
    validate_source_video_cast(payload, assets.get)


def test_source_swap_rejects_unproven_crowd_room_and_poster():
    import pytest
    assets = {"video": {"kind": "video", "role": "Исходное видео"},
              "vega": {"kind": "image", "role": "Персонаж целиком"},
              "orion": {"kind": "image", "role": "Персонаж целиком"},
              "kira": {"kind": "image", "role": "Персонаж целиком"},
              "room": {"kind": "image", "role": "Окружение"},
              "board": {"kind": "image", "role": "Предмет"}}
    pairs = [{"owner": name, "performer": name.lower() + " source actor"}
             for name in ("VEGA", "ORION", "KIRA")]
    payload = JobInput(prompt="[Shot 1] They leave the bank.", asset_ids=["video", "vega", "orion", "kira"],
                       settings={"mode": "replace"},
                       reference_owners={"vega": "VEGA", "orion": "ORION", "kira": "KIRA"},
                       reference_performers={"video": {"pairs": pairs}})
    with pytest.raises(ValueError, match="трёх и более"):
        validate_source_video_cast(payload, assets.get)
    payload.asset_ids.remove("kira")
    pairs.pop()
    payload.asset_ids.append("room")
    with pytest.raises(ValueError, match="смену всей комнаты"):
        validate_source_video_cast(payload, assets.get)
    payload.asset_ids.remove("room")
    payload.prompt = "[Shot 1] VEGA studies a pinned photo of ORION."
    with pytest.raises(ValueError, match="отдельный подготовленный референс"):
        validate_source_video_cast(payload, assets.get)
    payload.asset_ids.append("board")
    validate_source_video_cast(payload, assets.get)


def test_hero_portrait_reference_does_not_require_prop_image():
    assets = {"video": {"kind": "video", "role": "Исходное видео"},
              "vega": {"kind": "image", "role": "Персонаж целиком"}}
    payload = JobInput(prompt="VEGA matches his portrait and full-body references.",
                       asset_ids=["video", "vega"], settings={"mode": "replace"},
                       reference_owners={"vega": "VEGA"},
                       reference_performers={"video": {"pairs": [
                           {"owner": "VEGA", "performer": "the source cat"}]}})
    validate_source_video_cast(payload, assets.get)


def test_shots_follow_the_part_cuts_and_long_shots_get_two_frames():
    spans = auto.shot_spans(PART)
    assert spans == [(0.0, 3.4), (3.4, 7.0), (7.0, 8.0)]
    picks = auto.frame_times(spans)
    assert [n for n, _ in picks] == [1, 1, 2, 2, 3]  # 3.4 s and 3.6 s shots change inside: two frames each
    assert auto.shot_spans({"duration": 8, "source_cuts": [3.4, 7.4]}) == [(0.0, 3.4), (3.4, 8.0)]  # 0.6 s tail joins
    assert auto.shot_spans({"duration": 6, "source_cuts": [0.5, 3.0]}) == [(0.0, 3.0), (3.0, 6.0)]
    many = auto.frame_times([(i, i + 3.0) for i in range(0, 30, 3)])
    assert len(many) == auto.MAX_FRAMES and {n for n, _ in many} == set(range(1, 11))  # every shot keeps one


def test_prompt_takes_the_heroes_from_the_passport_and_actions_from_the_agent():
    spans = auto.shot_spans(PART)
    actions = {1: "Wide: ADAM cleans the litter box while VEGA peeks over the cabinet.",
               2: "Close-up: VEGA narrows his eyes.", 3: "Insert: a pinned photo of ADAM."}
    prompt = auto.compose(KIT, HEROES, 5, PART, 3, spans, actions, "SOURCE LEAK GUARD: only them.")
    assert "<Subject 1> is ADAM: face from <Picture 2>, full body and outfit from <Picture 1>." in prompt
    assert "Black smiley T-shirt." in prompt and "never its clothes, hair, fur or body shape" in prompt
    assert "<Subject 3> is the environment of <Picture 5>" in prompt
    assert "[Shot 2] At 00:03.400, the shot cuts to close-up: VEGA narrows his eyes." in prompt
    assert "Part 1 of 3" in prompt and "SOURCE LEAK GUARD" in prompt
    assert len(auto.short_performer("x " * 60)) <= auto.PERFORMER_MAX


def test_conditional_alternative_costume_does_not_enter_swap_prompt():
    kit = {"cast": [{"name": "VEGA", "aliases": ["VEGA"], "line": "", "sheet": {
        "costume": "Full pink plush bunny suit; the purple wizard robe and hat only when the shot names it."}}]}
    prompt = auto.compose(kit, HEROES[1:], None, PART, 1, [(0.0, 8.0)],
                          {1: "VEGA follows the motorcycle."}, "SOURCE LEAK GUARD: one cat.")
    assert "pink plush bunny suit" in prompt
    assert "wizard robe" not in prompt


def test_shot_mapper_uses_selected_franchise_instead_of_other_cast():
    heroes = [{"name": "IRIS", "performer": "the rider on the left"},
              {"name": "MIRA", "performer": "the rider on the right"}]
    prompt = auto.describe_prompt(heroes, [(0.0, 5.0)], [(1, 2.5)], "часть 1/1",
                                  franchise_id="IRIS_MIRA")
    assert "Target franchise: IRIS_MIRA" in prompt
    assert "the rider on the left is IRIS" in prompt
    assert "ADAM" not in prompt and "VEGA" not in prompt


def test_the_set_replaces_the_source_room_in_the_job():
    job = auto.job_payload({}, PART, HEROES, PICTURES, "flat", "prompt", "a" * 32)
    assert job["asset_ids"] == ["adam-body", "adam-face", "vega-body", "vega-face", "flat", "seg-1"]
    assert job["reference_roles"]["seg-1"] == "Исходное видео" and job["reference_numbers"]["seg-1"] == 1
    assert job["reference_numbers"]["flat"] == 5
    assert job["reference_performers"]["seg-1"]["keep_others"] is False
    plain = auto.job_payload({}, PART, HEROES[:1], PICTURES[:1], None, "prompt", "a" * 32)
    assert plain["reference_performers"]["seg-1"]["keep_others"] is True  # the GPU swap route without a set


def test_run_maps_every_part_and_queues_it(tmp_path, monkeypatch):
    plan = {"ready": True, "source": {"duration": 12.2}, "glue_trim_frames": [0, 0], "glue_tail_frames": [14, 0],
            "parts": [PART, {**PART, "index": 2, "label": "часть 2/2", "start": 7.4, "duration": 5,
                             "source_cuts": [1.5], "segment_asset_id": "seg-2"}]}
    asked = []

    async def brain(req):
        asked.append(req)
        count = req.prompt.count("\nShot ")
        return {"data": {"shots": [{"index": n, "action": f"Action {n}."} for n in range(1, count + 1)]}}

    class Store:
        root = tmp_path

        def get_asset(self, asset_id):
            return {"path": str(tmp_path / f"{asset_id}.mp4")}

    monkeypatch.setattr(auto.video_swap, "plan", lambda store, payload, route: plan)
    monkeypatch.setattr(auto, "hero_set", lambda store, payload, plan: (HEROES, PICTURES, None))
    monkeypatch.setattr(auto.scene_kit, "get_scene_kit", lambda store, fid: KIT)
    monkeypatch.setattr(auto, "extract_frames", lambda video, picks, folder: [str(folder / f"{i}.jpg") for i, _ in enumerate(picks)])
    monkeypatch.setattr(auto.agent_brain, "ask", brain)
    jobs = []
    app = FastAPI()

    @app.post("/api/jobs")
    async def create(payload: dict) -> dict:
        jobs.append(payload)
        return {"job_id": f"job-{len(jobs)}"}

    runs = auto.AutoRuns(tmp_path / "runs")
    run = runs.save({"id": "b" * 32, "state": "planning", "parts": []})
    payload = auto.AutoInput(asset_id="src", franchise_id="f1", enqueue=True,
                             pairs=[{"hero": "ADAM", "performer": "the man"}, {"hero": "VEGA", "performer": "the cat"}])
    asyncio.run(auto.run_auto(Store(), runs, app, run, payload, "gpu"))
    saved = runs.load("b" * 32)
    assert saved["state"] == "awaiting_qc", saved.get("error")
    assert [p.get("job_id") for p in saved["parts"]] == ["job-1", None]
    assert all(r.schema_ == auto.SHOT_SCHEMA and r.images and not r.read_repo for r in asked)
    assert "[Shot 2] At 00:01.500, the shot cuts: Action 2." in saved["parts"][1]["prompt"]
    assert len(jobs) == 1


def test_world_change_stops_auto_swap_before_planning_or_queue(tmp_path, monkeypatch):
    def unexpected(*args):
        raise AssertionError("Character Swap plan must not run for a world change")

    monkeypatch.setattr(auto.video_swap, "plan", unexpected)
    runs = auto.AutoRuns(tmp_path / "runs")
    run = runs.save({"id": "d" * 32, "state": "planning", "parts": []})
    payload = auto.AutoInput(asset_id="src", franchise_id="f1", environment_asset_id="flat", enqueue=True,
                             pairs=[{"hero": "ADAM", "performer": "the man"}])
    asyncio.run(auto.run_auto(object(), runs, FastAPI(), run, payload, "gpu"))
    saved = runs.load("d" * 32)
    assert saved["state"] == "failed" and "Смена мира по видео" in saved["error"]


def test_a_shot_the_agent_skipped_stops_the_run_before_the_gpu(tmp_path, monkeypatch):
    async def brain(req):
        return {"data": {"shots": [{"index": 1, "action": "Only one."}]}}

    class Store:
        root = tmp_path

        def get_asset(self, asset_id):
            return {"path": str(Path(tmp_path) / "v.mp4")}

    monkeypatch.setattr(auto.video_swap, "plan", lambda *a: {"ready": True, "source": {}, "glue_trim_frames": [0],
                                                             "glue_tail_frames": [0], "parts": [PART]})
    monkeypatch.setattr(auto, "hero_set", lambda *a: (HEROES, PICTURES, "flat"))
    monkeypatch.setattr(auto.scene_kit, "get_scene_kit", lambda *a: KIT)
    monkeypatch.setattr(auto, "extract_frames", lambda video, picks, folder: [])
    monkeypatch.setattr(auto.agent_brain, "ask", brain)
    runs = auto.AutoRuns(tmp_path / "runs")
    run = runs.save({"id": "c" * 32, "state": "planning", "parts": []})
    payload = auto.AutoInput(asset_id="src", franchise_id="f1", pairs=[{"hero": "ADAM", "performer": "the man"}])
    asyncio.run(auto.run_auto(Store(), runs, FastAPI(), run, payload, "gpu"))
    saved = runs.load("c" * 32)
    assert saved["state"] == "failed" and "шоты [2, 3]" in saved["error"]


def test_shot_lines_use_the_official_at_timing():
    """06.11: the range form missed cuts by +0.6…+1.8 s, «At MM:SS.mmm, the shot cuts to» hit them (97d46b7c)."""
    from local_studio.video_swap_auto import shot_lines
    lines = shot_lines([(0.0, 1.63), (1.63, 3.4), (3.4, 6.0)],
                       {1: "Medium-wide view: ADAM kneels.", 2: "Cut to a low close insert.", 3: "VEGA sits up."})
    assert lines == ["[Shot 1] Medium-wide view: ADAM kneels.",
                     "[Shot 2] At 00:01.630, the shot cuts to a low close insert.",
                     "[Shot 3] At 00:03.400, the shot cuts: VEGA sits up."]


def test_shot_lines_keep_names_and_sentences():
    """A sentence after the cut stays as written: «aDAM's», «kira», «cuts to he turns» broke the prompt (review 06.11)."""
    from local_studio.video_swap_auto import shot_lines
    lines = shot_lines([(0, 1), (1, 2), (2, 3), (3, 4), (59.9996, 61)],
                       {1: "x", 2: "ADAM's paw lifts.", 3: "He turns to VEGA.", 4: "Kira jumps.", 5: "Wide view of the room."})
    assert lines[1:] == ["[Shot 2] At 00:01.000, the shot cuts: ADAM's paw lifts.",
                         "[Shot 3] At 00:02.000, the shot cuts: He turns to VEGA.",
                         "[Shot 4] At 00:03.000, the shot cuts: Kira jumps.",
                         "[Shot 5] At 01:00.000, the shot cuts to wide view of the room."]
