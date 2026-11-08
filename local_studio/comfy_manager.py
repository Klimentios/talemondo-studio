"""ComfyUI nodes, models and the user's manifest: what the GPU has, what Studio should add, one apply button.

The manifest (<Studio data>/comfy/manifest.json) is the only list a user edits: node packs pinned to a git
commit (+ exact pip pins) and model files pinned to a SHA-256. scripts/install_comfy_manifest.py installs it
on the GPU (dry run by default); scripts/h3_comfy_manifest_apply.sh runs that from the Mac and is the
«Применить на GPU» runbook of the server workbench. Nothing here downloads to the Mac.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

REPO_URL = re.compile(r"https://(?:github\.com|gitlab\.com|huggingface\.co|codeberg\.org)/[\w.-]+/[\w.-]+?(?:\.git)?")
COMMIT = re.compile(r"[0-9a-f]{40}")
PIP_PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?==[A-Za-z0-9.+!_-]+")
FOLDER = re.compile(r"[a-z0-9_]{2,40}")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,180}\.(?:safetensors|ckpt|pt|pth|bin|gguf|onnx|sft|yaml|json)")
FILE_URL = re.compile(r"https://[A-Za-z0-9.-]+/\S+")
SHA256 = re.compile(r"[0-9a-f]{64}")
DIR_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}")
# Shipped by ComfyUI itself, not node packs.
CORE_MODULES = ("nodes", "comfy_extras", "comfy_api_nodes", "comfy.")


class NodePackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(max_length=300)
    commit: str
    name: str | None = Field(default=None, max_length=80)
    pip: list[str] = Field(default=[], max_length=40)
    note: str = Field(default="", max_length=300)

    @field_validator("repo")
    @classmethod
    def _repo(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not REPO_URL.fullmatch(value):
            raise ValueError("Нужна https-ссылка на git-репозиторий (GitHub, GitLab, Hugging Face, Codeberg)")
        return value

    @field_validator("commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not COMMIT.fullmatch(value.strip().lower()):
            raise ValueError("Нужен полный хеш коммита (40 символов): узлы ставятся только закреплённой версией")
        return value.strip().lower()

    @field_validator("pip")
    @classmethod
    def _pip(cls, value: list[str]) -> list[str]:
        bad = [item for item in value if not PIP_PIN.fullmatch(item.strip())]
        if bad:
            raise ValueError(f"pip-пакеты только с точной версией (пакет==1.2.3): {', '.join(bad)}")
        return [item.strip() for item in value]


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder: str
    filename: str
    url: str = Field(max_length=1000)
    sha256: str
    note: str = Field(default="", max_length=300)

    @field_validator("folder")
    @classmethod
    def _folder(cls, value: str) -> str:
        if not FOLDER.fullmatch(value):
            raise ValueError("Папка моделей ComfyUI: латиница в нижнем регистре (checkpoints, loras, vae…)")
        return value

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        value = value.strip()
        if not FILENAME.fullmatch(value) or ".." in value:
            raise ValueError("Имя файла модели без папок, с расширением .safetensors/.ckpt/.pt/.pth/.bin/.gguf/.onnx")
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not FILE_URL.fullmatch(value.strip()):
            raise ValueError("Нужна прямая https-ссылка на файл")
        return value.strip()

    @field_validator("sha256")
    @classmethod
    def _sha(cls, value: str) -> str:
        if not SHA256.fullmatch(value.strip().lower()):
            raise ValueError("Нужен SHA-256 файла (64 символа): без него вес не проверить")
        return value.strip().lower()


def pack_dir(repo: str, name: str | None = None) -> str:
    """custom_nodes/<dir>: the repo name, as ComfyUI-Manager and a plain git clone would name it."""
    value = name or repo.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    if not DIR_NAME.fullmatch(value):
        raise ValueError(f"Недопустимое имя папки узлов: {value}")
    return value


class ComfyManifest:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / "comfy" / "manifest.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def read(self) -> dict:
        if not self.path.is_file():
            return {"version": 1, "revision": 0, "nodes": [], "models": [], "updated_at": None}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> dict:
        data["revision"] += 1
        data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)
        return data

    def add_node(self, item: NodePackIn) -> dict:
        directory = pack_dir(item.repo, item.name)
        with self._lock:
            data = self.read()
            if any(node["dir"] == directory for node in data["nodes"]):
                raise PermissionError(f"Пакет узлов {directory} уже в манифесте: удалите старую запись")
            data["nodes"].append({"id": "n_" + uuid.uuid4().hex[:8], "dir": directory, "repo": item.repo,
                                  "commit": item.commit, "pip": item.pip, "note": item.note.strip()})
            return self._write(data)

    def add_model(self, item: ModelIn) -> dict:
        with self._lock:
            data = self.read()
            if any((model["folder"], model["filename"]) == (item.folder, item.filename) for model in data["models"]):
                raise PermissionError(f"{item.folder}/{item.filename} уже в манифесте")
            data["models"].append({"id": "m_" + uuid.uuid4().hex[:8], "folder": item.folder, "filename": item.filename,
                                   "url": item.url, "sha256": item.sha256, "note": item.note.strip()})
            return self._write(data)

    def remove(self, kind: str, item_id: str) -> dict:
        key = {"nodes": "nodes", "models": "models"}.get(kind)
        if not key:
            raise LookupError("Нет такого раздела манифеста")
        with self._lock:
            data = self.read()
            kept = [item for item in data[key] if item["id"] != item_id]
            if len(kept) == len(data[key]):
                raise LookupError("Запись не найдена")
            data[key] = kept
            return self._write(data)


class Inventory:
    """What the running ComfyUI has: node packs from /object_info, model files from /models (60 s cache)."""

    def __init__(self, comfy_url: str, schema=None) -> None:
        self.comfy_url = comfy_url
        self.schema = schema  # comfy_schema.ObjectInfoCache shared with the graph editors
        self._cache: tuple[float, dict] | None = None

    async def get(self, fresh: bool = False) -> dict:
        if not self.comfy_url:
            return {"reachable": False, "error": "ComfyUI не настроен", "packs": [], "models": {}, "classes": 0}
        if not fresh and self._cache and time.monotonic() - self._cache[0] < 60:
            return self._cache[1]
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if self.schema is not None:
                    from .comfy_schema import ComfyUnavailable
                    try:
                        info = await self.schema.get(fresh)
                    except ComfyUnavailable as exc:
                        raise httpx.TransportError(str(exc)) from exc
                else:
                    info = (await client.get(f"{self.comfy_url}/object_info")).raise_for_status().json()
                folders = (await client.get(f"{self.comfy_url}/models")).raise_for_status().json()
                lists = await asyncio.gather(*(client.get(f"{self.comfy_url}/models/{folder}") for folder in folders),
                                             return_exceptions=True)
        except (httpx.HTTPError, ValueError) as exc:
            return {"reachable": False, "error": f"ComfyUI недоступен: {type(exc).__name__}", "packs": [],
                    "models": {}, "classes": 0}
        packs: dict[str, dict] = {}
        for name, node in info.items():
            module = str(node.get("python_module") or "nodes")
            core = module.startswith(CORE_MODULES)
            key = "ComfyUI" if core else module.removeprefix("custom_nodes.").split(".", 1)[0]
            pack = packs.setdefault(key, {"name": key, "core": core, "classes": []})
            pack["classes"].append(name)
        models = {}
        for folder, response in zip(folders, lists):
            if isinstance(response, httpx.Response) and response.status_code == 200:
                models[folder] = sorted(response.json())
        result = {"reachable": True, "classes": len(info), "models": models,
                  "packs": sorted(({**pack, "classes": sorted(pack["classes"])} for pack in packs.values()),
                                  key=lambda pack: (not pack["core"], pack["name"].lower()))}
        self._cache = (time.monotonic(), result)
        return result


def manifest_status(manifest: dict, inventory: dict) -> dict:
    """Each manifest entry: on the GPU or still waiting for «Применить на GPU»."""
    if not inventory.get("reachable"):
        state = lambda present: "unknown"  # noqa: E731
    else:
        state = lambda present: "installed" if present else "pending"  # noqa: E731
    packs = {pack["name"].lower() for pack in inventory.get("packs", [])}
    nodes = [{**node, "state": state(node["dir"].lower() in packs)} for node in manifest["nodes"]]
    models = [{**model, "state": state(model["filename"] in inventory.get("models", {}).get(model["folder"], []))}
              for model in manifest["models"]]
    return {**manifest, "nodes": nodes, "models": models,
            "pending": sum(item["state"] == "pending" for item in nodes + models)}


def make_router(ctx):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()
    manifest, inventory = ctx.comfy_manifest, ctx.comfy_inventory

    def found(call, *args):
        try:
            return call(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/comfy/info")
    async def info():
        """For the embedded editor and the setup page: where ComfyUI is and whether the bridge is current."""
        result = {"url": ctx.comfy_url, "reachable": False, "bridge": None}
        if not ctx.comfy_url:
            return result
        try:
            async with httpx.AsyncClient(timeout=6) as client:
                result["reachable"] = (await client.get(f"{ctx.comfy_url}/queue")).status_code == 200
                bridge = await client.get(f"{ctx.comfy_url}/h3/studio/workflows")
                result["bridge"] = "current" if bridge.status_code == 200 else (
                    "outdated" if bridge.status_code == 404 else f"HTTP {bridge.status_code}")
        except httpx.HTTPError:
            pass
        return result

    @router.get("/api/comfy/inventory")
    async def get_inventory(fresh: bool = False):
        return await inventory.get(fresh)

    @router.get("/api/comfy/manifest")
    async def get_manifest():
        return manifest_status(manifest.read(), await inventory.get())

    @router.post("/api/comfy/manifest/nodes", status_code=201)
    async def add_node(item: NodePackIn):
        return manifest_status(found(manifest.add_node, item), await inventory.get())

    @router.post("/api/comfy/manifest/models", status_code=201)
    async def add_model(item: ModelIn):
        return manifest_status(found(manifest.add_model, item), await inventory.get())

    @router.delete("/api/comfy/manifest/{kind}/{item_id}")
    async def remove(kind: str, item_id: str):
        return manifest_status(found(manifest.remove, kind, item_id), await inventory.get())

    @router.post("/api/comfy/manifest/apply", status_code=202)
    async def apply(request: Request):
        """«Применить на GPU»: the fixed runbook scripts/h3_comfy_manifest_apply.sh on the current server."""
        from .compute.manager import ComputeConflict
        from .compute.operator import _same_origin
        if not _same_origin(request.headers.get("host", ""), request.headers.get("origin")):
            raise HTTPException(403, "Требуется запрос со страницы Studio")
        if not manifest.read()["nodes"] and not manifest.read()["models"]:
            raise HTTPException(422, "Манифест пуст")
        compute = ctx.compute_manager
        current = compute.current_server()
        if not current.get("host"):
            raise HTTPException(409, "Нет текущего GPU-сервера: подключите его в «GPU и модели»")
        try:
            return await compute.start_operator_action("comfy_manifest", current["host"], int(current["port"]),
                                                       manifest=manifest.path)
        except ComputeConflict as exc:
            messages = {"studio_jobs_active": "В Studio есть активные задания. Дождись их завершения.",
                        "deployment_already_running": "Установка уже выполняется.",
                        "ssh_identity_missing": "SSH-ключ на Mac не найден.",
                        "deploy_script_missing": "Скрипт установки манифеста не найден."}
            raise HTTPException(409, messages.get(str(exc), "Действие сейчас недоступно.")) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return router
