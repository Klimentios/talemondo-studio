"""A model runtime counts as installed only when its Python really imports torch."""
import sys
from pathlib import Path

from app import audio_models


def test_python_imports_detects_missing_module(tmp_path):
    assert audio_models._python_imports(Path(sys.executable), "json") is True
    assert audio_models._python_imports(Path(sys.executable), "no_such_module_h3_xyz") is False
    assert audio_models._python_imports(tmp_path / "missing-python", "json") is False


def test_python_imports_is_cached(monkeypatch):
    calls = []
    real = audio_models.subprocess.run

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(audio_models.subprocess, "run", counting)
    audio_models._IMPORT_CHECKS.clear()
    audio_models._python_imports(Path(sys.executable), "os")
    audio_models._python_imports(Path(sys.executable), "os")
    assert len(calls) == 1
