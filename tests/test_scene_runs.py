"""Scene runs on the Mac: a whole scene goes keyframe → «✓ Принять кадр» → render → last frame → continuation →
glue without the tab, through the same chainStep as the tab (node scripts/scene_run_step.mjs)."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio.scene_runs import SceneRuns, make_router, node_executable

pytestmark = pytest.mark.skipif(not node_executable(), reason="node is needed for the scene step")

BOARD = {"running": False, "colorSource": "off", "strength": 0.6, "message": "", "linkPrevious": True,
         "motionContext": False, "autoKeyframes": True, "keyframesFirst": True, "keyframeCandidates": 1,
         "angleLoraAB": False, "angleAB": {"base": 0, "lora": 0}, "lastFrames": {}}
REFS = [{"id": "hero", "kind": "image", "role": "Лицо героя", "owner": "VEGA", "number": 1, "name": "vega.png"}]


def test_frame_review_cannot_accept_changed_cast(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    cut = {"id": "c1", "prompt": "VEGA waits", "startFrame": {"asset_id": "frame"}}
    run = {"references": list(REFS), "cuts": [cut], "state": "awaiting"}
    snapshot = runs._review_snapshot(run, cut)
    run["references"] = [{**REFS[0], "owner": "ORION"}]
    runs._apply_review(run, "c1", "frame", {"state": "completed", "accept": True}, snapshot)
    assert cut["frameReview"]["state"] == "stale"
    assert "frameAcceptance" not in cut and run["state"] == "awaiting"


def test_only_accepted_takes_reach_the_edit(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    good, bad = {"id": "c1", "shotJobId": "j1"}, {"id": "c2", "shotJobId": "j2"}
    run = {"cuts": [good, bad], "state": "awaiting", "qc_gate_cut_id": "c1"}
    runs._apply_take(run, "c1", "j1", {"state": "completed", "accept": True, "problems": ""})
    assert good["shotQc"] == {"job_id": "j1", "decision": "accept", "by": "claude", "problems": ""}
    runs._apply_take(run, "c2", "j2", {"state": "completed", "accept": False, "problems": "extra cat"})
    assert bad["shotJobId"] is None and bad["takeTries"] == 2 and "flagged" not in run  # first reject: re-shoot
    bad["shotJobId"] = "j2"
    runs._apply_take(run, "c2", "j2", {"state": "completed", "accept": False, "problems": "extra cat"})
    assert not bad.get("shotQcReview") and not bad.get("shotQc") and run["state"] == "stopped"  # never glued
    assert run["flagged"] == [{"shot": 2, "cut_id": "c2", "job_id": "j2", "problems": "extra cat"}]
    runs._apply_take(run, "c2", "old-take", {"state": "completed", "accept": True})  # a stale verdict changes nothing
    assert len(run["flagged"]) == 1 and run["state"] == "stopped"
    unchecked = {"id": "c3", "shotJobId": "j3"}
    run.update(cuts=[good, bad, unchecked], state="awaiting")
    runs._apply_take(run, "c3", "j3", {"state": "failed", "error": "claude limit"})
    assert "shotQc" not in unchecked and "takeReview" not in unchecked and run["state"] == "awaiting"


def test_gpu_waits_only_for_an_earlier_take_in_flight(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    jobs = [{"job_id": "a1", "state": "running"}]

    async def api(method, url, body=None):
        return jobs

    runs._api = api
    first = runs.save({"scene_id": "scene-a", "title": "A", "state": "running", "options": {"order": 1},
                       "cuts": [{"id": "c1", "shotJobId": "a1"}]})
    second = runs.save({"scene_id": "scene-b", "title": "B", "state": "running", "options": {"order": 2}, "cuts": []})
    assert asyncio.run(runs._gpu_turn(first)) is None and asyncio.run(runs._gpu_turn(second)) == "A"
    jobs[0]["state"] = "completed"  # A now waits for a frame: B takes the idle GPU
    assert asyncio.run(runs._gpu_turn(second)) is None


def test_lost_enqueue_response_blocks_resubmission(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    run = {"scene_id": "scene-a", "cuts": [{"id": "c1", "duration": 5}],
           "templates": {"c1": {"mode": "none", "body": {**body("frame"), "asset_ids": ["hero"]}}}}
    calls = []

    async def lost_response(method, url, body=None):
        calls.append(url)
        if method == "GET":
            return {"job_id": "already-accepted", "state": "queued_local"}
        raise httpx.ReadTimeout("response lost")

    runs._api = lost_response
    step = {"cutId": "c1", "index": 0, "plan": {}}

    async def scenario():
        with pytest.raises(httpx.ReadTimeout):
            await runs._enqueue(run, step)
        saved = runs.load("scene-a")
        assert saved["pending_submission"]["cut_id"] == "c1"
        job = await runs._enqueue(saved, step)
        assert job["job_id"] == "already-accepted"
        assert len(calls) == 2 and calls[0] == "/api/jobs" and calls[1].startswith("/api/job-receipts/")
        assert "pending_submission" not in runs.load("scene-a")

    asyncio.run(scenario())


ACTING = ("[Shot 1] A wide eye-level shot. VEGA sits at screen left on the floor and watches the window; his ears turn "
          "forward one after the other, he lowers his chest, lifts one forepaw and steps toward the glass, then stops when "
          "a bird lands outside. VEGA crouches, tail twitching, and creeps two careful steps closer; he ends nose to the "
          "glass with wide eyes while the bird flies away.")


def body(token):
    return {"prompt": "subject_definitions:\n<Picture 1> VEGA.\n\nsummary:\n[reference generation] Cat.\n\n"
                      "retention_analysis:\n<Picture 1> kept.\n\ndetailed_description:\n" + ACTING
                      + "\n\noverall_soundscape:\nRoom tone.\n\nnon_diegetic_music:\nNone.", "asset_ids": ["hero", token],
            "reference_numbers": {"hero": 1, token: 2}, "reference_roles": {"hero": "Лицо героя", token: "Кадр сцены"},
            "reference_owners": {"hero": "VEGA"}, "reference_performers": {},
            "settings": {"duration": 12, "guide_frames": [[2, 0]]}}


KIT = {"franchise_id": "fk", "franchise": "ADAM & VEGA", "mode": "", "style": "", "cast": [
    {"name": "VEGA", "aliases": ["VEGA", "Vega"], "line": "", "sheet": {}, "assets": ["hero"]}], "places": [],
    "allowed": {"hero": {"roles": ["Лицо героя"], "owners": ["VEGA"]}, "room": {"roles": [], "owners": []}},
    "foreign": {"ORION": "f2"}, "shared_excluded": [], "budget": {"min_take_s": 10, "max_take_s": 15,
    "min_prompt_words": 0, "max_prompt_words": 2000, "max_images": 9, "max_static_share": 1, "max_shared_action": 1}}
FK = {"franchise_id": "fk"}


def fake_studio(log):
    api = FastAPI()
    jobs, keyframes = {}, {}

    @api.get("/api/franchises/fk/profile")
    async def kit_profile():
        return {"roles": []}

    @api.get("/api/franchises/fk/scene-kit")
    async def kit():
        return KIT

    @api.get("/api/jobs")
    async def list_jobs():
        return list(jobs.values())

    @api.post("/api/jobs", status_code=202)
    async def create(payload: dict):
        job_id = f"j{len(jobs) + 1}"
        log.append(("job", payload))
        jobs[job_id] = {"job_id": job_id, "state": "queued_gpu"}
        return jobs[job_id]

    @api.post("/api/keyframes/jobs", status_code=202)
    async def keyframe(payload: dict):
        log.append(("keyframe", payload))
        keyframes["k1"] = {"keyframe_id": "k1", "state": "running"}
        return keyframes["k1"]

    @api.get("/api/keyframes/jobs/{key}")
    async def keyframe_get(key: str):
        return keyframes[key]

    @api.post("/api/jobs/{job_id}/last-frame", status_code=201)
    async def last_frame(job_id: str):
        log.append(("last", job_id))
        return {"asset_id": f"last-{job_id}", "kind": "image"}

    @api.post("/api/storyboard/concat")
    async def concat(payload: dict):
        log.append(("concat", payload["job_ids"]))
        return {"concat_id": "c", "url": "/x.mp4", "duration": 10.0, "job_ids": payload["job_ids"]}

    return api, jobs, keyframes


def test_motion_continuation_uses_previous_take_tail(tmp_path):
    log = []
    api, _, _ = fake_studio(log)

    @api.post('/api/jobs/{job_id}/reference-video')
    async def reference_video(job_id: str):
        return {'asset_id': f'video-{job_id}'}

    @api.get('/api/assets/{uid}/excerpt-info')
    async def excerpt_info(uid: str):
        return {'duration': 10.0}

    @api.post('/api/assets/{uid}/excerpt')
    async def excerpt(uid: str, payload: dict):
        log.append(('excerpt', payload))
        return {'asset_id': 'tail-video'}

    runs = SceneRuns(lambda: tmp_path)
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://127.0.0.1')
    run = {'cuts': [{'id': 'c1', 'shotJobId': 'j1'}], 'board': {}}

    async def scenario():
        assert await runs._resolve(run, 'tail', 'c1', 5) == 'tail-video'
        assert log == [('excerpt', {'start': 7.0, 'duration': 3.0})]
        await runs.client.aclose()

    asyncio.run(scenario())


def test_scene_start_rejects_malformed_official_ir_before_any_job(tmp_path):
    log = []
    api, _, _ = fake_studio(log)
    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://127.0.0.1')
    malformed = ('subject_definitions:\n<Subject 1> VEGA\nsummary:\nVEGA walks.\n'
                 'retention_analysis:\nVEGA: retained\ndetailed_description:\n[Shot 1] VEGA walks.\n'
                 'overall_soundscape:\nQuiet.\nnon_diegetic_music:\nNone.\n'
                 'Shot-specific action direction: VEGA runs.')

    async def scenario():
        result = await runs.client.post('/api/scene-runs/bad', json={
            'cuts': [{'id': 'c1', 'duration': 5}], 'board': BOARD, 'references': REFS, 'options': FK,
            'templates': {'c1': {'mode': 'keyframe', 'body': {**body('studio-run:start:c1'), 'prompt': malformed}}}})
        assert result.status_code == 422
        assert not runs.load('bad') and not log
        await runs.client.aclose()

    asyncio.run(scenario())


def test_scene_start_rejects_foreign_character_reference(tmp_path):
    log = []
    api, _, _ = fake_studio(log)

    @api.get('/api/franchises/f1/profile')
    async def profile():
        return {'roles': [{'name': 'VEGA', 'kind': 'character', 'assets': [{'asset_id': 'vega-face'}]}]}

    @api.get('/api/franchises/f1/entities')
    async def entities():
        return []

    @api.get('/api/franchises/f1/scene-kit')
    async def foreign_kit():
        return {**KIT, 'franchise_id': 'f1', 'allowed': {'vega-face': {'roles': ['Лицо героя'], 'owners': ['VEGA']}}}

    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://127.0.0.1')

    async def scenario():
        result = await runs.client.post('/api/scene-runs/foreign', json={
            'cuts': [{'id': 'c1', 'duration': 5}], 'board': BOARD,
            'references': [{'id': 'oddleys-face', 'role': 'Лицо героя', 'owner': 'VEGA'}],
            'options': {'franchise_id': 'f1'},
            'templates': {'c1': {'mode': 'keyframe', 'body': body('studio-run:start:c1')}}})
        assert result.status_code == 422 and 'паспорт' in result.json()['detail']
        assert not runs.load('foreign') and not log
        await runs.client.aclose()

    asyncio.run(scenario())


def test_scene_builds_on_the_mac_without_the_tab(tmp_path):
    log = []
    api, jobs, keyframes = fake_studio(log)
    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1")
    drawn = asyncio.Event()

    async def codex_frame(request, references, feedback):  # Codex draws the start frame (no GPU keyframe model)
        log.append(("keyframe", request))
        await drawn.wait()
        return {"asset_id": "kf1"}

    runs._codex_frame = codex_frame
    cuts = [{"id": "c1", "prompt": "VEGA walks to the window", "duration": 5, "startFrame": {"source": "keyframe", "asset_id": None}},
            {"id": "c2", "prompt": "VEGA sits down", "duration": 5, "startFrame": {"source": "previous", "asset_id": None}}]
    templates = {"c1": {"mode": "keyframe", "body": body("studio-run:start:c1")},
                 "c2": {"mode": "previous", "body": body("studio-run:last:c1")}}

    async def scenario():
        client = runs.client
        run = (await client.post("/api/scene-runs/scene-a", json={"cuts": cuts, "board": BOARD, "references": REFS,
                                                                  "source_context": {"seed": 42, "renderState": {"steps": 24}},
                                                                  "options": {**FK, "aspect": "9:16"}, "templates": templates})).json()
        assert run["state"] == "running" and "templates" not in run
        assert runs.load("scene-a")["context_snapshot"]["context"] == {"seed": 42, "renderState": {"steps": 24}}
        await asyncio.sleep(0)
        assert log[0][0] == "keyframe" and log[0][1]["asset_ids"] == ["hero"]
        duplicate = await client.post("/api/scene-runs/scene-a", json={"cuts": cuts, "board": BOARD, "references": REFS,
                                                                        "options": {**FK, "aspect": "9:16"}, "templates": templates})
        assert duplicate.status_code == 409 and len(log) == 1

        drawn.set()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if not runs.tasks:
                break
        run = await runs.advance("scene-a")
        assert run["state"] == "awaiting" and run["awaiting"] == [1]  # the frame waits for the user
        assert run["cuts"][0]["startFrame"] == {"source": "keyframe", "asset_id": "kf1"}
        assert not any(kind == "job" for kind, _ in log)

        run = (await client.patch("/api/scene-runs/scene-a/cuts/c1",
                                  json={"frameAcceptance": {"asset_id": "kf1", "spec_revision": None}})).json()
        sent = [payload for kind, payload in log if kind == "job"]
        assert run["state"] == "running" and run["cuts"][0]["shotJobId"] == "j1"
        assert sent[0]["asset_ids"] == ["hero", "kf1"] and sent[0]["reference_numbers"] == {"hero": 1, "kf1": 2}

        jobs["j1"]["state"] = "completed"
        run = await runs.advance("scene-a")
        sent = [payload for kind, payload in log if kind == "job"]
        assert run["state"] == "awaiting" and run["qc_gate_cut_id"] == "c1" and len(sent) == 1
        run = (await client.patch("/api/scene-runs/scene-a/cuts/c1",
                                  json={"shotQc": {"job_id": "j1", "decision": "accept"},
                                        "shotQcReview": "accepted"})).json()
        sent = [payload for kind, payload in log if kind == "job"]
        assert ("last", "j1") in log and run["board"]["lastFrames"] == {"j1": "last-j1"}
        assert sent[1]["asset_ids"] == ["hero", "last-j1"] and sent[1]["reference_roles"]["last-j1"] == "Кадр сцены"
        assert run["cuts"][1]["startFrame"] == {"source": "previous", "asset_id": "last-j1", "from_job": "j1"}

        jobs["j2"]["state"] = "completed"
        run = await runs.advance("scene-a")
        assert run["state"] == "awaiting" and ("concat", ["j1", "j2"]) not in log
        run = (await client.patch("/api/scene-runs/scene-a/cuts/c2",
                                  json={"shotQc": {"job_id": "j2", "decision": "accept"},
                                        "shotQcReview": "accepted"})).json()
        assert run["state"] == "done" and ("concat", ["j1", "j2"]) in log

        assert (await client.post("/api/scene-runs/scene-a/stop")).json()["state"] == "stopped"
        assert (await client.patch("/api/scene-runs/scene-a/cuts/c1", json={"shotJobId": "x"})).status_code == 422
        await client.delete("/api/scene-runs/scene-a")
        assert (await client.get("/api/scene-runs/scene-a")).status_code == 404

    asyncio.run(scenario())


def test_changed_shot_mode_needs_a_new_build(tmp_path):
    log = []
    api, jobs, _ = fake_studio(log)
    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1")
    cuts = [{"id": "c1", "prompt": "VEGA walks", "duration": 5, "startFrame": {"source": "library", "asset_id": "pic"}}]

    async def scenario():
        run = (await runs.client.post("/api/scene-runs/s", json={"cuts": cuts, "board": BOARD, "references": REFS, "options": FK,
                                                                 "templates": {"c1": {"mode": "none", "body": body("pic")}}})).json()
        assert run["state"] == "stopped" and "Собрать сцену" in run["message"]
        assert not any(kind == "job" for kind, _ in log)

    asyncio.run(scenario())


def test_overnight_codex_draws_claude_accepts(tmp_path, monkeypatch):
    """No GPU keyframe and no user: Codex draws, Claude rejects once (redraw with its note), then accepts."""
    from fastapi import File, UploadFile
    from local_studio import scene_frames
    log = []
    api, jobs, _ = fake_studio(log)
    uploads = []

    @api.get("/api/assets/{asset_id}/file")
    async def asset_file(asset_id: str):
        from fastapi.responses import Response
        return Response(b"\x89PNG fake", media_type="image/png")

    @api.post("/api/assets", status_code=201)
    async def upload(role: str, file: UploadFile = File(...)):
        uploads.append(role)
        return {"asset_id": f"drawn-{len(uploads)}", "kind": "image"}

    drawn, verdicts = [], [{"accept": False, "problems": "VEGA has the wrong costume"}, {"accept": True, "problems": ""}]

    async def fake_draw(request, references, pictures, feedback=""):
        drawn.append((feedback, [ref["role"] for ref in references]))
        return b"png"

    async def fake_review(frame, text, references, pictures):
        return verdicts.pop(0)

    monkeypatch.setattr(scene_frames, "draw", fake_draw)
    monkeypatch.setattr(scene_frames, "review", fake_review)
    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1")
    cuts = [{"id": "c1", "prompt": "VEGA walks to the window", "duration": 5, "startFrame": {"source": "keyframe", "asset_id": None}}]
    templates = {"c1": {"mode": "keyframe", "body": body("studio-run:start:c1")}}

    async def settle():
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not runs.tasks:
                return

    async def scenario():
        await runs.client.post("/api/scene-runs/s", json={"cuts": cuts, "board": BOARD, "references": REFS,
                                                          "options": {**FK, "autoAccept": True}, "templates": templates})
        for _ in range(8):  # what the 3 s loop does
            await settle()
            await runs.advance("s")
        run = runs.load("s")
        assert not any(kind == "keyframe" for kind, _ in log)  # no GPU keyframe
        assert drawn == [("", ["Лицо героя"]), ("VEGA has the wrong costume", ["Лицо героя"])]
        assert run["cuts"][0]["frameAcceptance"]["asset_id"] == "drawn-2"
        assert run["cuts"][0]["frameAcceptance"]["accepted_by"] == "claude"
        sent = [payload for kind, payload in log if kind == "job"]
        assert sent and sent[0]["asset_ids"] == ["hero", "drawn-2"]
        assert run["state"] == "running" and uploads == ["Кадр сцены", "Кадр сцены"]

    asyncio.run(scenario())


def test_two_panel_idea_sheet_is_detected():
    import io
    from PIL import Image, ImageDraw
    from local_studio.scene_frames import split_panel

    def png(image):
        out = io.BytesIO()
        image.save(out, "PNG")
        return out.getvalue()

    sheet = Image.new("RGB", (1200, 675), (200, 170, 120))
    ImageDraw.Draw(sheet).rectangle([600, 0, 1199, 674], fill=(40, 50, 90))  # «real | fantasy» halves
    assert split_panel(png(sheet))
    room = Image.new("RGB", (1200, 675), (200, 170, 120))
    ImageDraw.Draw(room).rectangle([450, 200, 700, 500], fill=(40, 50, 90))  # an object, not a seam
    assert not split_panel(png(room))
    assert not split_panel(png(Image.new("RGB", (768, 1344), (10, 10, 10))))


def test_pruning_leftovers_block_the_scene():
    from local_studio.scene_runs import prompt_defects
    ok = {"prompt": "<Picture 1> VEGA face; <Picture 2> street. [Shot 1: 0-5s] VEGA waits.", "asset_ids": ["a", "b", "s"],
          "settings": {"duration": 5}}
    assert prompt_defects(ok) == []
    assert prompt_defects({**ok, "prompt": "VEGA face; ; ; <Picture 2> street."})
    assert prompt_defects({**ok, "prompt": "<Subject 1> ADAM: preserve face from . <Subject 2> VEGA"})
    assert prompt_defects({**ok, "prompt": "Preserve ADAM and VEGA from Pictures 1-4."})
    assert prompt_defects({**ok, "prompt": "[Shot 2: 4.5-9s] VEGA jumps."})


def test_launch_runs_the_job_ir_check():
    from local_studio.scene_runs import ir_binding_problems
    body = {"asset_ids": ["k1", "k2"], "reference_roles": {"k1": "Лицо героя", "k2": "Персонаж целиком"},
            "reference_owners": {"k1": "ADAM", "k2": "ADAM"}}
    sections = "\n\nsummary:\n[reference generation] ADAM waits.\n\nretention_analysis:\n<Subject 1> fully_preserved.\n\n" \
               "detailed_description:\n[Shot 1] ADAM waits.\n\noverall_soundscape:\nRoom tone.\n\nnon_diegetic_music:\nNone."
    bad = {**body, "prompt": "subject_definitions:\n<Subject 1> ADAM: preserve face from <Picture 1> and body from <Picture 2>." + sections}
    good = {**body, "prompt": "subject_definitions:\n<Subject 1> is ADAM, whose face comes from <Picture 1> and whose body comes from <Picture 2>." + sections}
    assert ir_binding_problems(bad) and not ir_binding_problems(good)


def test_three_rejected_frames_stop_for_the_user(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    cut = {"id": "c1", "startFrame": {"source": "keyframe", "asset_id": "f3"}, "frameTries": 3}
    body = {"asset_ids": ["hero", "studio-run:start:c1"], "reference_roles": {"hero": "Лицо героя", "studio-run:start:c1": "Кадр сцены"},
            "reference_numbers": {"hero": 1, "studio-run:start:c1": 2},
            "settings": {"guide_frames": [[2, 0]], "color_match": {"picture": 2, "strength": 0.6}}}
    run = {"references": list(REFS), "cuts": [cut], "state": "awaiting", "options": {"autoAcceptTakes": True},
           "templates": {"c1": {"mode": "keyframe", "body": body}}}
    runs._apply_review(run, "c1", "f3", {"state": "completed", "accept": False, "problems": "wrong shirt"})
    assert run["templates"]["c1"]["mode"] == "keyframe" and cut["frameReview"]["state"] == "gave_up"
    assert run["state"] != "running" and "flagged" not in run


def test_one_picture_from_two_tokens_is_sent_once():
    from local_studio.scene_runs import drop_duplicate_pictures
    body = {"asset_ids": ["hero", "studio-run:last:c1", "studio-run:start:c1", "room"],
            "prompt": "Use <Picture 3> beside Picture 4; keep Pictures 1-4 coherent.",
            "reference_roles": {"hero": "Лицо героя", "studio-run:last:c1": "Кадр сцены", "studio-run:start:c1": "Окружение", "room": "Окружение"},
            "settings": {"guide_frames": [[2, 0]], "color_match": {"picture": 3}}}
    drop_duplicate_pictures(body, {"studio-run:last:c1": "f9", "studio-run:start:c1": "f9"})
    assert body["asset_ids"] == ["hero", "studio-run:last:c1", "room"]
    assert body["settings"]["guide_frames"] == [[2, 0]] and body["settings"]["color_match"]["picture"] == 2
    assert body["reference_numbers"] == {"hero": 1, "studio-run:last:c1": 2, "room": 3}
    assert body["prompt"] == "Use <Picture 2> beside Picture 3; keep Pictures 1-3 coherent."


def test_scene_medium_follows_the_franchise_passport():
    from local_studio.scene_runs import medium_mismatch
    photoreal = {"director_defaults": {"style": {"rendering": "photoreal"}}}
    cartoon = {"c1": {"body": {"settings": {"mode": "animation3d"}}}}
    assert "c1" in medium_mismatch(photoreal, cartoon)
    assert medium_mismatch(photoreal, {"c1": {"body": {"settings": {"mode": "realistic"}}}}) == ""
    assert medium_mismatch({"director_defaults": {"style": {"rendering": "animation3d"}}}, cartoon) == ""
    assert medium_mismatch({}, cartoon) == ""


def test_contract_endpoint_checks_a_draft_before_the_user_sees_it(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    api = FastAPI()
    api.include_router(make_router(runs))
    client = TestClient(api)
    refs = [{"asset_id": "a1", "role": "Лицо героя", "owner": "VEGA"}, {"asset_id": "yard", "role": "Окружение"}]
    shot = {"prompt": "<Picture 1> VEGA face; <Picture 2> yard. [Shot 1] VEGA walks.", "duration": 5,
            "start_frame": "new_keyframe", "framing": "wide", "angle": "eye", "lens_mm": 35, "camera_motion": "static"}
    bad = client.post("/api/scene-runs/contract", json={"references": refs, "shots": [shot]}).json()
    assert not bad["ok"] and any("дубль 5 с" in p for p in bad["problems"])
    good = client.post("/api/scene-runs/contract", json={"references": refs, "shots": [
        {**shot, "duration": 12, "prompt": body("x")["prompt"]}]}).json()
    assert good == {"ok": True, "problems": []}


def test_compose_endpoint_writes_the_staging_line_and_warns_on_a_second_camera_move(tmp_path):
    api = FastAPI()
    api.include_router(make_router(SceneRuns(lambda: tmp_path)))
    line = "VEGA is screen left 1.2 m from the camera, in close-up, looking toward screen right."
    take = {"summary": "Vega waits.", "cast": [{"name": "VEGA", "position": "screen left", "does": "waits", "ends": "sits"}],
            "camera": "The camera pans left to the door.", "end": "Vega sits"}
    shot = {"take": take, "framing": "close", "angle": "eye", "lens_mm": 85, "camera_motion": "static",
            "staging_line": line, "duration": 12}
    reply = TestClient(api).post("/api/scene-runs/compose", json={"shots": [shot]}).json()
    assert reply["prompts"][0].count(line) == 1
    assert reply["warnings"] == ["шот 1: take.camera называет движение (pan), а camera_motion шота — static: "
                                 "оставь одно — поправь camera_motion или текст"]


def test_scene_without_franchise_is_refused(tmp_path):
    log = []
    api, _, _ = fake_studio(log)
    runs = SceneRuns(lambda: tmp_path)
    api.include_router(make_router(runs))
    runs.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://127.0.0.1")

    async def scenario():
        result = await runs.client.post("/api/scene-runs/loose", json={
            "cuts": [{"id": "c1", "duration": 12}], "board": BOARD, "references": REFS,
            "templates": {"c1": {"mode": "keyframe", "body": body("studio-run:start:c1")}}})
        assert result.status_code == 422 and "франшизы" in result.json()["detail"]
        assert not runs.load("loose") and not log
        await runs.client.aclose()

    asyncio.run(scenario())


def test_take_outside_the_kit_never_reaches_the_gpu(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    foreign = {**body("frame"), "asset_ids": ["hero", "oddleys-face"],
               "reference_roles": {"hero": "Лицо героя", "oddleys-face": "Лицо героя"},
               "reference_owners": {"hero": "VEGA", "oddleys-face": "VEGA"}}
    run = {"scene_id": "scene-k", "kit": KIT, "cuts": [{"id": "c1", "duration": 12}],
           "templates": {"c1": {"mode": "none", "body": foreign}}}
    sent = []

    async def api(method, url, body=None):
        sent.append(url)
        return {"job_id": "j1"}

    runs._api = api
    with pytest.raises(Exception, match="не из комплекта"):
        asyncio.run(runs._enqueue(run, {"cutId": "c1", "index": 0, "plan": {}}))
    assert not sent


def test_legacy_run_cannot_resume_without_franchise_kit(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    scene_id = "scene-12345678-1234-1234-1234-123456789abc"
    runs.save({"scene_id": scene_id, "state": "stopped", "cuts": [], "board": {}, "options": {}})
    app = FastAPI()
    app.include_router(make_router(runs))
    response = TestClient(app).post(f"/api/scene-runs/{scene_id}/resume")
    assert response.status_code == 409
    assert "Старая сборка" in response.json()["detail"]
    assert runs.load(scene_id)["state"] == "stopped"


def test_a_hero_absent_from_the_shot_sends_no_picture_and_no_voice():
    """A shot with only MIRA must not carry IRIS's face, body and voice nor tell H3 she «appears in [Shot 1]»."""
    from local_studio.scene_runs import drop_absent_heroes
    ids = ["b-face", "b-body", "b-voice", "o-face", "lane", "start"]
    body = {"prompt": "<Picture 5> is the first frame. Picture 4 is the lane.", "asset_ids": list(ids),
            "reference_roles": dict(zip(ids, ["Лицо героя", "Персонаж целиком", "Голос", "Лицо героя", "Окружение",
                                               "Кадр сцены"])),
            "reference_owners": {"b-face": "IRIS", "b-body": "IRIS", "b-voice": "IRIS", "o-face": "Mira"},
            "settings": {"guide_frames": [[5, 0]], "color_match": {"picture": 5}}}
    assert drop_absent_heroes(body, {"characters": ["MIRA"]}) == ["IRIS"]
    assert body["asset_ids"] == ["o-face", "lane", "start"]
    assert body["reference_numbers"] == {"o-face": 1, "lane": 2, "start": 3}
    assert set(body["reference_owners"]) == {"o-face"}
    assert body["settings"]["guide_frames"] == [[3, 0]] and body["settings"]["color_match"]["picture"] == 3
    assert body["prompt"] == "<Picture 3> is the first frame. Picture 2 is the lane."
    keep = {"asset_ids": ["b-face"], "reference_roles": {"b-face": "Лицо героя"}, "reference_owners": {"b-face": "IRIS"}}
    assert drop_absent_heroes(keep, {"characters": ["Iris"]}) == [] and drop_absent_heroes(keep, {}) == []


