import json
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from local_studio import edl
from local_studio.edl import Edl, ShotsToEdl, TRANSITIONS, edl_from_shots, plan

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="нет ffmpeg")


def media(*durations, audio=True):
    return {f"job:j{i}": {"duration": d, "audio": {"codec_name": "aac"} if audio else None,
                          "video": {"width": 720, "height": 1280}} for i, d in enumerate(durations)}


def sheet(*clips, **kw):
    return Edl(clips=[{"job_id": f"j{i}", **c} for i, c in enumerate(clips)], **kw)


def test_every_schema_transition_has_an_edit_rule():
    assert edl.schema_transitions() == set(TRANSITIONS)


def test_cuts_add_up_and_xfades_overlap():
    result = plan(sheet({}, {"transition_in": "dissolve"}, {"transition_in": "whip_pan", "out": 2}), media(4, 4, 4))
    starts = [c["start"] for c in result["clips"]]
    assert starts == [0, 3.25, 7.0]
    assert result["total"] == 9.0
    assert result["clips"][2]["transition"] == {"type": "whip_pan", "effect": "hblur", "duration": 0.25}


def test_speed_changes_length_and_out_past_file_is_trimmed():
    result = plan(sheet({"speed": 2}, {"out": 9}), media(4, 5))
    assert [c["length"] for c in result["clips"]] == [2, 5]
    assert any("обрезан" in w for w in result["warnings"])


def test_transition_longer_than_half_a_clip_is_refused():
    with pytest.raises(ValueError, match="половины"):
        plan(sheet({"out": 1}, {"transition_in": "fade", "transition_duration": 0.8}), media(4, 4))


def test_sound_bridge_leads_audio_into_the_cut():
    result = plan(sheet({}, {"transition_in": "sound_bridge", "in": 1}), media(4, 4))
    assert result["clips"][1]["audio_lead"] == 0.6 and result["clips"][1]["start"] == 4
    short = plan(sheet({}, {"transition_in": "sound_bridge", "in": 0.2}), media(4, 4))
    assert short["clips"][1]["audio_lead"] == 0.2 and short["warnings"]


def test_unknown_transition_and_two_sources_are_rejected():
    with pytest.raises(ValueError):
        sheet({"transition_in": "star_wipe"})
    with pytest.raises(ValueError):
        Edl(clips=[{"job_id": "a", "asset_id": "b"}])


def test_target_runtime_and_shot_review():
    shots = [{"framing": "close", "location": "kitchen", "characters": ["ADAM"]},
             {"framing": "close", "location": "kitchen", "characters": ["ADAM"]}]
    result = plan(sheet({"shot": shots[0]}, {"shot": shots[1]}, target_seconds=20), media(4, 4))
    assert any("Хронометраж" in w for w in result["warnings"])
    assert "same_setup" in {f["rule"] for f in result["shot_review"]["findings"]}


def test_effects_freeze_frames_and_accents_shape_the_timeline():
    result = plan(sheet({"out": 3}, {"transition_in": "smash_cut", "transition_effect": "fadewhite", "freeze_end": 1,
                         "accents": [{"type": "shake", "duration": 0.4}, {"type": "flash", "offset": -0.1, "duration": 0.1}]}),
                  media(4, 4))
    assert result["clips"][1]["transition"] == {"type": "smash_cut", "effect": "fadewhite", "duration": 0.25}
    assert result["clips"][1]["length"] == 5  # 4 s of picture + 1 s held
    assert [(a["type"], a["start"]) for a in result["accents"]] == [("shake", 2.75), ("flash", 2.65)]
    with pytest.raises(ValueError, match="вид перехода"):
        sheet({}, {"transition_effect": "spin"})


def test_auto_sound_design_lands_on_seams_and_freezes():
    result = plan(sheet({"out": 3}, {"transition_in": "smash_cut"}, {"transition_in": "whip_pan", "freeze_end": 1},
                        auto_sfx=True), media(4, 4, 4))
    seam2 = result["clips"][2]["start"] + result["clips"][2]["transition"]["duration"] / 2
    by = {(s["bank"], round(s["start"] + edl.sfx_bank.anchor(s["bank"]), 3)) for s in result["sfx"]}
    assert ("impact", 3.0) in by and ("riser_short", 3.0) in by  # the riser ends on the smash
    assert ("whoosh", round(seam2, 3)) in by
    frozen = result["clips"][2]["end"] - 1
    assert ("shutter", round(frozen, 3)) in by and ("hit", round(frozen, 3)) in by
    assert all(s["start"] >= 0 and s["start"] + s["seconds"] <= result["total"] + 1e-6 for s in result["sfx"])
    quiet = plan(sheet({"out": 3}, {"transition_in": "smash_cut"}), media(4, 4))
    assert quiet["sfx"] == []


