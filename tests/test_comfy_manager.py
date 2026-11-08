"""«Узлы и модели» (local_studio/comfy_manager.py) and the server-side installer scripts/install_comfy_manifest.py."""
import asyncio
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

COMMIT = "d3cfe21625e5170126ce06fbfcfe1d88108688c3"
SHA = "a" * 64


def test_manifest_accepts_only_pinned_entries(tmp_path):
    from pydantic import ValidationError
    from local_studio.comfy_manager import ComfyManifest, ModelIn, NodePackIn

    with pytest.raises(ValidationError, match="коммита"):
        NodePackIn(repo="https://github.com/kijai/ComfyUI-KJNodes", commit="main")
    with pytest.raises(ValidationError, match="точной версией"):
        NodePackIn(repo="https://github.com/kijai/ComfyUI-KJNodes", commit=COMMIT, pip=["opencv-python"])
    with pytest.raises(ValidationError, match="git"):
        NodePackIn(repo="http://evil.example/x", commit=COMMIT)
    with pytest.raises(ValidationError, match="SHA-256"):
        ModelIn(folder="loras", filename="a.safetensors", url="https://huggingface.co/a/b/resolve/main/a.safetensors", sha256="x")
    with pytest.raises(ValidationError, match="без папок"):
        ModelIn(folder="loras", filename="../a.safetensors", url="https://h.co/a", sha256=SHA)

    manifest = ComfyManifest(tmp_path)
    data = manifest.add_node(NodePackIn(repo="https://github.com/kijai/ComfyUI-KJNodes.git", commit=COMMIT.upper(), pip=["kornia==0.7.1"]))
    assert data["nodes"][0]["dir"] == "ComfyUI-KJNodes" and data["nodes"][0]["commit"] == COMMIT
    with pytest.raises(PermissionError):
        manifest.add_node(NodePackIn(repo="https://github.com/other/ComfyUI-KJNodes", commit=COMMIT))
    data = manifest.add_model(ModelIn(folder="loras", filename="style.safetensors", url="https://h.co/style", sha256=SHA))
    assert data["revision"] == 2
    data = manifest.remove("models", data["models"][0]["id"])
    assert not data["models"] and json.loads(manifest.path.read_text())["revision"] == 3


def test_inventory_groups_packs_and_marks_manifest_state():
    from local_studio import comfy_manager as module

    def comfy(request):
        return {"/object_info": httpx.Response(200, json={
                    "KSampler": {"python_module": "nodes"},
                    "ColorMatch": {"python_module": "custom_nodes.ComfyUI-KJNodes"},
                    "GetImageSize+": {"python_module": "custom_nodes.ComfyUI-KJNodes.nodes"}}),
                "/models": httpx.Response(200, json=["loras", "vae"]),
                "/models/loras": httpx.Response(200, json=["style.safetensors"]),
                "/models/vae": httpx.Response(200, json=[])}[request.url.path]

    original = httpx.AsyncClient
    inventory = module.Inventory("http://comfy")
    module_client = module.httpx.AsyncClient
    module.httpx.AsyncClient = lambda **kw: original(transport=httpx.MockTransport(comfy), **kw)
    try:
        result = asyncio.run(inventory.get())
    finally:
        module.httpx.AsyncClient = module_client
    assert [pack["name"] for pack in result["packs"]] == ["ComfyUI", "ComfyUI-KJNodes"]
    assert result["packs"][1]["classes"] == ["ColorMatch", "GetImageSize+"]
    status = module.manifest_status({"nodes": [{"dir": "ComfyUI-KJNodes"}, {"dir": "Other"}],
                                     "models": [{"folder": "loras", "filename": "style.safetensors"}]}, result)
    assert [n["state"] for n in status["nodes"]] == ["installed", "pending"] and status["pending"] == 1
    assert module.manifest_status({"nodes": [{"dir": "x"}], "models": []}, {"reachable": False})["nodes"][0]["state"] == "unknown"