def test_reviewers_get_this_shots_heroes_and_the_passport_medium():
    from local_studio.scene_runs import shot_pictures, take_review_rules
    refs = [{"id": "lane", "kind": "image", "role": "Окружение"},
            {"id": "b", "kind": "image", "role": "Лицо героя", "owner": "IRIS"},
            {"id": "o", "kind": "image", "role": "Лицо героя", "owner": "MIRA"},
            {"id": "v", "kind": "audio", "role": "Голос", "owner": "MIRA"}]
    assert shot_pictures(refs, {"characters": ["Mira"]}) == ["lane", "o"]
    assert shot_pictures(refs, {}) == ["lane", "b", "o"]
    kit = {"mode": "realistic", "cast": [{"name": "IRIS", "aliases": ["Iris"]}, {"name": "MIRA", "aliases": ["Mira"]}]}
    rules = take_review_rules(kit, {"characters": ["MIRA"]})
    assert rules[0].startswith("Medium of the franchise: live-action photoreal") and "cartoon" in rules[0]
    assert rules[1] == "Heroes in this shot: MIRA. IRIS must not appear; any of them in the frame is a reject."
    assert take_review_rules(None, {"characters": ["MIRA"]}) == []


def test_a_re_shoot_rolls_a_new_seed(tmp_path):
    """The template's fixed seed made the re-shoot of a rejected take the same video with the same defect."""
    runs = SceneRuns(lambda: tmp_path)
    template = {**body("frame"), "asset_ids": ["hero"]}
    template["settings"] = {**template.get("settings", {}), "seed": 1234}
    sent = []

    async def api(method, url, payload=None):
        sent.append(payload)
        return {"job_id": f"j{len(sent)}", "state": "queued_local"}

    runs._api = api
    for tries in (1, 2):
        run = {"scene_id": "scene-a", "cuts": [{"id": "c1", "duration": 5, "takeTries": tries}],
               "templates": {"c1": {"mode": "none", "body": json.loads(json.dumps(template))}}}
        asyncio.run(runs._enqueue(run, {"cutId": "c1", "index": 0, "plan": {}}))
    assert sent[0]["settings"]["seed"] == 1234 and sent[1]["settings"]["seed"] is None


