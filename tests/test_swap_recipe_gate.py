"""Pre-GPU swap gates from the 03–04.11.2026 rejects (local_studio/store.py)."""
import pytest

from local_studio.store import SWAP_NEGATIONS_MAX, SWAP_PROMPT_MAX, swap_recipe_gate
from local_studio.video_swap_auto import gpu_guard


def video(role, performer="the cat at the table"):
    return {"job_role": role, "job_performers": [{"owner": "VEGA", "performer": performer}]}


def test_too_many_bans_are_refused_before_gpu():
    images = [{"job_role": "Персонаж целиком", "job_owner": "VEGA"}]
    swap_recipe_gate("Keep it. " + "no hat. " * SWAP_NEGATIONS_MAX, images, [video("Исходное видео")])
    with pytest.raises(ValueError, match="запретов"):
        swap_recipe_gate("no hat. " * (SWAP_NEGATIONS_MAX + 1), images, [video("Исходное видео")])


def test_motion_route_swap_with_environment_photo_is_refused():
    # 2f4ac2b8 / 0b37ce5e: foreign room and the source's second cat.
    images = [{"job_role": "Персонаж целиком", "job_owner": "VEGA"}, {"job_role": "Окружение"}]
    with pytest.raises(ValueError, match="Исходное видео"):
        swap_recipe_gate("short", images, [video("Движение и камера")])
    swap_recipe_gate("short", images[:1], [video("Движение и камера")])  # plain motion transfer stays allowed
    swap_recipe_gate("short", images, [{"job_role": "Движение и камера", "job_performers": []}])


def test_source_video_swap_prompt_length_limit():
    images = [{"job_role": "Персонаж целиком", "job_owner": "VEGA"}]
    swap_recipe_gate("x" * 3682, images, [video("Исходное видео")])
    with pytest.raises(ValueError, match="знаков"):
        swap_recipe_gate("x" * (SWAP_PROMPT_MAX + 1), images, [video("Исходное видео")])


def test_gpu_guard_does_not_repeat_source_looks():
    guard = gpu_guard([{"name": "ADAM", "performer": "the man in a teal beanie"},
                       {"name": "VEGA", "performer": "the hairless cat"}])
    assert "beanie" not in guard and "hairless" not in guard
    assert "ADAM, VEGA" in guard