def test_graphics_ride_on_their_clip_and_get_a_sound():
    result = plan(sheet({"out": 3}, {"transition_in": "dissolve", "freeze_end": 1.5},
                        graphics=[{"template": "NameCard", "props": {"name": "ADAM"}, "clip": 2, "anchor": "freeze"},
                                  {"template": "KineticTitle", "props": {"text": "GO"}, "offset": 0.5, "duration": 1}],
                        auto_sfx=True), media(4, 4))
    card, title = result["graphics"]
    assert card["start"] == round(result["clips"][1]["end"] - 1.5, 4)
    assert title == {"template": "KineticTitle", "props": {"text": "GO"}, "start": 0.5, "seconds": 1}
    assert any(s["bank"] == "hit" and s["reason"] == "графика KineticTitle" for s in result["sfx"])
    with pytest.raises(ValueError, match="нет стоп-кадра"):
        plan(sheet({}, graphics=[{"template": "NameCard", "anchor": "freeze"}]), media(4))
    with pytest.raises(ValueError, match="шаблона"):
        sheet({}, graphics=[{"template": "Fireworks"}])


def test_colour_controls_looks_and_matching():
    from local_studio import grade
    assert grade.primaries(None) == [] and grade.primaries(grade.Grade()) == []
    assert grade.primaries(grade.Grade(exposure=0.5, temperature=5000, contrast=1.1)) == [
        "exposure=exposure=0.500", "colortemperature=temperature=5000", "eq=contrast=1.100:saturation=1.000"]
    assert len(grade.look_graph("v", "o", "teal_orange", 0.6)) == 3
    assert grade.look_graph("v", "o", "noir", 1)[0].startswith("[v]monochrome")
    rng = np.random.default_rng(1)
    reference = rng.uniform(0.2, 0.7, (3, 400))
    warm = np.clip(reference * np.array([[1.15], [1.0], [0.8]]) + np.array([[0.05], [0], [0]]), 0, 1)
    lut = grade.match_filter(warm.tolist(), reference.tolist(), 1.0)
    fixed = []
    for channel, expr in enumerate(lut.split("'")[1::2]):
        a, b = map(float, expr[len("clip(val*"):].split(",")[0].replace("+", " +").replace("-", " -").split())
        fixed.append(np.clip(warm[channel] * 255 * a + b, 0, 255).mean() / 255)
    assert np.allclose(fixed, reference.mean(axis=1), atol=0.02)
    with pytest.raises(ValueError, match="лука"):
        sheet({}, look="sepia_dream")
    with pytest.raises(ValueError, match="Эталон"):
        sheet({}, match_color=True, match_reference=3)


def test_bank_sound_is_placed_by_its_anchor():
    result = plan(sheet({}, sfx=[{"bank": "riser", "at": 3.0}]), media(4))
    assert result["sfx"][0]["start"] == round(3.0 - edl.sfx_bank.anchor("riser"), 4) == 0.6
    with pytest.raises(ValueError, match="Нет звука"):
        plan(sheet({}, sfx=[{"bank": "lasers", "at": 1}]), media(4))


def test_sheet_from_shots_takes_seam_transitions():
    draft = edl_from_shots(ShotsToEdl(shots=[
        {"job_id": "a", "title": "Кухня", "duration": 4, "transition_out": "whip_pan"},
        {"job_id": "b", "duration": 3},
        {"job_id": "c", "duration": 3, "transition_in": "dissolve"}], music_asset_id="m"))
    assert [c["transition_in"] for c in draft["clips"]] == ["cut", "whip_pan", "dissolve"]
    assert draft["clips"][0]["out"] == 4 and draft["music"] == {"asset_id": "m"}
    Edl(**draft)  # the draft is a valid sheet


# --- real assembly -------------------------------------------------------

def clip(path: Path, seconds: float, colour: str, audio: bool = True, size="720x1280"):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
           f"color=c={colour}:s={size}:r=24:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)], check=True)


class FakeStore:
    def __init__(self, root: Path):
        self.root = root
        self.results_dir = root / "results"
        self.assets_dir = root / "assets"
        self.results_dir.mkdir()
        self.assets_dir.mkdir()
        self.jobs, self.assets = {}, {}

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def get_asset(self, asset_id):
        return self.assets.get(asset_id)

    def load_draft(self):
        return {}


