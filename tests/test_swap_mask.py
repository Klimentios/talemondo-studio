"""Masked hero swap: latent mask math, frame fitting, contract and graph wiring (A/B route, 04.11.2026)."""
import importlib.util
from pathlib import Path

import pytest
torch = pytest.importorskip("torch")  # GPU-only; the Mac check venv has no torch

from app.contract import GenerationRequest
from app.graph import build_prompt
from app.h3graph.nodes import Node
from app.swap_mask import SWAP_MASK_NODES, apply_swap_mask, normalize_swap_mask

_spec = importlib.util.spec_from_file_location(
    "latent_mask", Path(__file__).parents[1] / "app/comfy_nodes/h3_swap_mask/latent_mask.py")
latent_mask = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(latent_mask)


def test_latent_frames_follow_the_h3_17k_plus_5_grid():
    assert latent_mask.pixel_frames(37) == 124 and latent_mask.pixel_frames(72) == 243
    assert latent_mask.frame_spans(3) == [(0, 1), (1, 5), (5, 9)]


def test_mask_regenerates_only_the_performer_cells():
    mask = torch.zeros(124, 128, 64)
    mask[10:20, 40:72, 8:24] = 1.0  # frames 10–19, a box in the middle-left
    grid = latent_mask.latent_noise_mask(mask, 37, 8, 4)
    assert grid.shape == (1, 1, 37, 8, 4)
    assert grid[0, 0, 0].sum() == 0  # frame 0 is outside the performer's frames
    hot = grid[0, 0, 3]  # latent 3 covers frames 9–12
    assert hot.sum() > 0 and hot[0].sum() == 0 and hot[-1].sum() == 0
    assert torch.equal(hot[::2], hot[1::2])  # 2x2 DiT cells are whole


def test_frames_fit_canvas_and_exact_length():
    clip = torch.rand(100, 1920, 1080, 3)
    fitted = latent_mask.fit_frames(clip, 672, 1216, 124)
    assert fitted.shape == (124, 1216, 672, 3)
    assert torch.equal(fitted[-1], fitted[99])  # the short source repeats its last frame


def test_contract_rejects_bad_boxes_and_duplicates():
    spec = normalize_swap_mask({"performers": [{"name": "VEGA", "box": [0.1, 0.4, 0.3, 0.5]}]})
    assert spec == {"performers": [{"name": "VEGA", "box": [0.1, 0.4, 0.3, 0.5]}], "grow": 24, "feather": 0}
    assert normalize_swap_mask(None) is None
    for bad in ({"performers": []}, {"performers": [{"name": "A", "box": [0.8, 0, 0.5, 0.5]}]},
                {"performers": [{"name": "A", "box": [0, 0, .2, .2]}, {"name": "a", "box": [.5, .5, .2, .2]}]},
                {"performers": [{"name": "A", "box": [0, 0, .2, .2]}], "grow": 500}):
        with pytest.raises(ValueError):
            normalize_swap_mask(bad)


