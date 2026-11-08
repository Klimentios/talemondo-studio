"""«Свои воркфлоу»: any ComfyUI graph in API format becomes a Studio button, a REST endpoint and an agent tool.

Import a graph (file, paste, or «⇪ Воркфлоу в Studio» in the ComfyUI editor via h3-studio-bridge), Studio finds
its inputs — pictures, videos, sounds, prompts, seed, numbers — and the user picks which ones the form shows.
A run uploads the chosen library files to ComfyUI, fills the inputs, checks the graph against /object_info and
queues it directly, with the same receipt rules as Motion Director (side_jobs.SideQueueRules): one POST per
receipt, an ambiguous answer is reconciled by the `studio_workflow_job` marker, never resent.

This lane is separate from the H3 production path: it never edits the accepted recipe or the Studio graph.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import mimetypes
import random
import re
import threading
import uuid
from pathlib import Path, PurePosixPath

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .side_jobs import ACTIVE, SideQueueRules
from scripts.h3_graph_validate import validate

WORKFLOW_ID = re.compile(r"wf_[0-9a-f]{12}")
MAX_GRAPH_NODES = 2000
MAX_OUTPUT_FILES = 32
MAX_RUNS = 4

# Loader node → the input that names its file. Anything else with a literal file input stays a fixed value.
MEDIA_LOADERS = {
    "LoadImage": ("image", "image"), "LoadImageMask": ("image", "image"), "LoadImageOutput": ("image", "image"),
    "LoadVideo": ("file", "video"), "VHS_LoadVideo": ("video", "video"), "VHS_LoadVideoFFmpeg": ("video", "video"),
    "LoadAudio": ("audio", "audio"), "VHS_LoadAudioUpload": ("audio", "audio"),
}
TEXT_INPUTS = {"text", "prompt", "positive", "negative", "text_g", "text_l", "string", "caption",
               "positive_prompt", "negative_prompt", "user_prompt", "system_prompt"}
SEED_INPUTS = {"seed", "noise_seed"}
NUMBER_INPUTS = {"steps", "cfg", "denoise", "width", "height", "length", "batch_size", "frame_rate", "fps",
                 "duration", "num_frames", "frames", "shift", "guidance", "megapixels", "scale_by"}
OUTPUT_KEYS = ("images", "gifs", "videos", "video", "audio", "animated")
SUFFIXES = {"image": {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"},
            "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
            "audio": {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}}


def _is_link(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


def check_api_graph(graph) -> dict:
    """An API-format graph {node id: {class_type, inputs}}; the editor's own save format is refused with the way out."""
    if isinstance(graph, dict) and isinstance(graph.get("prompt"), dict):
        graph = graph["prompt"]
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), list) and "links" in graph:
        raise ValueError("Это файл редактора ComfyUI, а нужен формат API: в ComfyUI «Workflow → Export (API)» "
                         "или кнопка «⇪ Воркфлоу в Studio»")
    if not isinstance(graph, dict) or not graph:
        raise ValueError("Граф пустой или не в формате API ComfyUI")
    if len(graph) > MAX_GRAPH_NODES:
        raise ValueError(f"В графе больше {MAX_GRAPH_NODES} узлов")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs", {}), dict):
            raise ValueError(f"Узел {node_id}: нет class_type/inputs — граф не в формате API ComfyUI")
        for name, value in node.get("inputs", {}).items():
            if _is_link(value) and value[0] not in graph:
                raise ValueError(f"Узел {node_id}: вход {name} ссылается на отсутствующий узел {value[0]}")
    return graph


def _title(graph: dict, node_id: str) -> str:
    node = graph[node_id]
    return str((node.get("_meta") or {}).get("title") or node["class_type"])


def _negative_nodes(graph: dict) -> set[str]:
    """Text nodes wired (directly) into a sampler's negative input."""
    return {value[0] for node in graph.values() for name, value in node.get("inputs", {}).items()
            if name == "negative" and _is_link(value)}


def detect_params(graph: dict) -> list[dict]:
    """Every input a person would want to change, in node order; media, prompts and seed are shown by default."""
    params, negative = [], _negative_nodes(graph)

    def order(item):
        return (0, int(item[0])) if item[0].isdigit() else (1, item[0])

    for node_id, node in sorted(graph.items(), key=order):
        cls, inputs, title = node["class_type"], node.get("inputs", {}), _title(graph, node_id)
        media = MEDIA_LOADERS.get(cls)
        for name, value in inputs.items():
            if _is_link(value):
                continue
            param = None
            if media and name == media[0] and isinstance(value, str):
                param = {"kind": media[1], "exposed": True, "label": title}
            elif name in TEXT_INPUTS and isinstance(value, str):
                label = f"{title} · негатив" if node_id in negative else title
                param = {"kind": "text", "exposed": True, "label": label}
            elif name in SEED_INPUTS and isinstance(value, int) and not isinstance(value, bool):
                param = {"kind": "seed", "exposed": True, "label": f"{title} · seed"}
            elif name in NUMBER_INPUTS and isinstance(value, (int, float)) and not isinstance(value, bool):
                param = {"kind": "int" if isinstance(value, int) else "float", "exposed": False, "label": f"{title} · {name}"}
            if param:
                params.append({"key": f"{node_id}.{name}", "node": node_id, "input": name, "class_type": cls,
                               "default": value, **param})
    return params


