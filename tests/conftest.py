"""Test isolation: importing local_studio.main opens the Studio store at H3_MAC_DATA_DIR
(default: the user's real ~/Library/Application Support/H3 Studio). Point it at a throwaway
directory before any test module imports it, and never talk to a real GPU."""
import os
import tempfile

_DATA = tempfile.mkdtemp(prefix="h3-studio-tests-")
os.environ["H3_MAC_DATA_DIR"] = _DATA
os.environ.pop("H3_GPU_API_URL", None)

import pytest

# Modules that free the H3 weights for the GPU Qwen Director only when it is installed (app.director.director_installed).
_DIRECTOR_USERS = ("app.main", "app.scenarist", "app.scene_planner", "app.scene_edits")


@pytest.fixture(autouse=True)
def _director_installed(monkeypatch):
    """Tests stub LocalQwenDirector, so they run as on a server with it; tests/test_director_missing.py covers --qwen disabled."""
    import importlib
    for name in _DIRECTOR_USERS:
        monkeypatch.setattr(importlib.import_module(name), "director_installed", lambda: True)


@pytest.fixture(autouse=True)
def _demo_franchises(monkeypatch):
    """A fresh Studio starts empty; the franchise tests expect four demo franchises in a new store."""
    import local_studio.franchises as franchises
    monkeypatch.setattr(franchises, "DEFAULT_NAMES", ("ADAM & VEGA", "VEGA & ORION", "SAM & LEO", "MAX & ZOE"))