def test_a_keyframe_is_asked_in_the_passport_medium(tmp_path):
    """The medium comes from the passport, not only from a scene style the agent could omit."""
    runs = SceneRuns(lambda: tmp_path)
    posted = []

    async def codex_frame(request, references, feedback):
        return {"asset_id": "kf1"}

    def spawn(scene_id, key, work, apply=None):
        work.close()  # only the request Codex would get matters here

    runs._codex_frame = lambda request, references, feedback: (posted.append(request), codex_frame(request, references, feedback))[1]
    runs._spawn = spawn
    run = {"scene_id": "scene-a", "kit": {"mode": "realistic"}, "options": {},
           "cuts": [{"id": "c1"}], "references": []}
    asyncio.run(runs._order_keyframe(run, {"cutId": "c1", "request": {"prompt": "x" * 3800, "asset_ids": []}}))
    assert posted[0]["prompt"].startswith("Medium: a photoreal live-action film still")
    assert len(posted[0]["prompt"]) <= 3800


def test_a_shot_with_no_hero_gets_no_hero_pictures():
    """characters: [] is an empty room, not «unknown»: the heroes' faces stay out and the reviewer expects nobody."""
    from local_studio.scene_runs import drop_absent_heroes, shot_pictures, take_review_rules
    body = {"asset_ids": ["b-face", "room"], "reference_roles": {"b-face": "Лицо героя", "room": "Окружение"},
            "reference_owners": {"b-face": "IRIS"}}
    assert drop_absent_heroes(body, {"characters": []}) == ["IRIS"] and body["asset_ids"] == ["room"]
    refs = [{"id": "room", "kind": "image", "role": "Окружение"}, {"id": "b", "kind": "image", "role": "Лицо героя", "owner": "IRIS"}]
    assert shot_pictures(refs, {"characters": []}) == ["room"] and shot_pictures(refs, {}) == ["room", "b"]
    kit = {"cast": [{"name": "IRIS", "aliases": ["Iris"]}]}
    assert take_review_rules(kit, {"characters": []}) == [
        "No franchise hero is in this shot. IRIS must not appear; any of them in the frame is a reject."]


