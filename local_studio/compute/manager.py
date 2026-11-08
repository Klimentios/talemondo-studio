from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from .keychain import KeychainStore
from .vast import MIN_DISK_GB, VastClient, VastOfferUnavailable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("Invalid provider ID")
    return value


class ComputeConflict(RuntimeError):
    pass


class ComputeManager:
    """Mac-local Vast orchestration. API credentials stay behind the Keychain adapter."""

    def __init__(self, data_root: Path, *, keychain: KeychainStore | None = None,
                 client_factory: Callable[[str], Any] = VastClient,
                 busy: Callable[[], bool] | None = None, repo_root: Path | None = None,
                 gpu_url: str = "") -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_root / "compute.sqlite3"
        self.keychain = keychain or KeychainStore()
        self.client_factory = client_factory
        self.busy = busy or (lambda: False)
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        self.gpu_url = gpu_url.rstrip("/")
        self._lock = asyncio.Lock()
        self._log_threads: dict[str, threading.Thread] = {}
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS compute_operations (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                instance_id INTEGER, offer_id INTEGER, label TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}'
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS compute_instances (
                id INTEGER PRIMARY KEY, label TEXT, state TEXT NOT NULL,
                gpu_name TEXT, rate REAL, updated_at TEXT NOT NULL,
                deployment_state TEXT NOT NULL DEFAULT 'not_started'
            )""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(compute_operations)")}
            for name, definition in (("pid", "INTEGER"), ("log_path", "TEXT"), ("exit_code", "INTEGER")):
                if name not in columns:
                    db.execute(f"ALTER TABLE compute_operations ADD COLUMN {name} {definition}")
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _client(self):
        key = self.keychain.get("vast")
        if not key:
            raise ComputeConflict("provider_not_connected")
        return self.client_factory(key)

    def current_server(self) -> dict[str, Any]:
        """Read only the endpoint fields from the installed tunnel script; never execute it."""
        path = Path.home() / "Library" / "Application Support" / "MiniMax H3" / "tunnel.sh"
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            return {"host": None, "port": None, "configured": False}
        host_match = re.search(r'^SERVER_HOST="([A-Za-z0-9.-]{1,253})"$', source, re.MULTILINE)
        port_match = re.search(r'^SERVER_PORT="([0-9]{1,5})"$', source, re.MULTILINE)
        host = host_match.group(1) if host_match else None
        port = int(port_match.group(1)) if port_match else None
        if not self._valid_ssh_host(host) or port is None or not 1 <= port <= 65535:
            return {"host": None, "port": None, "configured": False}
        return {"host": host, "port": port, "configured": True}

    def _is_current_server(self, instance: dict[str, Any]) -> bool:
        current = self.current_server()
        if not current["configured"]:
            return False
        # Vast may omit SSH endpoints for stopped instances. An unknown running endpoint is
        # conservatively treated as the configured tunnel target, but a stopped server is manageable.
        if not instance.get("ssh_host") or not instance.get("ssh_port"):
            return str(instance.get("actual_status", "")).lower() in {"running", "loading", "starting", "creating"}
        return instance.get("ssh_host") == current["host"] and instance.get("ssh_port") == current["port"]

    def _deployment_in_progress(self) -> bool:
        """True only while an installer process may really be running. A finished deploy that
        ended in needs_reconciliation (health not confirmed) must not block stop/destroy/redeploy:
        the server keeps billing and the user has to be able to switch it off from the UI."""
        with self._connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id,state FROM compute_operations WHERE kind IN ('deploy','overlay','restore','comfy_manifest') AND state IN ('starting','running')")]
        for row in rows:
            if row["state"] == "starting":
                return True
            # operation() re-checks the installer pid and demotes a dead one to needs_reconciliation.
            current = self.operation(row["id"])
            if current and current["state"] == "running":
                return True
        return False

    async def connection(self) -> dict[str, Any]:
        if not self.keychain.has("vast"):
            return {"connected": False, "email": None, "balance": None, "checked_at": None}
        account = await self._client().get_account()
        return {"connected": True, "email": account["email"], "balance": account["balance"],
                "checked_at": _now()}

    async def save_key(self, key: str) -> dict[str, Any]:
        candidate = self.client_factory(key)
        account = await candidate.get_account()
        self.keychain.set("vast", key)
        return {"connected": True, "email": account["email"], "balance": account["balance"],
                "checked_at": _now()}

    def delete_key(self) -> None:
        self.keychain.delete("vast")

    async def offers(self, *, max_hourly_rate: float | None = None) -> list[dict[str, Any]]:
        return await self._client().search_h3_offers(max_hourly_rate=max_hourly_rate)

    async def instances(self) -> list[dict[str, Any]]:
        client = self._client()
        rows = await client.get_instances()
        await self._reconcile_pending_rents(rows)
        with self._connect() as db:
            for row in rows:
                db.execute("""INSERT INTO compute_instances(id,label,state,gpu_name,rate,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,
                    state=excluded.state,gpu_name=excluded.gpu_name,rate=excluded.rate,updated_at=excluded.updated_at""",
                    (row["id"], row.get("label"), row.get("actual_status") or "unknown",
                     row.get("gpu_name"), row.get("total_usd_per_hour"), _now()))
            local = {r["id"]: dict(r) for r in db.execute("SELECT * FROM compute_instances")}
            deployments = {}
            for row in db.execute("SELECT id,instance_id,state,updated_at FROM compute_operations WHERE kind='deploy' ORDER BY updated_at DESC"):
                deployments.setdefault(row["instance_id"], dict(row))
        for row in rows:
            saved = local.get(row["id"], {})
            row["deployment_state"] = saved.get("deployment_state", "not_started")
            deployment = deployments.get(row["id"], {})
            row["deployment_operation_id"] = deployment.get("id")
            row["deployment_updated_at"] = deployment.get("updated_at")
            row["is_current_tunnel_target"] = self._is_current_server(row)
        return rows

    async def _reconcile_pending_rents(self, rows: list[dict[str, Any]]) -> None:
        with self._connect() as db:
            pending = [dict(row) for row in db.execute(
                "SELECT id,label FROM compute_operations WHERE kind='rent' AND state IN ('creating','needs_reconciliation')")]
        by_label: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("label"):
                by_label.setdefault(row["label"], []).append(row)
        for operation in pending:
            matches = by_label.get(operation["label"], [])
            if len(matches) != 1:
                continue
            match = matches[0]
            self._record_instance(match)
            with self._connect() as db:
                db.execute("UPDATE compute_operations SET state='created',instance_id=?,updated_at=? WHERE id=?",
                           (match["id"], _now(), operation["id"]))

    async def rent(self, offer_id: int, max_hourly_rate: float) -> dict[str, Any]:
        offer_id = _safe_id(offer_id)
        if (isinstance(max_hourly_rate, bool) or not isinstance(max_hourly_rate, (int, float))
                or not math.isfinite(max_hourly_rate) or max_hourly_rate <= 0):
            raise ValueError("Maximum hourly rate must be positive")
        async with self._lock:
            client = self._client()
            remote_instances = await client.get_instances()
            await self._reconcile_pending_rents(remote_instances)
            with self._connect() as db:
                unresolved = db.execute("SELECT 1 FROM compute_operations WHERE kind='rent' AND state IN ('creating','needs_reconciliation') LIMIT 1").fetchone()
            if unresolved:
                raise ComputeConflict("rent_needs_reconciliation")
            fresh = await client.search_h3_offers(max_hourly_rate=float(max_hourly_rate))
            offer = next((row for row in fresh if row["id"] == offer_id), None)
            if offer is None:
                raise VastOfferUnavailable("The selected offer is no longer compatible or exceeds the confirmed price")
            operation_id = uuid.uuid4().hex
            label = f"H3 Studio {operation_id[:12]}"
            now = _now()
            with self._connect() as db:
                db.execute("INSERT INTO compute_operations(id,kind,state,offer_id,label,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                           (operation_id, "rent", "creating", offer_id, label, now, now))
            try:
                result = await client.create_instance(offer_id, MIN_DISK_GB, label=label)
            except Exception as exc:
                # A timed out create may have succeeded remotely. Record intent and never retry here.
                with self._connect() as db:
                    db.execute("UPDATE compute_operations SET state='needs_reconciliation',updated_at=? WHERE id=?",
                               (_now(), operation_id))
                try:
                    rows = await client.get_instances()
                    matches = [row for row in rows if row.get("label") == label]
                    if len(matches) == 1:
                        self._record_instance(matches[0])
                        with self._connect() as db:
                            db.execute("UPDATE compute_operations SET state='created',instance_id=?,updated_at=? WHERE id=?",
                                       (matches[0]["id"], _now(), operation_id))
                        return {"operation_id": operation_id, "instance_id": matches[0]["id"], "state": "created"}
                except Exception:
                    pass
                raise ComputeConflict("rent_needs_reconciliation") from None
            self._record_instance({"id": result["instance_id"], "label": label,
                                   "actual_status": "creating", "gpu_name": offer.get("gpu_name"),
                                   "total_usd_per_hour": offer["total_usd_per_hour"]})
            with self._connect() as db:
                db.execute("UPDATE compute_operations SET state='created',instance_id=?,updated_at=? WHERE id=?",
                           (result["instance_id"], _now(), operation_id))
            return {"operation_id": operation_id, "instance_id": result["instance_id"], "state": "created"}

    def _record_instance(self, row: dict[str, Any]) -> None:
        with self._connect() as db:
            db.execute("""INSERT INTO compute_instances(id,label,state,gpu_name,rate,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,state=excluded.state,
                gpu_name=excluded.gpu_name,rate=excluded.rate,updated_at=excluded.updated_at""",
                (row["id"], row.get("label"), row.get("actual_status") or "creating",
                 row.get("gpu_name"), row.get("total_usd_per_hour"), _now()))

    async def stop(self, instance_id: int) -> dict[str, str]:
        instance_id = _safe_id(instance_id)
        if self.busy():
            raise ComputeConflict("studio_jobs_active")
        async with self._lock:
            if self._deployment_in_progress():
                raise ComputeConflict("deployment_in_progress")
            remote = next((item for item in await self._client().get_instances() if item["id"] == instance_id), None)
            if remote is None:
                raise ComputeConflict("instance_not_found")
            await self._client().stop_instance(instance_id)
            with self._connect() as db:
                db.execute("UPDATE compute_instances SET state='stopping',deployment_state='not_started',updated_at=? WHERE id=?",
                           (_now(), instance_id))
            return {"state": "stopping"}

    async def start(self, instance_id: int) -> dict[str, str]:
        instance_id = _safe_id(instance_id)
        async with self._lock:
            client = self._client()
            instance = await client.get_instance(instance_id)
            if str(instance.get("actual_status", "")).lower() != "stopped":
                raise ComputeConflict("instance_not_stopped")
            await client.start_instance(instance_id)
            with self._connect() as db:
                db.execute("UPDATE compute_instances SET state='starting',deployment_state='not_started',updated_at=? WHERE id=?",
                           (_now(), instance_id))
            return {"state": "starting"}

    async def destroy(self, instance_id: int, confirmation_id: int) -> dict[str, str]:
        instance_id = _safe_id(instance_id)
        if confirmation_id != instance_id:
            raise ValueError("Confirmation ID does not match")
        if self.busy():
            raise ComputeConflict("studio_jobs_active")
        async with self._lock:
            if self._deployment_in_progress():
                raise ComputeConflict("deployment_in_progress")
            remote = next((item for item in await self._client().get_instances() if item["id"] == instance_id), None)
            if remote is None:
                raise ComputeConflict("instance_not_found")
            if self._is_current_server(remote) and str(remote.get("actual_status", "")).lower() != "stopped":
                raise ComputeConflict("stop_current_before_destroy")
            await self._client().destroy_instance(instance_id)
            with self._connect() as db:
                db.execute("UPDATE compute_instances SET state='destroyed',updated_at=? WHERE id=?", (_now(), instance_id))
            return {"state": "destroyed"}

    def operation(self, operation_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT id,kind,state,label,instance_id,offer_id,created_at,updated_at,pid,exit_code,log_path FROM compute_operations WHERE id=?",
                             (operation_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        if item["kind"] in {"deploy", "verify", "drift", "overlay", "restore", "comfy_manifest"} and item["state"] == "running" and item["pid"]:
            try:
                os.kill(item["pid"], 0)
                check = subprocess.run(["ps", "-p", str(item["pid"]), "-o", "command="],
                                       capture_output=True, text=True, timeout=2, check=False)
                expected = {
                    "deploy": self.repo_root / "infra" / "h3_up.sh",
                    "verify": self.repo_root / "scripts" / "verify_h3_server.sh",
                    "drift": self.repo_root / "scripts" / "h3_server_drift.sh",
                    "overlay": self.repo_root / "scripts" / "h3_new_server.sh",
                    "restore": self.repo_root / "infra" / "h3_up.sh",
                    "comfy_manifest": self.repo_root / "scripts" / "h3_comfy_manifest_apply.sh",
                }[item["kind"]]
                if str(expected) not in check.stdout:
                    raise ProcessLookupError
            except (OSError, subprocess.SubprocessError):
                item["state"] = "needs_reconciliation"
                with self._connect() as db:
                    db.execute("UPDATE compute_operations SET state='needs_reconciliation',updated_at=? WHERE id=?",
                               (_now(), operation_id))
                    if item["instance_id"] and item["kind"] in {"deploy", "overlay", "restore"}:
                        db.execute("UPDATE compute_instances SET deployment_state='needs_reconciliation',updated_at=? WHERE id=?",
                                   (_now(), item["instance_id"]))
        item["log_tail"] = self._log_tail(item.pop("log_path"))
        item.pop("pid", None)
        return item

    @staticmethod
    def _log_tail(path: str | None, limit: int = 12000) -> str:
        if not path:
            return ""
        try:
            raw = Path(path).read_bytes()[-limit:].decode("utf-8", errors="replace")
        except OSError:
            return ""
        patterns = (r"hf_[A-Za-z0-9]{12,}", r"(?:Bearer|token|secret[_-]?access[_-]?key)\s*[:= ]\s*[^\s\"']+",
                    r"(?:RCLONE_CONFIG_R2_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY)=)[^\s]+")
        for pattern in patterns:
            raw = re.sub(pattern, "[скрыто]", raw, flags=re.IGNORECASE)
        return raw

    def _persist_log_stream(self, operation_id: str, stream) -> None:
        with self._connect() as db:
            row = db.execute("SELECT log_path FROM compute_operations WHERE id=?", (operation_id,)).fetchone()
        if not row or not row["log_path"]:
            return
        path = Path(row["log_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        patterns = (re.compile(rb"hf_[A-Za-z0-9]{12,}"),
                    re.compile(rb"(?i)(bearer|token|secret[_-]?access[_-]?key)(\s*[:= ]\s*)[^\s\"']+"),
                    re.compile(rb"(?i)(RCLONE_CONFIG_R2_(?:ACCESS_KEY_ID|SECRET_ACCESS_KEY)=)[^\s]+"))
        try:
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = stream.readline(16384)
                    if not chunk:
                        break
                    for pattern in patterns:
                        chunk = pattern.sub(lambda match: (match.group(1) + match.group(2) + b"[redacted]")
                                            if pattern is patterns[1] else b"[redacted]", chunk)
                    if output.tell() + len(chunk) > 2 * 1024 * 1024:
                        output.flush()
                        output.seek(0)
                        tail = output.read()[-1024 * 1024:]
                        output.seek(0)
                        output.truncate(0)
                        output.write(tail)
                    output.write(chunk)
        except OSError:
            return

    @staticmethod
    def _valid_ssh_host(host: Any) -> bool:
        return isinstance(host, str) and len(host) <= 253 and bool(re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)", host)) and ".." not in host

    async def start_deploy(self, instance_id: int) -> dict[str, Any]:
        instance_id = _safe_id(instance_id)
        if self.busy():
            raise ComputeConflict("studio_jobs_active")
        async with self._lock:
            rows = await self._client().get_instances()
            target = next((item for item in rows if item["id"] == instance_id), None)
            if not target:
                raise ComputeConflict("instance_not_found")
            if self._is_current_server(target):
                raise ComputeConflict("deploy_current_server")
            if str(target.get("actual_status", "")).lower() != "running":
                raise ComputeConflict("instance_not_running")
            host, port = target.get("ssh_host"), target.get("ssh_port")
            if not self._valid_ssh_host(host) or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ComputeConflict("ssh_endpoint_not_ready")
            identity = Path.home() / ".ssh" / "id_ed25519"
            if not identity.is_file():
                raise ComputeConflict("ssh_identity_missing")
            # Own R2 snapshot (infra/h3_up.sh) when its keys are in the Keychain; otherwise a fresh install with the
            # MiniMax H3 weights from Hugging Face (scripts/h3_new_server.sh) — the path for a new Studio owner.
            snapshot = self._r2_configured()
            script = self.repo_root / ("infra/h3_up.sh" if snapshot else "scripts/h3_new_server.sh")
            if not script.is_file():
                raise ComputeConflict("deploy_script_missing")
            if self._deployment_in_progress():
                raise ComputeConflict("deployment_already_running")
            operation_id = uuid.uuid4().hex
            log_path = self.data_root / f"compute-deploy-{operation_id}.log"
            now = _now()
            with self._connect() as db:
                db.execute("INSERT INTO compute_operations(id,kind,state,instance_id,created_at,updated_at,log_path) VALUES(?,?,?,?,?,?,?)",
                           (operation_id, "deploy", "starting", instance_id, now, now, str(log_path)))
                db.execute("UPDATE compute_instances SET deployment_state='installing',updated_at=? WHERE id=?",
                           (now, instance_id))
            try:
                process = subprocess.Popen(
                    ["bash", str(script), "--host", f"root@{host}", "--port", str(port), "--identity", str(identity),
                     "--qwen", "disabled"],  # both scripts take the same arguments
                    cwd=self.repo_root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    close_fds=True, start_new_session=True,
                    env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                         "HOME": str(Path.home()), "USER": os.environ.get("USER", ""),
                         "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                         "TMPDIR": os.environ.get("TMPDIR", "/tmp")},
                )
            except OSError:
                with self._connect() as db:
                    db.execute("UPDATE compute_operations SET state='failed',updated_at=? WHERE id=?", (_now(), operation_id))
                    db.execute("UPDATE compute_instances SET deployment_state='failed',updated_at=? WHERE id=?", (_now(), instance_id))
                raise ComputeConflict("deploy_start_failed") from None
            with self._connect() as db:
                db.execute("UPDATE compute_operations SET state='running',pid=?,updated_at=? WHERE id=?",
                           (process.pid, _now(), operation_id))
            if process.stdout is not None:
                log_thread = threading.Thread(target=self._persist_log_stream,
                                              args=(operation_id, process.stdout), daemon=True)
                self._log_threads[operation_id] = log_thread
                log_thread.start()
            asyncio.create_task(self._watch_deploy(operation_id, process))
            return {"operation_id": operation_id, "instance_id": instance_id, "state": "running"}

    async def _watch_deploy(self, operation_id: str, process: subprocess.Popen) -> None:
        exit_code = await asyncio.to_thread(process.wait)
        log_thread = self._log_threads.pop(operation_id, None)
        if log_thread:
            await asyncio.to_thread(log_thread.join, 3)
        item = self.operation(operation_id)
        instance_id = item.get("instance_id") if item else None
        ready = False
        if exit_code == 0 and self.gpu_url:
            ready = await self._gpu_ready()
        state = "ready" if ready else ("needs_reconciliation" if exit_code == 0 else "failed")
        now = _now()
        with self._connect() as db:
            db.execute("UPDATE compute_operations SET state=?,exit_code=?,updated_at=? WHERE id=?",
                       (state, exit_code, now, operation_id))
            if instance_id:
                db.execute("UPDATE compute_instances SET deployment_state=?,updated_at=? WHERE id=?",
                           ("ready" if ready else state, now, instance_id))
                if ready:
                    db.execute("UPDATE compute_instances SET deployment_state='inactive',updated_at=? WHERE id!=? AND deployment_state='ready'",
                               (now, instance_id))

    HEALTH_ATTEMPTS = 6
    HEALTH_RETRY_S = 5.0

    async def _gpu_ready(self) -> bool:
        """GPU API /api/health answers {"status":"ok","engine":"ready"} (app/main.py). The tunnel
        and the engine may need a few seconds after the installer exits, so retry briefly."""
        for attempt in range(self.HEALTH_ATTEMPTS):
            if attempt:
                await asyncio.sleep(self.HEALTH_RETRY_S)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
                    response = await client.get(f"{self.gpu_url}/api/health")
                body = response.json() if response.is_success else {}
            except Exception:
                continue
            if isinstance(body, dict) and (body.get("engine") == "ready" or body.get("gpu_reachable") is True):
                return True
        return False

    @staticmethod
    def _r2_configured() -> bool:
        """A personal R2 snapshot exists only when its three keys are in the login Keychain (infra/h3_r2_common.sh)."""
        try:
            return subprocess.run(["security", "find-generic-password", "-s", "h3-r2-account-id", "-a", "h3"],
                                  capture_output=True, timeout=10).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def deployment_status(self) -> dict[str, Any]:
        identity = Path.home() / ".ssh" / "id_ed25519"
        script = self.repo_root / "infra" / "h3_up.sh"
        return {"available": identity.is_file() and script.is_file(),
                "state": "available" if identity.is_file() and script.is_file() else "prerequisites_missing",
                "message": "Потребуется только SSH-ключ ~/.ssh/id_ed25519 («Первый запуск»). Веса скачиваются с Hugging Face без токена."}

    def recent_operations(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT id FROM compute_operations ORDER BY created_at DESC LIMIT ?", (min(max(limit, 1), 100),))
            ids = [row["id"] for row in rows]
        return [item for operation_id in ids if (item := self.operation(operation_id)) is not None]

    async def start_operator_action(self, action: str, host: str, port: int, manifest: Path | None = None) -> dict[str, Any]:
        """Run one fixed H3 script. Browser text is never interpolated into a shell command.
        comfy_manifest: the user's ComfyUI manifest file (a path Studio owns) onto the current server."""
        if action not in {"verify", "drift", "overlay", "restore", "comfy_manifest"}:
            raise ValueError("Неизвестное действие сервера")
        if not self._valid_ssh_host(host) or isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("Укажи допустимый SSH host и порт")
        identity = Path.home() / ".ssh" / "id_ed25519"
        if not identity.is_file():
            raise ComputeConflict("ssh_identity_missing")
        if action in {"overlay", "restore", "comfy_manifest"} and self.busy():
            raise ComputeConflict("studio_jobs_active")
        current = self.current_server()
        if action in {"overlay", "comfy_manifest"} and (host, port) != (current["host"], current["port"]):
            raise ComputeConflict("overlay_requires_current_server")
        if action == "restore" and (host, port) == (current["host"], current["port"]):
            raise ComputeConflict("restore_current_server")
        scripts = {"verify": self.repo_root / "scripts" / "verify_h3_server.sh",
                   "drift": self.repo_root / "scripts" / "h3_server_drift.sh",
                   "overlay": self.repo_root / "scripts" / "h3_new_server.sh",
                   "restore": self.repo_root / ("infra/h3_up.sh" if self._r2_configured() else "scripts/h3_new_server.sh"),
                   "comfy_manifest": self.repo_root / "scripts" / "h3_comfy_manifest_apply.sh"}
        script = scripts[action]
        if action == "comfy_manifest" and (manifest is None or not Path(manifest).is_file()):
            raise ValueError("Манифест ComfyUI не найден")
        if not script.is_file():
            raise ComputeConflict("deploy_script_missing")
        async with self._lock:
            if action in {"overlay", "restore", "comfy_manifest"} and self._deployment_in_progress():
                raise ComputeConflict("deployment_already_running")
            operation_id = uuid.uuid4().hex
            now = _now()
            log_path = self.data_root / f"compute-{action}-{operation_id}.log"
            args = ["bash", str(script), "--host", f"root@{host}", "--port", str(port), "--identity", str(identity)]
            if action == "overlay":
                args += ["--skip-base", "--keep-paused"]
            elif action == "restore" and script.name == "h3_up.sh":
                args += ["--stamp", "LATEST", "--keep-paused"]
            elif action == "comfy_manifest":
                args += ["--manifest", str(manifest), "--apply"]
            with self._connect() as db:
                db.execute("INSERT INTO compute_operations(id,kind,state,label,created_at,updated_at,log_path) VALUES(?,?,?,?,?,?,?)",
                           (operation_id, action, "starting", f"{host}:{port}", now, now, str(log_path)))
            try:
                process = subprocess.Popen(args, cwd=self.repo_root, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           close_fds=True, start_new_session=True,
                                           env={"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                                "HOME": str(Path.home()), "USER": os.environ.get("USER", ""),
                                                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                                                "TMPDIR": os.environ.get("TMPDIR", "/tmp")})
            except OSError:
                with self._connect() as db:
                    db.execute("UPDATE compute_operations SET state='failed',updated_at=? WHERE id=?", (_now(), operation_id))
                raise ComputeConflict("deploy_start_failed") from None
            with self._connect() as db:
                db.execute("UPDATE compute_operations SET state='running',pid=?,updated_at=? WHERE id=?",
                           (process.pid, _now(), operation_id))
            if process.stdout is not None:
                log_thread = threading.Thread(target=self._persist_log_stream,
                                              args=(operation_id, process.stdout), daemon=True)
                self._log_threads[operation_id] = log_thread
                log_thread.start()
            asyncio.create_task(self._watch_operator(operation_id, process))
            return {"operation_id": operation_id, "action": action, "state": "running"}

    async def _watch_operator(self, operation_id: str, process: subprocess.Popen) -> None:
        exit_code = await asyncio.to_thread(process.wait)
        log_thread = self._log_threads.pop(operation_id, None)
        if log_thread:
            await asyncio.to_thread(log_thread.join, 3)
        with self._connect() as db:
            db.execute("UPDATE compute_operations SET state=?,exit_code=?,updated_at=? WHERE id=?",
                       ("ready" if exit_code == 0 else "failed", exit_code, _now(), operation_id))
