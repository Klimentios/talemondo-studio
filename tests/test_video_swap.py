"""«Замена по видео»: the whole source becomes 4–10 s parts with the built-in swap settings."""
import hashlib
import shutil
import subprocess

import pytest

from local_studio import video_swap as vs
from local_studio.store import StudioStore


def covered(parts, duration):
    """The glued film: each part minus the frames its head loses to the part before."""
    return sum(p["duration"] - (p["trim_frames"] + p["tail_frames"]) / vs.FPS for p in parts), duration


def test_parts_start_on_the_source_cuts_and_tile_the_whole_source():
    parts = vs.split_parts(37.4, [6.2, 13.8, 21.1, 33.0])
    assert [p["keep"] for p in parts] == [[0.0, 6.2], [6.2, 13.8], [13.8, 21.1], [21.1, 31.1], [31.1, 37.4]]
    assert [(p["start"], p["duration"]) for p in parts] == [(0.0, 7), (6.2, 8), (13.8, 8), (21.1, 10), (30.4, 7)]
    assert all(vs.MIN_PART_S <= p["duration"] <= vs.MAX_PART_S for p in parts)
    assert [p["tail_frames"] for p in parts] == [19, 10, 17, 0, 0]  # each run-on past its cut; 21.1–31.1 has none to reach
    assert parts[-1]["end"] == 37.4 and parts[-1]["trim_frames"] == round(0.7 * vs.FPS)
    total, duration = covered(parts, 37.4)
    assert total == pytest.approx(duration, abs=1 / vs.FPS)
    assert parts[2]["source_cuts"] == [7.3]  # the 21.1 s cut is in part 3's run-on, cut off by the glue


def test_short_source_is_one_part_and_no_cuts_falls_back_to_10_s():
    assert [(p["start"], p["duration"], p["trim_frames"]) for p in vs.split_parts(9.0, [])] == [(0.0, 9, 0)]
    parts = vs.split_parts(31.0, [])
    assert [p["duration"] for p in parts] == [10, 10, 7, 4]
    assert covered(parts, 31.0)[0] == pytest.approx(31.0, abs=1 / vs.FPS)
    with pytest.raises(ValueError):
        vs.split_parts(3.0, [])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_plan_saves_every_part_with_the_recipe_and_pairs(tmp_path, monkeypatch):
    store = StudioStore(tmp_path)
    source = store.assets_dir / "source.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=red:s=180x320:r=24:d=9", "-f", "lavfi", "-i", "color=blue:s=180x320:r=24:d=9",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=18", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-map", "2:a", "-shortest", "-pix_fmt", "yuv420p", str(source)], check=True)
    raw = source.read_bytes()
    video = store.add_asset(kind="video", role="Исходное видео", original_name="source.mp4", mime="video/mp4",
                            size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=source)
    monkeypatch.setattr(vs, "hero_pictures", lambda *a: ({"ORION": {"picture": "orion-full", "voice": None,
                                                                    "name": "ORION"}}, []))
    plan = vs.plan(store, vs.SwapPlanInput(asset_id=video["asset_id"], franchise_id="am",
                                           pairs=[{"hero": "ORION", "performer": "the ginger cat by the red car"}]))
    assert plan["ready"] and plan["recipe_id"] == "default"
    assert plan["source"]["cuts"] and abs(plan["source"]["cuts"][0] - 9.0) < 0.1  # red → blue
    assert [(p["start"], p["duration"]) for p in plan["parts"]] == [(0.0, 9), (9.0, 9)]
    first = plan["parts"][0]
    assert first["label"] == "часть 1/2" and store.get_asset(first["segment_asset_id"])["kind"] == "video"
    form = first["form"]
    assert form["asset_ids"] == ["orion-full", first["segment_asset_id"]]
    assert form["reference_roles"] == {"orion-full": "Персонаж целиком", first["segment_asset_id"]: "Исходное видео"}
    assert form["reference_performers"][first["segment_asset_id"]] == {
        "pairs": [{"owner": "ORION", "performer": "the ginger cat by the red car"}], "keep_others": True}
    settings = form["settings"]
    assert settings["duration"] == 9 and settings["seed"] is None
    assert (settings["steps"], settings["cache_mode"], settings["ref_image_size"], settings["mode"]) == (24, "safe", "max", "replace")
    assert settings["use_video_audio"] is True and settings["aspect_ratio"] == "9:16"
    assert plan["glue_trim_frames"] == [0, 0]