def test_take_verdict_cannot_accept_edited_shot(tmp_path):
    runs = SceneRuns(lambda: tmp_path)
    cut = {"id": "c1", "shotJobId": "j1", "prompt": "Vega waits"}
    run = {"references": list(REFS), "cuts": [cut], "state": "awaiting"}
    snapshot = runs._review_snapshot(run, cut)
    cut["prompt"] = "Orion runs"
    runs._apply_take(run, "c1", "j1", {"state": "completed", "accept": True}, snapshot)
    assert cut["takeReview"]["state"] == "stale"
    assert not cut.get("shotQcReview") and run["state"] == "awaiting"


@pytest.mark.parametrize('verdict', ['accepted', 'rejected'])
def test_manual_take_decision_supersedes_review_and_old_callback(tmp_path, verdict):
    runs = SceneRuns(lambda: tmp_path)
    cut = {'id': 'c1', 'shotJobId': 'j1', 'takeReview': {'job_id': 'j1', 'state': 'running', 'key': 'old-check'}}
    runs.save({'scene_id': 'manual', 'state': 'running' if verdict == 'rejected' else 'stopped',
               'cuts': [cut], 'references': []})
    app = FastAPI()
    app.include_router(make_router(runs))
    async def no_advance(scene_id):
        return runs.load(scene_id)
    runs.advance = no_advance
    response = TestClient(app).patch('/api/scene-runs/manual/cuts/c1', json={
        'shotQc': {'job_id': 'j1', 'decision': 'manual_review'}, 'shotQcReview': verdict})
    assert response.status_code == 200
    saved = runs.load('manual')
    assert saved['cuts'][0]['takeReview']['by'] == 'user'
    assert saved['cuts'][0]['takeReview']['state'] == verdict
    assert saved['cuts'][0]['takeReviewHistory'][0]['key'] == 'old-check'
    if verdict == 'rejected':
        assert saved['state'] == 'stopped' and saved['qc_gate_cut_id'] == 'c1'
    saved['state'] = 'awaiting'
    runs._apply_take(saved, 'c1', 'j1', {'state': 'completed', 'accept': verdict != 'accepted'}, review_key='old-check')
    assert saved['cuts'][0]['shotQcReview'] == verdict
    assert saved['cuts'][0]['takeReview']['by'] == 'user'
    invalid = TestClient(app).patch('/api/scene-runs/manual/cuts/c1', json={
        'shotQc': {'job_id': 'other', 'decision': 'manual_review'}, 'shotQcReview': verdict})
    assert invalid.status_code == 422
    if verdict == 'rejected':
        unbound = TestClient(app).patch('/api/scene-runs/manual/cuts/c1', json={'shotQcReview': verdict})
        assert unbound.status_code == 422  # A stale client must identify the take it rejects.


