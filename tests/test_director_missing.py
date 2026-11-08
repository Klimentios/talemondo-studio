"""A GPU deployed with --qwen disabled: nothing unloads the H3 weights for a Director that is not there."""
import asyncio
import importlib
import urllib.error
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app import director
from app.director import DIRECTOR_MISSING


class NoUnload:
    async def release_models_if_idle(self):
        raise AssertionError("H3 weights must stay loaded when Qwen is not installed")


def test_director_installed_follows_the_port(monkeypatch):
    monkeypatch.delenv("H3_QWEN_AUTOMANAGE", raising=False)
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        assert director.director_installed() is False
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 404, "x", {}, None)):
        assert director.director_installed() is True
    monkeypatch.setenv("H3_QWEN_AUTOMANAGE", "1")  # guard mode starts the stopped service itself
    with patch("urllib.request.urlopen", side_effect=AssertionError("no probe in guard mode")):
        assert director.director_installed() is True


@pytest.mark.parametrize("module, path", [
    ("app.scenarist", "/api/scenarist"),
    ("app.scene_edits", "/api/scene-board-edits"),
    ("app.scene_planner", "/api/scene-plans"),
])
def test_qwen_routes_answer_503_without_unloading(monkeypatch, module, path):
    mod = importlib.import_module(module)
    monkeypatch.setattr(mod, "director_installed", lambda: False)
    route = next(item for item in mod.make_router(NoUnload()).routes if item.path == path)
    payload = route.dependant.body_params[0].type_.model_construct()
    with pytest.raises(HTTPException) as caught:
        asyncio.run(route.endpoint(payload))
    assert caught.value.status_code == 503 and caught.value.detail == DIRECTOR_MISSING