def test_apply_runs_fixed_runbook_on_current_server(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from local_studio import comfy_manager as module

    manifest = module.ComfyManifest(tmp_path)
    compute = SimpleNamespace(current_server=lambda: {"host": "gpu.example", "port": 2222, "configured": True},
                              start_operator_action=AsyncMock(return_value={"operation_id": "b" * 32, "state": "running"}))
    inventory = SimpleNamespace(get=AsyncMock(return_value={"reachable": False}))
    app = FastAPI()
    app.include_router(module.make_router(SimpleNamespace(comfy_manifest=manifest, comfy_inventory=inventory,
                                                          compute_manager=compute, comfy_url="")))
    client = TestClient(app, base_url="http://127.0.0.1:18187")
    page = {"Origin": "http://127.0.0.1:18187"}
    empty = client.post("/api/comfy/manifest/apply", headers=page)
    assert empty.status_code == 422 and "пуст" in empty.text
    client.post("/api/comfy/manifest/models", json={"folder": "loras", "filename": "a.safetensors", "url": "https://h.co/a", "sha256": SHA})
    refused = client.post("/api/comfy/manifest/apply")
    assert refused.status_code == 403, refused.text  # not from a Studio page
    response = client.post("/api/comfy/manifest/apply", headers=page)
    assert response.status_code == 202, response.text
    compute.start_operator_action.assert_awaited_once_with("comfy_manifest", "gpu.example", 2222, manifest=manifest.path)


def test_compute_manager_refuses_comfy_manifest_without_file(tmp_path, monkeypatch):
    from local_studio.compute.manager import ComputeManager
    manager = ComputeManager(tmp_path, busy=lambda: False, repo_root=Path(__file__).resolve().parents[1], gpu_url="")
    monkeypatch.setattr(manager, "current_server", lambda: {"host": "gpu.example", "port": 22, "configured": True})
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519").write_text("k")
    with pytest.raises(ValueError, match="Манифест"):
        asyncio.run(manager.start_operator_action("comfy_manifest", "gpu.example", 22, manifest=tmp_path / "none.json"))


def test_server_installer_plans_and_refuses_foreign_files(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "install_comfy_manifest.py"
    comfy = tmp_path / "ComfyUI"
    (comfy / "custom_nodes").mkdir(parents=True)
    weight = comfy / "models" / "loras" / "have.safetensors"
    weight.parent.mkdir(parents=True)
    weight.write_bytes(b"weights")
    good = hashlib.sha256(b"weights").hexdigest()
    manifest = tmp_path / "manifest.json"

    def run(data):
        manifest.write_text(json.dumps(data))
        return subprocess.run([sys.executable, "-I", str(script), "--comfy", str(comfy), "--python", sys.executable,
                               "--manifest", str(manifest)], capture_output=True, text=True, timeout=60)

    node = {"id": "n_1", "dir": "ComfyUI-KJNodes", "repo": "https://github.com/kijai/ComfyUI-KJNodes", "commit": COMMIT, "pip": []}
    plan = run({"nodes": [node], "models": [
        {"id": "m_1", "folder": "loras", "filename": "have.safetensors", "url": "https://h.co/a", "sha256": good},
        {"id": "m_2", "folder": "loras", "filename": "new.safetensors", "url": "https://h.co/b", "sha256": SHA}]})
    assert plan.returncode == 0, plan.stdout
    assert "install" in plan.stdout and "ok" in plan.stdout and "download" in plan.stdout and "DRY RUN" in plan.stdout
    conflict = run({"nodes": [], "models": [{"id": "m_1", "folder": "loras", "filename": "have.safetensors", "url": "https://h.co/a", "sha256": SHA}]})
    assert conflict.returncode == 3 and "different SHA-256" in conflict.stdout
    bad = run({"nodes": [{**node, "commit": "main"}], "models": []})
    assert bad.returncode == 2