@needs_ffmpeg
def test_render_route_assembles_checked_mp4(tmp_path):
    store = FakeStore(tmp_path)
    for name, colour, audio in (("j0", "red", True), ("j1", "blue", False), ("j2", "green", True)):
        path = store.results_dir / f"{name}.mp4"
        clip(path, 3, colour, audio)
        store.jobs[name] = {"state": "completed", "output_path": str(path)}
    music = store.assets_dir / "music.m4a"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=20",
                    "-c:a", "aac", str(music)], check=True)
    store.assets["m"] = {"path": str(music)}
    app = FastAPI()
    app.include_router(edl.make_router(store))
    with TestClient(app) as client:
        body = {"clips": [{"job_id": "j0", "transition_in": "fade"},
                          {"job_id": "j1", "transition_in": "whip_pan"},
                          {"job_id": "j2", "transition_in": "sound_bridge", "in": 1}],
                "music": {"asset_id": "m"}, "sfx": [{"asset_id": "m", "at": 2, "duration": 0.5}], "fade_out": 0.5}
        planned = client.post("/api/edl/plan", json=body).json()
        assert planned["total"] == 7.75 and planned["fade_in"] == 1.0
        started = client.post("/api/edl/renders", json=body)
        assert started.status_code == 202, started.text
        render_id = started.json()["render_id"]
        for _ in range(120):
            state = client.get(f"/api/edl/renders/{render_id}").json()
            if state["state"] != "running":
                break
            time.sleep(0.5)
        assert state["state"] == "completed", state
        assert abs(state["duration"] - 7.75) < 0.25
        assert client.get(f"/api/edl/renders/{render_id}/file").status_code == 200
        saved = json.loads((tmp_path / "edl_renders" / f"{render_id}.json").read_text())
        assert saved["edl"]["clips"][2]["in"] == 1
        assert set(saved["snapshot"]["sources"]) >= {"job:j0", "job:j1", "job:j2", "asset:m"}
        assert state["input_fingerprint_sha256"] == saved["snapshot"]["input_fingerprint_sha256"]
        assert state["output_sha256"] == edl.file_sha256(tmp_path / "edl_renders" / f"{render_id}.mp4")
        assert saved["command"][-1].endswith(f"{render_id}.mp4")
        assert state["loudness"]["target_lufs"] == -14
        loud = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", str(tmp_path / "edl_renders" / f"{render_id}.mp4"),
                               "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
        integrated = float(loud.rsplit("I:", 1)[1].split("LUFS")[0])
        assert abs(integrated + 14) < 1.5


@needs_ffmpeg
@pytest.mark.skipif(not edl.motion.ready(), reason="моушен-графика не установлена (tools/motion)")
def test_name_card_renders_with_alpha(tmp_path):
    path = edl.motion.render("NameCard", {"name": "ADAM", "line": "Test"}, 360, 640, 24, 1.0, tmp_path)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt,nb_frames", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout
    assert info.startswith("yuva") and info.strip().endswith(",24")
    assert edl.motion.render("NameCard", {"name": "ADAM", "line": "Test"}, 360, 640, 24, 1.0, tmp_path) == path  # cached


@needs_ffmpeg
def test_finished_cut_with_bank_sfx_accents_and_freeze(tmp_path):
    store = FakeStore(tmp_path)
    for name, colour in (("j0", "red"), ("j1", "blue")):
        path = store.results_dir / f"{name}.mp4"
        clip(path, 3, colour)
        store.jobs[name] = {"state": "completed", "output_path": str(path)}
    app = FastAPI()
    app.include_router(edl.make_router(store))
    with TestClient(app) as client:
        kit = client.get("/api/edl/toolkit").json()
        assert "zoomin" in kit["effects"] and {s["name"] for s in kit["sfx_bank"]} >= {"whoosh", "impact", "riser"}
        body = {"clips": [{"job_id": "j0"}, {"job_id": "j1", "transition_in": "smash_cut", "freeze_end": 0.5,
                                              "accents": [{"type": "shake"}, {"type": "glitch", "offset": 1}]}],
                "auto_sfx": True, "grain": 0.3, "vignette": True, "look": "teal_orange", "match_color": True,
                "grade": {"exposure": 0.2}}
        started = client.post("/api/edl/renders", json=body).json()
        for _ in range(120):
            state = client.get(f"/api/edl/renders/{started['render_id']}").json()
            if state["state"] != "running":
                break
            time.sleep(0.5)
        assert state["state"] == "completed", state
        assert abs(state["duration"] - 6.5) < 0.25
        assert (tmp_path / "sfx_bank" / f"v{edl.sfx_bank.VERSION}" / "impact.wav").is_file()
        assert [q["clip"] for q in state["color_qc"]] == [1, 2]


