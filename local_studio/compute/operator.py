"""Local-only H3 server workbench: inspect, fixed runbooks and an explicit SSH terminal."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import struct
import subprocess
import termios

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .manager import ComputeConflict, ComputeManager


class Target(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(gt=0, le=65535)


class Runbook(Target):
    action: str


def _same_origin(host: str, origin: str | None) -> bool:
    return bool(re.fullmatch(r"(?:127\.0\.0\.1|localhost):[0-9]{1,5}", host.lower())) and origin == f"http://{host.lower()}"


def _probe(manager: ComputeManager, host: str, port: int) -> dict:
    identity = Path.home() / ".ssh" / "id_ed25519"
    result = {"host": host, "port": port, "ssh_key": identity.is_file(), "ssh": False,
              "r2_keys": {}, "hf_token": False, "r2_latest": None,
              "r2_snapshot_complete": False, "r2_weight_gb": None, "remote": {}, "problems": []}
    for service in ("h3-r2-account-id", "h3-r2-access-key-id", "h3-r2-secret-access-key", "h3-hf-token"):
        try:
            check = subprocess.run(["security", "find-generic-password", "-s", service, "-a", "h3"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            present = check.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            present = False
        if service == "h3-hf-token":
            result["hf_token"] = present
        else:
            result["r2_keys"][service] = present
    if all(result["r2_keys"].values()):
        command = ("source infra/h3_r2_common.sh; eval \"$(h3_r2_env_lines)\"; "
                   "export RCLONE_CONFIG=/dev/null; "
                   "stamp=$(rclone cat \"$H3_R2_ROOT/snapshots/LATEST\" 2>/dev/null | tr -d '[:space:]'); "
                   "[[ -n $stamp ]] || exit 1; printf 'stamp=%s\\n' \"$stamp\"; "
                   "for item in software.tar.zst software.sha256 weights.tsv SNAPSHOT.txt; do "
                   "rclone lsf \"$H3_R2_ROOT/snapshots/$stamp/$item\" 2>/dev/null | grep -q . || exit 2; done; "
                   "printf 'complete=yes\\n'; "
                   "rclone cat \"$H3_R2_ROOT/snapshots/$stamp/weights.tsv\" 2>/dev/null | "
                   "awk -F '\\t' 'NF>=3 {s+=$2} END {printf \"weight_gb=%.1f\\n\",s/1e9}'")
        try:
            latest = subprocess.run(["bash", "-c", command], cwd=manager.repo_root,
                                    capture_output=True, text=True, timeout=12)
            for line in latest.stdout.splitlines():
                key, sep, value = line.partition("=")
                if sep and key == "stamp":
                    result["r2_latest"] = value.strip()[:80]
                elif sep and key == "complete":
                    result["r2_snapshot_complete"] = value == "yes"
                elif sep and key == "weight_gb":
                    result["r2_weight_gb"] = value.strip()[:40]
        except (OSError, subprocess.TimeoutExpired):
            pass
    if not identity.is_file():
        result["problems"].append("SSH-ключ отсутствует")
        return result
    script = """set -u
