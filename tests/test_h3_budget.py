"""H3 packed-sequence budget (app/h3_budget.py) against ComfyUI's own frame and canvas rules."""
from app.h3_budget import adapt_canvas, align_frame_count, job_budget, reference_image_size, video_latent_t


def test_frame_grid_and_canvas_match_comfyui():
    assert align_frame_count(240) == 243 and video_latent_t(243) == 72
    assert align_frame_count(120) == 124 and video_latent_t(124) == 37
    assert adapt_canvas(1080, 1920) == (768, 1344)  # a 1080p source is encoded at 768 short edge
    assert reference_image_size(1024, 1536, "max", 672, 1216) == (1024, 1536)  # max never upscales
    assert reference_image_size(1024, 1536, "match", 672, 1216) == (736, 1120)


def test_source_video_dominates_a_swap():
    swap = job_budget(672, 1216, 10, images=[(1024, 1536)] * 2, videos=[(1080, 1920, 10)], prompt_chars=3500)
    assert swap["video_rows"] > swap["target_rows"] > swap["picture_rows"]
    small_source = job_budget(672, 1216, 10, images=[(1024, 1536)] * 2, videos=[(672, 1216, 10)], prompt_chars=3500)
    assert 0.85 * swap["total_rows"] < small_source["total_rows"] < 0.95 * swap["total_rows"]  # only ~11% fewer rows
    photos_only = job_budget(672, 1216, 10, images=[(1024, 1536)] * 3, prompt_chars=3500)
    assert photos_only["attention_vs_target"] < 1.5