@needs_ffmpeg
def test_plan_route_reports_missing_shot(tmp_path):
    app = FastAPI()
    app.include_router(edl.make_router(FakeStore(tmp_path)))
    response = TestClient(app).post("/api/edl/plan", json={"clips": [{"job_id": "nope"}]})
    assert response.status_code == 422 and "не готов" in response.json()["detail"]


@needs_ffmpeg
def test_export_writes_premiere_and_resolve_timelines(tmp_path):
    import xml.etree.ElementTree as ET
    store = FakeStore(tmp_path)
    for name, colour in (("j0", "red"), ("j1", "blue")):
        path = store.results_dir / f"{name}.mp4"
        clip(path, 3, colour)
        store.jobs[name] = {"state": "completed", "output_path": str(path)}
    app = FastAPI()
    app.include_router(edl.make_router(store))
    with TestClient(app) as client:
        body = {"title": "Test", "clips": [{"job_id": "j0"}, {"job_id": "j1", "transition_in": "dissolve", "freeze_end": 1}],
                "auto_sfx": True, "look": "teal_orange"}
        done = client.post("/api/edl/exports", json=body)
        assert done.status_code == 200, done.text
        result = done.json()
        folder = Path(result["folder"])
        premiere = ET.parse(folder / "STUDIO.xml").getroot()
        v1 = premiere.find("sequence/media/video/track")
        assert [item.findtext("name") for item in v1.findall("clipitem")] == ["Шот 1", "Шот 2", "Шот 2 · стоп-кадр"]
        assert v1.find("transitionitem/effect/name").text == "Cross Dissolve"
        assert premiere.find("sequence/duration").text == str(round((3 + 3 + 1 - 0.75) * 24))
        resolve = ET.parse(folder / "STUDIO.fcpxml").getroot()
        spine = resolve.find("library/event/project/sequence/spine")
        assert [child.tag for child in spine] == ["asset-clip", "transition", "asset-clip", "video"]
        from urllib.parse import unquote
        missing = [rep.get("src") for rep in resolve.iter("media-rep") if not Path(unquote(rep.get("src").removeprefix("file://"))).is_file()]
        assert not missing, missing
        cube = (folder / "look.cube").read_text().splitlines()
        assert cube[1] == "LUT_3D_SIZE 33" and len(cube) == 2 + 33 ** 3
        assert client.get(result["zip"]).status_code == 200
        assert client.post("/api/edl/exports/0123/resolve").status_code == 404




@needs_ffmpeg
def test_draft_preview_with_every_accent_and_a_lut_look(tmp_path):
    store = FakeStore(tmp_path)
    for name, colour in (("j0", "red"), ("j1", "blue")):
        path = store.results_dir / f"{name}.mp4"
        clip(path, 3, colour)
        store.jobs[name] = {"state": "completed", "output_path": str(path)}
    app = FastAPI()
    app.include_router(edl.make_router(store))
    accents = [{"type": kind, "offset": 0.25 * i, "duration": 0.2} for i, kind in enumerate(sorted(edl.ACCENTS))]
    assert set(edl.ACCENT_NAMES) == edl.ACCENTS
    with TestClient(app) as client:
        body = {"clips": [{"job_id": "j0"}, {"job_id": "j1", "transition_in": "dissolve", "accents": accents[:6]}],
                "look": "lut_teal_supreme", "look_intensity": 0.6}
        body["clips"][0]["accents"] = accents[6:]
        started = client.post("/api/edl/renders?draft=1", json=body)
        assert started.status_code == 202, started.text
        assert started.json()["plan"]["total"] == 5.25
        render_id = started.json()["render_id"]
        for _ in range(120):
            state = client.get(f"/api/edl/renders/{render_id}").json()
            if state["state"] != "running":
                break
            time.sleep(0.5)
        assert state["state"] == "completed" and state["draft"] and state["loudness"] is None, state
        size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                               "-of", "csv=p=0", str(tmp_path / "edl_renders" / f"{render_id}.mp4")],
                              capture_output=True, text=True).stdout.strip()
        assert size == "360,640"   # clip() makes 720x1280; the draft is half
        assert client.delete(f"/api/edl/renders/{render_id}").json()["cancelled"] is False