def model_files(graph: dict) -> list[str]:
    from app.graph_edit import model_files as files
    return sorted(files(graph))


def apply_values(graph: dict, params: list[dict], values: dict) -> dict:
    """A copy of the graph with each exposed parameter set; values are already checked by check_values."""
    filled = copy.deepcopy(graph)
    by_key = {param["key"]: param for param in params}
    for key, value in values.items():
        param = by_key[key]
        filled[param["node"]]["inputs"][param["input"]] = value
    return filled


def check_values(params: list[dict], values: dict, store) -> dict:
    """Only exposed parameters may change; media are library asset ids of the right kind."""
    if not isinstance(values, dict):
        raise ValueError("values — объект {ключ параметра: значение}")
    exposed = {param["key"]: param for param in params if param["exposed"]}
    unknown = sorted(set(values) - set(exposed))
    if unknown:
        raise ValueError("Эти параметры не открыты в форме воркфлоу: " + ", ".join(unknown))
    checked = {}
    for key, value in values.items():
        param = exposed[key]
        kind = param["kind"]
        if value is None or value == "":
            continue  # keep the graph's own value (seed: a new random one, see job_values)
        if kind in {"image", "video", "audio"}:
            asset = store.get_asset(str(value))
            if not asset or asset["kind"] != kind:
                raise ValueError(f"{param['label']}: в библиотеке нет такого файла типа {kind}")
            checked[key] = {"asset_id": asset["asset_id"]}
        elif kind == "text":
            if not isinstance(value, str) or len(value) > 20000:
                raise ValueError(f"{param['label']}: текст до 20000 символов")
            checked[key] = value
        elif kind == "seed":
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**53 - 1:
                raise ValueError(f"{param['label']}: seed — целое от 0")
            checked[key] = value
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{param['label']}: нужно целое число")
            checked[key] = value
        elif kind == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{param['label']}: нужно число")
            checked[key] = float(value)
    return checked


def job_values(params: list[dict], checked: dict, index: int) -> dict:
    """Values of one run: an unset exposed seed is random; run i of a batch with a set seed uses seed + i."""
    values = dict(checked)
    for param in params:
        if param["exposed"] and param["kind"] == "seed":
            base = values.get(param["key"])
            values[param["key"]] = random.randint(0, 2**48) if base is None else base + index
    return values


def file_suffix(asset: dict) -> str:
    kind = asset["kind"]
    suffix = Path(asset.get("original_name") or "").suffix.lower()
    if suffix in SUFFIXES[kind]:
        return suffix
    guessed = mimetypes.guess_extension(asset.get("mime") or "") or ""
    if guessed in SUFFIXES[kind]:
        return guessed
    raise ValueError("Не удалось определить формат файла из библиотеки")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ParamEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    exposed: bool | None = None
    label: str | None = Field(default=None, max_length=120)


class WorkflowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    graph: dict
    params: list[ParamEdit] = []


class WorkflowPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    params: list[ParamEdit] = []


class GraphReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    graph: dict


class GraphEdit(BaseModel):
    """Operations of app/graph_ops.py on the workflow's graph; dry_run returns the result without saving."""
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    ops: list[dict] = Field(min_length=1, max_length=200)
    note: str = Field(default="", max_length=300)
    dry_run: bool = False


