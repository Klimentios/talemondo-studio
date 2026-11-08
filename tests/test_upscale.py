from __future__ import annotations

from pathlib import Path

import pytest

from app.upscale_graph import MODEL, VAE, build_upscale_graph, required_nodes
from local_studio.store import StudioStore
from local_studio.upscale import UpscaleQueue


def test_upscale_graph_keeps_source_audio_and_uses_fp16_seedvr2():
    graph = build_upscale_graph("source.mp4", "job-1", 24.0)
    assert {node["class_type"] for node in graph.values()} == required_nodes()
    assert graph["7"]["inputs"]["unet_name"] == MODEL
    assert graph["5"]["inputs"]["vae_name"] == VAE
    assert graph["3"]["inputs"]["resize_type.multiplier"] == 2.0
    assert "multiplier" not in graph["3"]["inputs"]
    assert graph["12"]["inputs"]["audio"] == ["2", 1]
    assert graph["12"]["inputs"]["fps"] == 24.0
    assert graph["13"]["inputs"]["video"] == ["12", 0]


def test_upscale_queue_preserves_source_and_deduplicates_active(tmp_path: Path):
    store = StudioStore(tmp_path)
    source = store.results_dir / "source.mp4"
    source.write_bytes(b"untouched original")
    with store.connect() as db:
        db.execute("""INSERT INTO jobs(job_id,state,original_prompt,compiled_prompt,settings_json,
                     manifest_json,mapping_json,output_path) VALUES ('source','completed','','','{}','{}','{}',?)""",
                   (str(source),))
    queue = UpscaleQueue(store, "http://127.0.0.1:18189")
    first = queue.create("source")
    assert first["state"] == "queued_local"
    assert queue.create("source")["upscale_id"] == first["upscale_id"]
    assert source.read_bytes() == b"untouched original"
    queue.update(first["upscale_id"], "failed", error="test")
    assert queue.create("source")["upscale_id"] != first["upscale_id"]


def test_upscale_graph_rejects_invalid_geometry():
    with pytest.raises(ValueError):
        build_upscale_graph("source.mp4", "id", 0)


def test_upscale_mode_graph_is_unchanged_and_new_modes_add_only_rife():
    base = build_upscale_graph("src.mp4", "job", 24.0)
    assert build_upscale_graph("src.mp4", "job", 24.0, mode="upscale") == base
    assert "20" not in base and base["12"]["inputs"]["fps"] == 24.0
    both = build_upscale_graph("src.mp4", "job", 24.0, mode="upscale_smooth")
    assert both["20"]["class_type"] == "RIFE VFI" and both["20"]["inputs"]["frames"] == ["11", 0]
    assert both["20"]["inputs"]["multiplier"] == 2 and both["20"]["inputs"]["fast_mode"] is False
    assert both["12"]["inputs"]["images"] == ["20", 0] and both["12"]["inputs"]["fps"] == 48.0
    assert {k: v for k, v in both.items() if k not in {"12", "20"}} == {k: v for k, v in base.items() if k != "12"}
    smooth = build_upscale_graph("src.mp4", "job", 24.0, mode="smooth")
    assert {n["class_type"] for n in smooth.values()} == required_nodes("smooth")
    assert smooth["12"]["inputs"]["audio"] == ["2", 1] and smooth["12"]["inputs"]["fps"] == 48.0


def test_unknown_mode_and_too_high_source_fps_are_rejected():
    import pytest
    with pytest.raises(ValueError):
        build_upscale_graph("src.mp4", "job", 24.0, mode="sharpen")
    with pytest.raises(ValueError):
        build_upscale_graph("src.mp4", "job", 120.0, mode="smooth")
