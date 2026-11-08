"""A/B swap options need the user's request (store.require_ab_request)."""
import json

import pytest

from test_swap_mask import _replacement


@pytest.mark.parametrize("settings", [{"swap_lora": "v1.5"}, {"style_adapter": "character_swap_v15"}])
def test_v15_without_the_users_words_is_refused(tmp_path, settings):
    with pytest.raises(ValueError, match="A/B-проба"):
        _replacement(tmp_path, settings)


def test_v15_with_the_users_words_is_queued_and_keeps_them(tmp_path):
    job = _replacement(tmp_path, {"swap_lora": "v1.5", "ab_user_request": "  Да,   сравни v1.5  "})
    settings = json.loads(job["settings_json"])
    assert settings["style_adapter"] == "character_swap_v15"
    assert settings["ab_user_request"] == "Да, сравни v1.5"


def test_accepted_route_needs_nothing_and_drops_a_stray_request(tmp_path):
    job = _replacement(tmp_path, {"ab_user_request": "лишнее поле"})
    settings = json.loads(job["settings_json"])
    assert settings["style_adapter"] == "character_swap" and "ab_user_request" not in settings


def test_queue_holds_while_finished_takes_wait_for_review(tmp_path, monkeypatch):
    from local_studio import review_gate
    from local_studio.store import StudioStore
    store = StudioStore(tmp_path)
    monkeypatch.setattr(review_gate, "review_hold", lambda _store: {"reason": "3 без проверки"})
    assert store.queue_paused() is True
    monkeypatch.setattr(review_gate, "review_hold", lambda _store: None)
    assert store.queue_paused() is False
    store.set_queue_paused(True)
    assert store.queue_paused() is True