def test_graph_feeds_the_sampler_through_the_edit_mask():
    request = GenerationRequest("<Picture 1> replaces the man in <Video 1>.", 5, "9:16", "custom",
                                megapixels=0.8, steps=24)
    graph = build_prompt(request, ["vega.png"], ["source.mp4"], style_adapter="character_swap")
    latent_before = graph[Node.SAMPLE]["inputs"]["latent_image"]
    spec = normalize_swap_mask({"performers": [{"name": "VEGA", "box": [0.1, 0.4, 0.3, 0.5]}]})
    graph = apply_swap_mask(graph, spec, request.width, request.height, request.length)
    edit = graph[graph[Node.SAMPLE]["inputs"]["latent_image"][0]]
    assert edit["class_type"] == "H3SourceEditMask" and edit["inputs"]["latent"] == latent_before
    assert {graph[k]["class_type"] for k in ("601", "602", "603", "604")} == SWAP_MASK_NODES
    assert graph["601"]["inputs"]["length"] == request.length and graph["602"]["inputs"]["characters_json"] == ["601", 1]
    assert graph["603"]["inputs"]["mode"] == "replace"
    for item in graph.values():
        for value in item["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                assert value[0] in graph


def _replacement(store_dir, settings, style_adapters=frozenset({"character_swap", "character_swap_v15"})):
    from local_studio.store import StudioStore
    store = StudioStore(Path(store_dir))
    ids = []
    for name, kind, mime in (("a.png", "image", "image/png"), ("m.mp4", "video", "video/mp4")):
        path = store.assets_dir / name
        path.write_bytes(b"fixture" * 300)
        ids.append(store.add_asset(kind=kind, role="Персонаж целиком" if kind == "image" else "Исходное видео",
                                   original_name=name, mime=mime, size=2100, sha256=name, path=path)["asset_id"])
    return store.create_job(
        prompt="ANNA <Picture 1> takes the place of the woman on the left in <Video 1>.", asset_ids=ids,
        settings={"mode": "replace", **settings},
        reference_roles={ids[0]: "Персонаж целиком", ids[1]: "Исходное видео"},
        reference_owners={ids[0]: "ANNA"},
        reference_performers={ids[1]: {"pairs": [{"owner": "ANNA", "performer": "the woman on the left"}]}},
        style_adapters=set(style_adapters))


def test_studio_keeps_the_mask_for_a_mapped_replacement(tmp_path):
    import json
    mask = {"performers": [{"name": "ANNA", "box": [0.05, 0.3, 0.35, 0.6]}], "grow": 16}
    job = _replacement(tmp_path, {"swap_mask": mask, "swap_lora": "v1.5", "ab_user_request": "Да, сравни маску"})
    settings = json.loads(job["settings_json"])
    assert settings["swap_mask"]["performers"][0]["name"] == "ANNA" and settings["swap_mask"]["grow"] == 16
    assert settings["style_adapter"] == "character_swap_v15"


def test_studio_refuses_a_mask_for_an_unmapped_hero(tmp_path):
    with pytest.raises(ValueError, match="нет пары"):
        _replacement(tmp_path, {"swap_mask": {"performers": [{"name": "ADAM", "box": [0, 0, .3, .3]}]}})


def test_auto_swap_turns_first_frame_boxes_into_a_mask():
    from local_studio.video_swap_auto import AutoInput, describe_prompt, mask_from_boxes
    heroes = [{"name": "ADAM", "performer": "the man on the left"}, {"name": "VEGA", "performer": "the cat at the table"}]
    mask = mask_from_boxes(heroes, [{"hero": "vega", "box": [0.5, 0.5, 0.3, 0.3]},
                                    {"hero": "ADAM", "box": [0.05, 0.1, 0.4, 0.85]}], "часть 1/2")
    assert [p["name"] for p in mask["performers"]] == ["ADAM", "VEGA"] and mask["grow"] == 24
    with pytest.raises(ValueError, match="ADAM"):
        mask_from_boxes(heroes, [{"hero": "VEGA", "box": [0.5, 0.5, 0.3, 0.3]}], "часть 1/2")
    text = describe_prompt(heroes, [(0.0, 5.0)], [(1, 2.5)], "часть 1/2", ["a.jpg"], "adam_vega", "first.jpg")
    assert "First frame (0.00 s): file first.jpg" in text and '"boxes"' in text
    assert "First frame" not in describe_prompt(heroes, [(0.0, 5.0)], [(1, 2.5)], "часть 1/2", ["a.jpg"])
    assert AutoInput(asset_id="a", franchise_id="f", pairs=[{"hero": "VEGA", "performer": "the cat"}]).mask is False


def test_boxes_move_into_the_centre_crop_of_a_vertical_job():
    # 16:9 source 1920x1080 into a 9:16 job: the crop keeps the middle 0.316 of the width.
    crop = latent_mask.crop_box(672, 1216, 1920, 1080)
    assert crop[1] == 0.0 and crop[3] == 1.0 and round(crop[2], 3) == 0.311
    centre = latent_mask.remap_box([0.45, 0.2, 0.1, 0.6], crop)
    assert 0.3 < centre[0] < 0.4 and round(centre[2], 2) == round(0.1 / crop[2], 2)
    with pytest.raises(ValueError, match="вне кадра"):
        latent_mask.remap_box([0.0, 0.2, 0.1, 0.6], crop)  # far left: cut away by the crop
    assert latent_mask.remap_box([0.1, 0.1, 0.5, 0.5], latent_mask.crop_box(672, 1216, 1344, 2432)) == \
        pytest.approx([0.1, 0.1, 0.5, 0.5], abs=1e-3)


def test_studio_refuses_a_mask_where_the_gpu_would_ignore_it(tmp_path):
    mask = {"performers": [{"name": "ANNA", "box": [0.05, 0.3, 0.35, 0.6]}]}
    with pytest.raises(ValueError, match="турбо"):
        _replacement(tmp_path, {"swap_mask": mask, "ab_turbo": "dmd8"})


def test_swap_lora_and_explicit_adapter_must_agree(tmp_path):
    with pytest.raises(ValueError, match="разные"):
        _replacement(tmp_path, {"style_adapter": "character_swap", "swap_lora": "v1.5"})


def test_seed_hunt_refuses_a_size_the_upscaler_cannot_halve(tmp_path):
    import json
    from local_studio.store import StudioStore
    job = _replacement(tmp_path, {})
    store = StudioStore(tmp_path)
    settings = json.loads(job["settings_json"])
    assert (settings["width"], settings["height"]) == (672, 1216)
    with pytest.raises(ValueError, match="64"):
        store.add_seed_hunt_jobs(job, [{"seed": 1, "seed_hunt": {"role": "preview", "hunt_id": "h", "index": 0}}])