def test_static_camera_share_is_one_rule_for_build_and_draft():
    # JOURNAL 02.11: scene_draft passed a scene that «Собрать сцену» then refused for a static camera.
    from local_studio.scene_kit import static_camera_problems
    shots = [{"camera_motion": "static"}, {"camera_motion": "locked"}, {"camera_motion": "dolly_in"}]
    assert "2 из 3" in static_camera_problems(shots, {"max_static_share": 0.4})[0]
    assert static_camera_problems(shots, {"max_static_share": 0.7}) == []
    assert static_camera_problems(shots[:2], {"max_static_share": 0}) == []  # under three shots: not judged


def test_start_frame_review_sees_the_previous_accepted_frame(monkeypatch):
    # JOURNAL 31.10: scene_frames.review(previous=…) existed, but scene_runs never passed the shot before it.
    import asyncio
    from pathlib import Path
    from local_studio import scene_frames
    from local_studio.scene_runs import SceneRuns
    run = {"cuts": [{"startFrame": {"asset_id": "f1"}, "frameAcceptance": {"asset_id": "f1"}, "prompt": "VEGA on the sofa"},
                    {"startFrame": {"asset_id": "f2"}}]}
    assert SceneRuns._previous_accepted(run, 1) is None
    assert SceneRuns._previous_accepted(run, 2) == {"asset_id": "f1", "text": "VEGA on the sofa"}
    run["cuts"][0]["frameAcceptance"] = {"asset_id": "old"}
    assert SceneRuns._previous_accepted(run, 2) is None
    seen = {}

    class Dummy:
        async def _pictures(self, ids, tmp):
            return [Path(f"/tmp/{i}.png") for i in ids]

    async def fake_review(frame, text, references, pictures, previous=None):
        seen.update(frame=frame, pictures=pictures, previous=previous)
        return {"accept": True, "problems": ""}

    monkeypatch.setattr(scene_frames, "review", fake_review)
    asyncio.run(SceneRuns._claude_review(Dummy(), "f2", "shot", ["hero"], [{"role": "Персонаж целиком"}],
                                         {"asset_id": "f1", "text": "VEGA on the sofa"}))
    assert seen["frame"] == Path("/tmp/f2.png") and seen["pictures"] == [Path("/tmp/hero.png")]
    assert seen["previous"] == {"frame": Path("/tmp/f1.png"), "text": "VEGA on the sofa"}


def test_compose_returns_passport_breakdown_to_the_agent(tmp_path):
    # JOURNAL 01.11: «место без картинки» reached only «Собрать сцену»; compose now warns while the draft can change.
    runs = SceneRuns(lambda: tmp_path)
    kit = {"franchise": "adam-vega", "places": [{"name": "balcony", "aliases": [], "assets": []}], "props": [], "cast": []}

    async def fake_api(method, path, *args, **kwargs):
        return kit if path.endswith("/scene-kit") else {"roles": []}

    runs._api = fake_api
    api = FastAPI()
    api.include_router(make_router(runs))
    take = {"summary": "Vega waits.", "cast": [{"name": "VEGA", "does": "waits"}], "camera": "Wide shot."}
    reply = TestClient(api).post("/api/scene-runs/compose", json={
        "franchise_id": "adam-vega", "place": "The balcony of the flat.", "shots": [{"take": take, "duration": 12}]}).json()
    assert any("нет картинки" in w and "balcony" in w for w in reply["warnings"]), reply["warnings"]