def test_a_hero_without_full_body_picture_stops_the_plan(tmp_path, monkeypatch):
    store = StudioStore(tmp_path)
    path = store.assets_dir / "v.mp4"
    path.write_bytes(b"x")
    video = store.add_asset(kind="video", role="Исходное видео", original_name="v.mp4", mime="video/mp4",
                            size=1, sha256="x", path=path)
    monkeypatch.setattr(vs, "_media_duration_seconds", lambda p: 12.0)
    monkeypatch.setattr(vs, "hero_pictures", lambda *a: ({}, ["VEGA: в паспорте франшизы нет фото «Персонаж целиком»"]))
    plan = vs.plan(store, vs.SwapPlanInput(asset_id=video["asset_id"], franchise_id="am",
                                           pairs=[{"hero": "VEGA", "performer": "the cat on the left"}]))
    assert plan == {"ready": False, "problems": ["VEGA: в паспорте франшизы нет фото «Персонаж целиком»"]}
    with pytest.raises(ValueError):
        vs.plan(store, vs.SwapPlanInput(asset_id=video["asset_id"], franchise_id="am",
                                        pairs=[{"hero": "VEGA", "performer": "the cat"}, {"hero": "vega", "performer": "dog"}]))


def test_new_world_swap_rejects_gpu_route_before_making_segments(tmp_path):
    store = StudioStore(tmp_path)
    with pytest.raises(ValueError, match="Окружение"):
        vs.plan(store, vs.SwapPlanInput(asset_id="missing", franchise_id="ka", environment_asset_id="set",
                                        route="gpu", pairs=[{"hero": "ADAM", "performer": "the man on the left"}]))


def test_swap_rejects_three_performers_before_making_segments(tmp_path):
    store = StudioStore(tmp_path)
    with pytest.raises(ValueError, match="трёх и более"):
        vs.plan(store, vs.SwapPlanInput(asset_id="missing", franchise_id="ka", route="gpu",
                                        pairs=[{"hero": h, "performer": f"source actor {n}"}
                                               for n, h in enumerate(("ADAM", "VEGA", "ORION"))]))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_glue_cuts_heads_and_tails_to_the_source_length(tmp_path):
    from local_studio.storyboard import concat, probe
    clips = []
    for index, seconds in enumerate([6, 5]):
        clip = tmp_path / f"c{index}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"color=green:s=180x320:r=24:d={seconds}", "-f", "lavfi", "-i", f"sine=duration={seconds}",
                        "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
        clips.append(clip)
    out = tmp_path / "out.mp4"
    concat(clips, out, shutil.which("ffmpeg"), shutil.which("ffprobe"), trim_frames=[0, 12], tail_frames=[24, 0])
    assert probe(out, shutil.which("ffprobe"))["duration"] == pytest.approx(6 - 1 + 5 - 0.5, abs=0.1)


def test_shorter_max_part_keeps_parts_within_it_and_tiles_the_source():
    parts = vs.split_parts(29.67, [4.67, 8.0, 13.57, 17.2, 23.3, 27.47], 8)
    assert all(vs.MIN_PART_S <= p["duration"] <= 8 for p in parts)
    total, duration = covered(parts, 29.67)
    assert total == pytest.approx(duration, abs=1 / vs.FPS)
    assert vs.split_parts(37.4, [6.2, 13.8, 21.1, 33.0]) == vs.split_parts(37.4, [6.2, 13.8, 21.1, 33.0], vs.MAX_PART_S)


def test_api_plan_is_rejected_before_making_segments(tmp_path):
    store = StudioStore(tmp_path)
    with pytest.raises(ValueError, match="через MiniMax API не поддерживается"):
        vs.plan(store, vs.SwapPlanInput(asset_id="missing", franchise_id="ka", route="minimax_api",
                                        pairs=[{"hero": "ADAM", "performer": "the man on the left"}]))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_black_end_card_is_left_out_of_the_plan(tmp_path):
    clip = tmp_path / "outro.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:s=180x320:r=24:d=6",
                    "-f", "lavfi", "-i", "color=black:s=180x320:r=24:d=3", "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]",
                    "-map", "[v]", "-pix_fmt", "yuv420p", str(clip)], check=True)
    assert vs.end_card_start(clip, 9.0) == pytest.approx(6.0, abs=0.1)
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:s=180x320:r=24:d=6",
                    "-pix_fmt", "yuv420p", str(plain)], check=True)
    assert vs.end_card_start(plain, 6.0) is None


def test_last_part_never_opens_on_a_sliver_and_short_shots_are_flagged():
    """Parts opening on a 0.33–0.68 s tail collapse every later cut (h3_take_check)."""
    parts = vs.split_parts(16.4, [8.0])  # the last 8.4 s would reach back to 7.4: a 0.6 s sliver before the 8.0 cut
    last = parts[-1]
    assert (last["start"], last["duration"]) == (6.4, 10)
    assert not any(0 < c - last["start"] < vs.MIN_SHOT_S for c in [8.0])
    assert last["end"] == 16.4 and covered(parts, 16.4)[0] == pytest.approx(16.4, abs=1 / vs.FPS)
    tight = vs.split_parts(9.0, [3.0, 3.6, 7.0])[0]
    assert [0.6] == [round(b - a, 2) for a, b in tight["short_shots"]]