class Restore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class Fork(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict = {}
    count: int = Field(default=1, ge=1, le=MAX_RUNS)


def _edit_params(params: list[dict], edits: list[ParamEdit]) -> list[dict]:
    by_key = {param["key"]: param for param in params}
    for edit in edits:
        if edit.key not in by_key:
            raise ValueError(f"Нет параметра {edit.key}")
        if edit.exposed is not None:
            by_key[edit.key]["exposed"] = edit.exposed
        if edit.label:
            by_key[edit.key]["label"] = edit.label.strip()
    return params


class WorkflowLibrary:
    """One JSON file per workflow in <Studio data>/workflows; edits carry the revision they were based on."""

    def __init__(self, data_root: Path) -> None:
        self.folder = data_root / "workflows"
        self.folder.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, wid: str) -> Path:
        if not WORKFLOW_ID.fullmatch(wid or ""):
            raise LookupError("Воркфлоу не найден")
        return self.folder / f"{wid}.json"

    def _write(self, record: dict) -> dict:
        path = self._path(record["id"])
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return record

    def _versions(self, wid: str) -> Path:
        self._path(wid)
        return self.folder / "versions" / wid

    def _snapshot(self, record: dict) -> None:
        """The revision about to be replaced, kept as is: history is never rewritten, a restore is a new revision."""
        folder = self._versions(record["id"])
        folder.mkdir(parents=True, exist_ok=True)
        keep = {key: record.get(key) for key in ("id", "name", "revision", "source", "graph", "params", "note", "updated_at")}
        tmp = folder / f".r{record['revision']}.tmp"
        tmp.write_text(json.dumps(keep, ensure_ascii=False), encoding="utf-8")
        tmp.replace(folder / f"r{record['revision']}.json")

    def versions(self, wid: str) -> list[dict]:
        current = self.get(wid)
        items = [current]
        for path in self._versions(wid).glob("r*.json"):
            try:
                items.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        items.sort(key=lambda item: item["revision"], reverse=True)
        return [{"revision": item["revision"], "current": item["revision"] == current["revision"],
                 "note": item.get("note") or "", "source": item.get("source"), "updated_at": item.get("updated_at"),
                 "nodes": len(item["graph"])} for item in items]

    def version(self, wid: str, revision: int) -> dict:
        current = self.get(wid)
        if revision == current["revision"]:
            return current
        path = self._versions(wid) / f"r{int(revision)}.json"
        if not path.is_file():
            raise LookupError(f"Ревизии {revision} нет в истории")
        return json.loads(path.read_text(encoding="utf-8"))

    def get(self, wid: str) -> dict:
        path = self._path(wid)
        if not path.is_file():
            raise LookupError("Воркфлоу не найден")
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        items = []
        for path in self.folder.glob("wf_*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append(summary(record))
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def create(self, payload: WorkflowIn, check: dict | None, source: str = "import", origin: dict | None = None) -> dict:
        graph = check_api_graph(payload.graph)
        params = _edit_params(detect_params(graph), payload.params)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record = {"id": "wf_" + uuid.uuid4().hex[:12], "name": payload.name.strip(), "description": payload.description.strip(),
                  "revision": 1, "source": source, "graph": graph, "params": params, "models": model_files(graph),
                  "node_classes": sorted({node["class_type"] for node in graph.values()}),
                  "check": check, "created_at": now, "updated_at": now, "note": "импорт",
                  **({"origin": origin} if origin else {})}
        with self._lock:
            return self._write(record)

    def update(self, wid: str, patch: WorkflowPatch) -> dict:
        from datetime import datetime, timezone
        with self._lock:
            record = self.get(wid)
            if record["revision"] != patch.expected_revision:
                raise PermissionError("Воркфлоу изменён; перечитайте текущую ревизию")
            if patch.name is not None:
                record["name"] = patch.name.strip()
            if patch.description is not None:
                record["description"] = patch.description.strip()
            self._snapshot(record)
            record["params"] = _edit_params(record["params"], patch.params)
            record["note"] = "форма или название"
            record["revision"] += 1
            record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return self._write(record)

    def replace_graph(self, wid: str, graph: dict, check: dict | None, expected_revision: int | None,
                      source: str | None = None, note: str = "") -> dict:
        """New graph for the same workflow: the form keeps the choices made for inputs that still exist."""
        from datetime import datetime, timezone
        graph = check_api_graph(graph)
        with self._lock:
            record = self.get(wid)
            if expected_revision is not None and record["revision"] != expected_revision:
                raise PermissionError("Воркфлоу изменён; перечитайте текущую ревизию")
            self._snapshot(record)
            before = {param["key"]: param for param in record["params"]}
            params = detect_params(graph)
            for param in params:
                kept = before.get(param["key"])
                if kept and kept["class_type"] == param["class_type"]:
                    param["exposed"], param["label"] = kept["exposed"], kept["label"]
            record.update(graph=graph, params=params, models=model_files(graph), check=check,
                          node_classes=sorted({node["class_type"] for node in graph.values()}))
            if source:
                record["source"] = source
            record["note"] = note or ("граф из ComfyUI" if source == "comfyui" else "граф изменён")
            record["revision"] += 1
            record["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return self._write(record)

    def set_check(self, wid: str, check: dict) -> dict:
        with self._lock:
            record = self.get(wid)
            record["check"] = check
            return self._write(record)

    def delete(self, wid: str) -> None:
        with self._lock:
            path = self._path(wid)
            if not path.is_file():
                raise LookupError("Воркфлоу не найден")
            path.unlink()
            import shutil
            shutil.rmtree(self._versions(wid), ignore_errors=True)


def summary(record: dict) -> dict:
    exposed = [param for param in record["params"] if param["exposed"]]
    return {key: record.get(key) for key in ("id", "name", "description", "revision", "source", "models", "check",
                                             "created_at", "updated_at", "note", "origin")} | {
        "inputs": [{"key": p["key"], "kind": p["kind"], "label": p["label"]} for p in exposed],
        "nodes": len(record["graph"])}


def agent_view(record: dict) -> dict:
    """What an agent needs to run it: the form, not the whole graph."""
    form = [{"key": p["key"], "kind": p["kind"], "label": p["label"], "default": p["default"],
             "how": {"image": "asset_id of a library image", "video": "asset_id of a library video",
                     "audio": "asset_id of a library audio", "text": "string", "seed": "integer ≥ 0; omit for random",
                     "int": "integer", "float": "number"}[p["kind"]]}
            for p in record["params"] if p["exposed"]]
    return {**summary(record), "form": form,
            "hidden_params": [p["key"] for p in record["params"] if not p["exposed"]],
            "node_classes": record.get("node_classes", [])}


async def object_info(comfy_url: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(f"{comfy_url}/object_info")
        response.raise_for_status()
        return response.json()


def graph_check(graph: dict, info: dict) -> dict:
    """Missing node classes and wrong values; uploaded media inputs are checked again at run time."""
    problems = [p for p in validate(graph, info) if "класс узла не установлен" in p or not any(
        f" {name}=" in p for name in ("image", "video", "file", "audio"))]
    missing = sorted({graph[node]["class_type"] for node in graph if graph[node]["class_type"] not in info})
    return {"checked": True, "ok": not problems, "missing_nodes": missing, "problems": problems[:50]}


async def check_against_comfy(comfy_url: str, graph: dict) -> dict:
    if not comfy_url:
        return {"checked": False, "reason": "ComfyUI не настроен (H3_COMFY_URL)"}
    try:
        return graph_check(graph, await object_info(comfy_url))
    except (httpx.HTTPError, ValueError) as exc:
        return {"checked": False, "reason": f"ComfyUI недоступен: {type(exc).__name__}"}


class WorkflowQueue(SideQueueRules):
    TABLE = "workflow_jobs"
    ID = "job_id"
    NOUN = "задание воркфлоу"

    def __init__(self, store, comfy_url: str, library: WorkflowLibrary) -> None:
        self.store, self.comfy_url, self.library = store, comfy_url, library
        self._init_rules()
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workflow_jobs (
                job_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
                graph_json TEXT NOT NULL, params_json TEXT NOT NULL, values_json TEXT NOT NULL,
                manifest_json TEXT NOT NULL, state TEXT NOT NULL, remote_job_id TEXT, sent_graph_json TEXT,
                outputs_json TEXT, error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        self.results = store.results_dir / "workflows"

    def get(self, uid: str) -> dict | None:
        row = self._row(uid)
        return public_job(row) if row else None

    def list(self, workflow_id: str | None = None, limit: int = 50) -> list[dict]:
        query = "SELECT * FROM workflow_jobs" + (" WHERE workflow_id=?" if workflow_id else "") + \
                " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        with self.store.connect() as db:
            rows = db.execute(query, (*([workflow_id] if workflow_id else []), limit)).fetchall()
        return [public_job(dict(row)) for row in rows]

    def create(self, wid: str, run: RunIn) -> list[dict]:
        record = self.library.get(wid)
        checked = check_values(record["params"], run.values, self.store)
        manifest = {}
        for value in checked.values():
            if isinstance(value, dict):
                asset = self.store.get_asset(value["asset_id"])
                path = Path(asset["path"])
                if not path.is_file() or not path.resolve().is_relative_to(self.store.assets_dir.resolve()):
                    raise ValueError("Файл из библиотеки недоступен")
                digest = file_hash(path)
                if asset.get("sha256") and digest != asset["sha256"]:
                    raise ValueError("Файл библиотеки изменился после импорта")
                manifest[asset["asset_id"]] = {"kind": asset["kind"], "sha256": digest}
        jobs = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for index in range(run.count):
                uid = str(uuid.uuid4())
                db.execute("""INSERT INTO workflow_jobs(job_id,workflow_id,revision,name,graph_json,params_json,values_json,
                              manifest_json,state) VALUES (?,?,?,?,?,?,?,?,'queued_local')""",
                           (uid, wid, record["revision"], record["name"], json.dumps(record["graph"]),
                            json.dumps(record["params"]), json.dumps(job_values(record["params"], checked, index)),
                            json.dumps(manifest)))
                jobs.append(uid)
        return [self.get(uid) for uid in jobs]

    def update(self, uid: str, state: str, **fields) -> None:
        allowed = {"remote_job_id", "sent_graph_json", "outputs_json", "error"}
        if state not in ACTIVE | {"completed", "failed"} or fields.keys() - allowed:
            raise ValueError("Некорректное состояние задания")
        values = {"state": state, **fields}
        with self.store.connect() as db:
            db.execute(f"UPDATE workflow_jobs SET {','.join(key + '=?' for key in values)},updated_at=CURRENT_TIMESTAMP "
                       "WHERE job_id=? AND state NOT IN ('completed','failed','cancelled')", (*values.values(), uid))

    def claim(self, uid: str) -> bool:
        with self.store.connect() as db:
            return db.execute("UPDATE workflow_jobs SET state='uploading',updated_at=CURRENT_TIMESTAMP "
                              "WHERE job_id=? AND state='queued_local'", (uid,)).rowcount == 1

    async def _lookup(self, uid: str):
        async with httpx.AsyncClient(timeout=30) as client:
            history = await client.get(f"{self.comfy_url}/history")
            history.raise_for_status()
            queue = await client.get(f"{self.comfy_url}/queue")
            queue.raise_for_status()
        candidates = [entry.get("prompt") for entry in history.json().values() if isinstance(entry, dict)]
        data = queue.json()
        candidates += data.get("queue_running", []) + data.get("queue_pending", [])
        found = {item[1] for item in candidates if isinstance(item, list) and len(item) > 3
                 and isinstance(item[3], dict) and item[3].get("studio_workflow_job") == uid}
        if len(found) > 1:
            raise ValueError("Найдено несколько удалённых заданий для одной квитанции")
        return {"job_id": found.pop()} if found else None

    async def _upload(self, client, asset_id: str, entry: dict) -> str:
        """Top level of ComfyUI/input under the content hash, so the loader's own choice list includes it."""
        asset = self.store.get_asset(asset_id)
        path = Path(asset["path"]) if asset else None
        if not path or not path.is_file() or await asyncio.to_thread(file_hash, path) != entry["sha256"]:
            raise ValueError("Файл изменился после постановки задания")
        name = f"studio_{entry['sha256'][:32]}{file_suffix(asset)}"
        content = await asyncio.to_thread(path.read_bytes)
        response = await client.post(f"{self.comfy_url}/upload/image", data={"overwrite": "true", "type": "input"},
                                     files={"image": (name, content, asset.get("mime") or "application/octet-stream")})
        response.raise_for_status()
        result = response.json()
        if result.get("type", "input") != "input" or not result.get("name"):
            raise ValueError("ComfyUI не подтвердил загрузку файла")
        return str(PurePosixPath(result.get("subfolder") or "") / result["name"])

    async def run_once(self) -> bool:
        job = self.first_active()
        if not job or not self.comfy_url:
            return False
        uid, state = job["job_id"], job["state"]
        if state == "uploading":
            self.park(uid, "Восстановление после прерывания; сверка ComfyUI")
            return True
        if state == "queued_local":
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.get(f"{self.comfy_url}/queue")
                    response.raise_for_status()
                    queue = response.json()
                    if queue.get("queue_running") or queue.get("queue_pending"):
                        return False  # the GPU is someone else's right now
                    if not self.claim(uid):
                        return False
                    params = json.loads(job["params_json"])
                    values = json.loads(job["values_json"])
                    uploaded = {asset_id: await self._upload(client, asset_id, entry)
                                for asset_id, entry in json.loads(job["manifest_json"]).items()}
                    values = {key: uploaded[value["asset_id"]] if isinstance(value, dict) else value
                              for key, value in values.items()}
                    graph = apply_values(json.loads(job["graph_json"]), params, values)
                    for node in graph.values():
                        if isinstance(node.get("inputs", {}).get("filename_prefix"), str):
                            node["inputs"]["filename_prefix"] = f"studio_workflows/{uid[:8]}"
                    schema = await client.get(f"{self.comfy_url}/object_info")
                    schema.raise_for_status()
                    problems = validate(graph, schema.json())
                    if problems:
                        raise ValueError("Граф не прошёл проверку ComfyUI: " + "; ".join(problems)[:2000])
                    self.update(uid, "uploading", sent_graph_json=json.dumps(graph))
                    if self._row(uid)["state"] != "uploading":
                        return True  # cancelled meanwhile
                    try:
                        response = await client.post(f"{self.comfy_url}/prompt", json={
                            "prompt": graph, "client_id": uid, "extra_data": {"studio_workflow_job": uid}})
                        if response.status_code == 400:
                            self.update(uid, "failed", error="ComfyUI отклонил граф: " + response.text[:1500])
                            return True
                        response.raise_for_status()
                        remote = response.json().get("prompt_id")
                        if not isinstance(remote, str) or not remote:
                            raise ValueError("Пустая квитанция ComfyUI")
                    except Exception as exc:
                        self.park(uid, f"Ответ на отправку потерян; автоповтор отключён: {type(exc).__name__}")
                        return True
                    self.update(uid, "queued_gpu", remote_job_id=remote, error="")
            except ValueError as exc:
                self.update(uid, "failed", error=str(exc))
            except httpx.HTTPError:
                self.update(uid, "queued_local", error="ComfyUI недоступен при подготовке")
                return False
            return True
        if state in {"queued_gpu", "running", "downloading"}:
            return await self._poll(job)
        return False

    async def _poll(self, job: dict) -> bool:
        uid, remote = job["job_id"], job["remote_job_id"]
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(f"{self.comfy_url}/history/{remote}")
            response.raise_for_status()
            result = response.json().get(remote)
            if not result:
                response = await client.get(f"{self.comfy_url}/queue")
                response.raise_for_status()
                remote_queue = response.json()
                running = remote_queue.get("queue_running", [])
                items = running + remote_queue.get("queue_pending", [])
                if not any(isinstance(item, list) and len(item) > 1 and item[1] == remote for item in items):
                    self.park(uid, "Задание отсутствует в очереди и истории ComfyUI; требуется сверка")
                else:
                    self.update(uid, "running" if any(item[1] == remote for item in running) else "queued_gpu")
                return False
            status = result.get("status", {})
            if status.get("status_str") == "error":
                self.update(uid, "failed", error="Ошибка исполнения в ComfyUI: " + str(status.get("messages", ""))[:1500])
                return True
            if not status.get("completed"):
                return False
            files = [(node, item) for node, output in (result.get("outputs") or {}).items() if isinstance(output, dict)
                     for key in OUTPUT_KEYS for item in (output.get(key) or []) if isinstance(item, dict)
                     and isinstance(item.get("filename"), str) and item.get("type") in {"output", "temp"}]
            if not files:
                self.update(uid, "failed", error="Граф выполнен, но не вернул ни одного файла (нет узла Save/Preview)")
                return True
            self.update(uid, "downloading")
            folder = self.results / uid
            folder.mkdir(parents=True, exist_ok=True)
            saved = []
            try:
                for index, (node, item) in enumerate(files[:MAX_OUTPUT_FILES]):
                    name = PurePosixPath(item["filename"]).name
                    target = folder / f"{index:02d}-{name}"
                    temporary = target.with_name("." + target.name + ".part")
                    params = {key: item[key] for key in ("filename", "subfolder", "type") if key in item}
                    async with client.stream("GET", f"{self.comfy_url}/view", params=params) as media:
                        media.raise_for_status()
                        with temporary.open("wb") as handle:
                            async for block in media.aiter_bytes():
                                handle.write(block)
                    temporary.replace(target)
                    kind = next((k for k, suffixes in SUFFIXES.items() if target.suffix.lower() in suffixes), "file")
                    saved.append({"index": index, "node": node, "name": name, "kind": kind,
                                  "size": target.stat().st_size, "sha256": await asyncio.to_thread(file_hash, target),
                                  "path": str(target)})
            except Exception as exc:
                for leftover in folder.glob(".*.part"):
                    leftover.unlink(missing_ok=True)
                error = self.download_failed(uid, exc)
                if error:
                    self.update(uid, "failed", error=error)
                return False
            note = f"Сохранены первые {MAX_OUTPUT_FILES} из {len(files)} файлов" if len(files) > MAX_OUTPUT_FILES else ""
            self.update(uid, "completed", outputs_json=json.dumps(saved), error=note)
            return True


def public_job(row: dict) -> dict:
    outputs = json.loads(row.get("outputs_json") or "[]")
    return {key: row.get(key) for key in ("job_id", "workflow_id", "revision", "name", "state", "remote_job_id",
                                          "error", "created_at", "updated_at")} | {
        "values": json.loads(row.get("values_json") or "{}"),
        "outputs": [{k: v for k, v in item.items() if k != "path"} |
                    {"url": f"/api/workflow-jobs/{row['job_id']}/files/{item['index']}"} for item in outputs]}


def make_router(ctx):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import FileResponse

    router = APIRouter()
    library, queue = ctx.workflow_library, ctx.workflow_queue

    def found(call, *args):
        try:
            return call(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/api/workflows")
    async def list_workflows():
        return {"workflows": library.list(), "comfy_configured": bool(ctx.comfy_url)}

    @router.post("/api/workflows/detect")
    async def detect(body: dict):
        graph = found(check_api_graph, body.get("graph"))
        return {"params": detect_params(graph), "models": model_files(graph), "nodes": len(graph),
                "check": await check_against_comfy(ctx.comfy_url, graph)}

    @router.post("/api/workflows", status_code=201)
    async def create(payload: WorkflowIn):
        graph = found(check_api_graph, payload.graph)
        check = await check_against_comfy(ctx.comfy_url, graph)
        return agent_view(found(library.create, payload, check))

    @router.get("/api/workflows/comfy-inbox")
    async def inbox():
        """Graphs sent from the ComfyUI editor with «⇪ Воркфлоу в Studio» (h3-studio-bridge)."""
        if not ctx.comfy_url:
            raise HTTPException(503, "ComfyUI не настроен")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f"{ctx.comfy_url}/h3/studio/workflows")
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"ComfyUI недоступен: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise HTTPException(404, "На GPU старый мост Studio без «⇪ Воркфлоу в Studio»: обновите расширения "
                                     "(scripts/install_h3_studio_extensions.py --apply и перезапуск ComfyUI)")
        response.raise_for_status()
        data = response.json()
        # Copies Studio itself put there for «Открыть в ComfyUI» are not edits: hide them, so the inbox
        # lists only what came back with «⇪ Воркфлоу в Studio».
        return {**data, "items": [item for item in data.get("items", []) if item.get("origin") != "studio"]}

    @router.post("/api/workflows/comfy-inbox/{item_id}/import", status_code=201)
    async def import_from_inbox(item_id: str, body: dict | None = None):
        if not re.fullmatch(r"[0-9a-f-]{36}", item_id):
            raise HTTPException(422, "Некорректный ID")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{ctx.comfy_url}/h3/studio/workflows/{item_id}")
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"ComfyUI недоступен: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise HTTPException(404, "Воркфлоу не найден в ComfyUI")
        response.raise_for_status()
        sent = response.json()
        target = (body or {}).get("target")
        if target:
            graph = found(check_api_graph, sent.get("prompt") or {})
            check = await check_against_comfy(ctx.comfy_url, graph)
            return agent_view(found(library.replace_graph, target, graph, check, None, "comfyui"))
        name = str((body or {}).get("name") or sent.get("name") or "Воркфлоу из ComfyUI")[:120]
        payload = found(lambda: WorkflowIn(name=name, graph=sent.get("prompt") or {}))
        graph = found(check_api_graph, payload.graph)
        return agent_view(found(library.create, payload, await check_against_comfy(ctx.comfy_url, graph), "comfyui"))

    @router.get("/api/workflows/{wid}")
    async def get(wid: str, graph: bool = False):
        record = found(library.get, wid)
        return {**agent_view(record), "params": record["params"], **({"graph": record["graph"]} if graph else {})}

    @router.put("/api/workflows/{wid}")
    async def update(wid: str, patch: WorkflowPatch):
        return agent_view(found(library.update, wid, patch))

    @router.put("/api/workflows/{wid}/graph")
    async def replace_graph(wid: str, payload: GraphReplace):
        """Graph edited in Studio's node view: same workflow, next revision, form choices kept."""
        graph = found(check_api_graph, payload.graph)
        check = await check_against_comfy(ctx.comfy_url, graph)
        return agent_view(found(library.replace_graph, wid, graph, check, payload.expected_revision))

    async def schema() -> dict:
        from .comfy_schema import ComfyUnavailable
        try:
            return await ctx.comfy_schema.get()
        except ComfyUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post("/api/workflows/{wid}/edit")
    async def edit_graph(wid: str, payload: GraphEdit):
        """Add, remove, replace or rewire nodes, LoRAs and values (app/graph_ops.py), checked against the live
        /object_info. Saved as the next revision (the previous one stays in the history) unless dry_run."""
        from app.graph_ops import apply_ops
        record = found(library.get, wid)
        if record["revision"] != payload.expected_revision:
            raise HTTPException(409, "Воркфлоу изменён; перечитайте текущую ревизию")
        info = await schema()
        result = found(apply_ops, record["graph"], payload.ops, info)
        if result["graph"] == record["graph"]:
            raise HTTPException(422, "Граф не изменился")
        body = {key: result[key] for key in ("changes", "notes", "warnings", "problems", "new_problems", "refs")}
        if payload.dry_run:
            return {**body, "saved": False, "graph": result["graph"]}
        if result["new_problems"]:
            raise HTTPException(422, "Правка ломает граф: " + "; ".join(result["new_problems"][:8]))
        check = graph_check(result["graph"], info)
        note = payload.note.strip() or "; ".join(result["notes"])[:300] or f"{len(result['changes']['changed'])} знач."
        saved = found(library.replace_graph, wid, result["graph"], check, payload.expected_revision, None, note)
        return {**body, "saved": True, "workflow": agent_view(saved)}

    @router.get("/api/workflows/{wid}/versions")
    async def versions(wid: str):
        return {"versions": found(library.versions, wid)}

    @router.get("/api/workflows/{wid}/versions/{revision}")
    async def version(wid: str, revision: int):
        """One revision's graph and what differs from the current one."""
        from app.graph_edit import graph_changes
        current = found(library.get, wid)
        old = found(library.version, wid, revision)
        return {"revision": old["revision"], "note": old.get("note") or "", "updated_at": old.get("updated_at"),
                "graph": old["graph"], "changes_to_current": graph_changes(old["graph"], current["graph"])}

    @router.post("/api/workflows/{wid}/restore")
    async def restore(wid: str, payload: Restore):
        """A past revision becomes the next one; nothing in the history is overwritten."""
        old = found(library.version, wid, payload.revision)
        check = await check_against_comfy(ctx.comfy_url, old["graph"])
        return agent_view(found(library.replace_graph, wid, old["graph"], check, payload.expected_revision, None,
                                f"возврат к ревизии {payload.revision}"))

    async def fork(graph: dict, name: str, origin: dict):
        graph = found(check_api_graph, graph)
        check = await check_against_comfy(ctx.comfy_url, graph)
        payload = found(lambda: WorkflowIn(name=name[:120], graph=graph))
        return agent_view(found(library.create, payload, check, "job", origin))

    @router.post("/api/workflows/from-job/{job_id}", status_code=201)
    async def from_job(job_id: str, payload: Fork | None = None):
        """An H3 job's exact graph as a new workflow: edit nodes, LoRAs and references freely in this lane,
        the job and the accepted recipe stay untouched."""
        from .job_graphs import JobGraphMissing
        try:
            got = await ctx.job_graphs.get(ctx.store, job_id)
        except JobGraphMissing as exc:
            raise HTTPException(404 if "не найдено" in str(exc) else 409, str(exc)) from exc
        name = (payload.name if payload and payload.name else None) or f"Граф задания {job_id[:8]}"
        return await fork(got["graph"], name, {"kind": "job", "job_id": job_id, "remote_job_id": got["remote_job_id"],
                                              "graph_source": got["source"]})

    @router.post("/api/workflows/from-recipe/{recipe_id}", status_code=201)
    async def from_recipe(recipe_id: str, payload: Fork | None = None):
        """An accepted recipe's graph (the graph of its accepted job) as a new workflow. The recipe file is not changed."""
        from .job_graphs import JobGraphMissing
        from .recipes import accepted_recipes
        recipe = next((item for item in accepted_recipes() if item["id"] == recipe_id), None)
        if not recipe:
            raise HTTPException(404, "Принятый рецепт не найден")
        if not recipe.get("job_id"):
            raise HTTPException(409, "У рецепта нет задания: графа нет")
        try:
            got = await ctx.job_graphs.get(ctx.store, recipe["job_id"])
        except JobGraphMissing as exc:
            raise HTTPException(409, f"Рецепт «{recipe['title']}»: {exc}") from exc
        name = (payload.name if payload and payload.name else None) or f"Рецепт · {recipe['title']}"
        return await fork(got["graph"], name, {"kind": "recipe", "recipe_id": recipe_id, "job_id": recipe["job_id"],
                                              "graph_source": got["source"]})

    @router.post("/api/workflows/{wid}/open-in-comfy")
    async def open_in_comfy(wid: str):
        """Puts the graph into ComfyUI's Studio inbox; the editor opens it with ?h3_workflow=<id> and
        «⇪ Воркфлоу в Studio» sends it back to this same workflow."""
        record = found(library.get, wid)
        if not ctx.comfy_url:
            raise HTTPException(503, "ComfyUI не настроен: подключите GPU-сервер")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{ctx.comfy_url}/h3/studio/workflows", json={
                    "name": record["name"], "prompt": record["graph"], "studio_id": wid, "origin": "studio"})
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"ComfyUI недоступен: {type(exc).__name__}") from exc
        if response.status_code == 404:
            raise HTTPException(404, "На GPU старый мост Studio: обновите расширения "
                                     "(scripts/install_h3_studio_extensions.py --apply и перезапуск ComfyUI)")
        response.raise_for_status()
        item = response.json()["id"]
        return {"id": item, "url": f"{ctx.comfy_url}/?h3_workflow={item}"}

    @router.delete("/api/workflows/{wid}")
    async def delete(wid: str):
        found(library.delete, wid)
        return {"deleted": wid}

    @router.post("/api/workflows/{wid}/check")
    async def recheck(wid: str):
        record = found(library.get, wid)
        check = await check_against_comfy(ctx.comfy_url, record["graph"])
        return agent_view(found(library.set_check, wid, check))

    @router.post("/api/workflows/{wid}/run", status_code=202)
    async def run(wid: str, payload: RunIn):
        if not ctx.comfy_url:
            raise HTTPException(503, "ComfyUI не настроен: подключите GPU-сервер")
        return {"jobs": await asyncio.to_thread(found, queue.create, wid, payload)}

    @router.get("/api/workflow-jobs")
    async def jobs(workflow_id: str | None = None, limit: int = 50):
        return {"jobs": queue.list(workflow_id, max(1, min(limit, 200)))}

    @router.get("/api/workflow-jobs/{uid}")
    async def job(uid: str):
        row = queue.get(uid)
        if not row:
            raise HTTPException(404, "Задание не найдено")
        return row

    @router.post("/api/workflow-jobs/{uid}/cancel")
    async def cancel(uid: str):
        return public_job(found(queue.cancel, uid))

    @router.get("/api/workflow-jobs/{uid}/files/{index}")
    async def file(uid: str, index: int):
        row = queue._row(uid)
        if not row or row["state"] != "completed":
            raise HTTPException(404, "Результат ещё не готов")
        item = next((item for item in json.loads(row["outputs_json"] or "[]") if item["index"] == index), None)
        path = Path(item["path"]) if item else None
        if not path or not path.is_file() or not path.resolve().is_relative_to(queue.results.resolve()):
            raise HTTPException(404, "Файл отсутствует")
        if await asyncio.to_thread(file_hash, path) != item["sha256"]:
            raise HTTPException(409, "Файл изменён после скачивания")
        return FileResponse(path, filename=item["name"])

    return router