printf 'gpu='; nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1
printf 'cuda='; nvcc --version 2>/dev/null | sed -n 's/.*release \\([0-9.]*\\).*/\\1/p' | tail -1
printf 'disk_available='; df -B1 --output=avail /workspace | tail -1 | tr -d ' '
printf 'image_stamp='; cat /workspace/h3/.h3-image-stamp 2>/dev/null || true
printf 'h3_dir='; if [ -d /workspace/h3 ]; then if [ -z "$(ls -A /workspace/h3 2>/dev/null)" ]; then echo empty; else echo populated; fi; else echo absent; fi
printf 'comfy='; curl -fsS --max-time 3 http://127.0.0.1:18188/queue 2>/dev/null | python3 -c 'import sys,json;d=json.load(sys.stdin);print(len(d.get("queue_running",[])),len(d.get("queue_pending",[])),sep=",")' 2>/dev/null || echo unavailable
"""
    try:
        probe = subprocess.run(["ssh", "-i", str(identity), "-p", str(port), "-o", "BatchMode=yes",
                                "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
                                f"root@{host}", "bash", "-s"], input=script,
                               capture_output=True, text=True, timeout=18)
    except (OSError, subprocess.TimeoutExpired):
        result["problems"].append("SSH не ответил за 18 секунд")
        return result
    if probe.returncode != 0:
        result["problems"].append("SSH недоступен или ключ сервера изменился")
        return result
    result["ssh"] = True
    for line in probe.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {"gpu", "cuda", "disk_available", "image_stamp", "h3_dir", "comfy"}:
            result["remote"][key] = value.strip()[:180]
    current = manager.current_server()
    result["current"] = (host, port) == (current["host"], current["port"])
    result["jobs_active"] = manager.busy()
    # Without an own R2 snapshot the server is installed from scratch with the public weights from Hugging Face.
    result["install_mode"] = "snapshot" if result["r2_snapshot_complete"] else "fresh"
    if result["current"] and result["jobs_active"]:
        result["problems"].append("На текущем сервере есть задания; обновление сервисов отложить")
    result["plan"] = (["Проверить SSH, GPU, CUDA и диск", "Сверить снимок R2",
                       "Восстановить программный архив и веса параллельно", "Проверить SHA всех весов",
                       "Применить код и конфиги", "Проверить граф, узлы, API и туннель Studio"]
                      if result["install_mode"] == "snapshot" else
                      ["Проверить SSH, GPU, CUDA и диск", "Поставить ComfyUI с закреплённой сборкой",
                       "Скачать открытые веса MiniMax H3 с Hugging Face и проверить SHA-256",
                       "Поставить узлы Studio и GPU API", "Проверить граф, узлы, API и туннель Studio"])
    return result


def make_router(manager: ComputeManager) -> APIRouter:
    router = APIRouter(prefix="/operator", tags=["compute-operator"])

    @router.post("/plan")
    async def plan(target: Target, request: Request):
        if not _same_origin(request.headers.get("host", ""), request.headers.get("origin")):
            raise HTTPException(403, "Требуется запрос со страницы Studio")
        if not manager._valid_ssh_host(target.host):
            raise HTTPException(422, "Недопустимый SSH host")
        return await asyncio.to_thread(_probe, manager, target.host, target.port)

    @router.get("/operations")
    async def operations():
        return {"operations": manager.recent_operations()}

    @router.post("/run")
    async def run(payload: Runbook, request: Request):
        if not _same_origin(request.headers.get("host", ""), request.headers.get("origin")):
            raise HTTPException(403, "Требуется запрос со страницы Studio")
        if payload.action not in {"verify", "drift", "overlay", "restore"}:
            raise HTTPException(422, "Неизвестное действие сервера")
        try:
            if payload.action in {"restore", "overlay"}:
                checked = await asyncio.to_thread(_probe, manager, payload.host, payload.port)
                if not checked["ssh"]:
                    raise ComputeConflict("ssh_not_ready")
            return await manager.start_operator_action(payload.action, payload.host, payload.port)
        except ComputeConflict as exc:
            messages = {"studio_jobs_active": "В Studio есть активные задания. Дождись их завершения.",
                        "overlay_requires_current_server": "Обновлять код можно только на текущем сервере Studio.",
                        "restore_current_server": "Нельзя восстанавливать снимок поверх текущего сервера.",
                        "deployment_already_running": "Установка уже выполняется.",
                        "ssh_identity_missing": "SSH-ключ на Mac не найден.",
                        "ssh_not_ready": "SSH недоступен; сначала проверь адрес и ключ.",
                        "snapshot_not_ready": "R2 LATEST или Hugging Face token не подтверждены.",
                        "deploy_script_missing": "Скрипт H3 в проекте не найден."}
            raise HTTPException(409, messages.get(str(exc), "Действие сейчас недоступно.")) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @router.websocket("/terminal")
    async def terminal(websocket: WebSocket):
        if not _same_origin(websocket.headers.get("host", ""), websocket.headers.get("origin")):
            await websocket.close(code=1008)
            return
        host, port_text = websocket.query_params.get("host", ""), websocket.query_params.get("port", "")
        try:
            port = int(port_text)
        except ValueError:
            port = 0
        identity = Path.home() / ".ssh" / "id_ed25519"
        if not manager._valid_ssh_host(host) or not 1 <= port <= 65535 or not identity.is_file():
            await websocket.close(code=1008)
            return
        await websocket.accept()
        master, slave = os.openpty()
        process = None
        try:
            process = subprocess.Popen(["ssh", "-tt", "-i", str(identity), "-p", str(port),
                                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                        "-o", "StrictHostKeyChecking=accept-new", f"root@{host}"],
                                       stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                       close_fds=True)
            os.close(slave)
            slave = -1

            async def output():
                while True:
                    try:
                        chunk = await asyncio.to_thread(os.read, master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    await websocket.send_text(chunk.decode("utf-8", errors="replace"))

            async def input_loop():
                while True:
                    data = await websocket.receive_text()
                    if len(data) > 8192:
                        continue
                    message = json.loads(data)
                    if message.get("type") == "input":
                        os.write(master, str(message.get("data", ""))[:8192].encode())
                    elif message.get("type") == "resize":
                        rows = min(max(int(message.get("rows", 24)), 5), 200)
                        cols = min(max(int(message.get("cols", 80)), 20), 400)
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

            readers = [asyncio.create_task(output()), asyncio.create_task(input_loop())]
            done, pending = await asyncio.wait(readers, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                try:
                    task.result()
                except (WebSocketDisconnect, OSError, ValueError, json.JSONDecodeError):
                    pass
        finally:
            if slave >= 0:
                os.close(slave)
            os.close(master)
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), timeout=2)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
            if websocket.client_state.name == "CONNECTED":
                await websocket.close()

    return router
