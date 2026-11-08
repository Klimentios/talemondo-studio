"""«Первый запуск» (local_studio/setup_status.py): readiness checks and saved addresses."""
import asyncio
from pathlib import Path
from types import SimpleNamespace


def test_status_lists_fixes_and_settings_round_trip(tmp_path, monkeypatch):
    from local_studio import setup_status as module
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    ctx = SimpleNamespace(root=Path(__file__).resolve().parents[1], data_root=tmp_path, gpu_url="", comfy_url="",
                          compute_manager=SimpleNamespace(current_server=lambda: {"host": None, "port": None, "configured": False}))
    result = asyncio.run(module.status(ctx))
    checks = {check["key"]: check for check in result["checks"]}
    assert not result["ready"]
    assert not checks["gpu"]["ok"] and "адрес" in checks["gpu"]["fix"]
    assert not checks["agents"]["ok"] and "h3_studio_mcp.py" in result["agents"]["claude"]
    assert checks["data"]["fix"] == "" or not checks["data"]["ok"]
    saved = module.save_settings(tmp_path, module.SettingsIn(gpu_url="http://127.0.0.1:18190/", comfy_url=""))
    assert saved["gpu_url"] == "http://127.0.0.1:18190" and module.saved_settings(tmp_path)["gpu_url"] == saved["gpu_url"]
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        module.SettingsIn(gpu_url="http://127.0.0.1:18190/api; rm -rf")
