"""Agent → open Studio tab: commands the page itself carries out with its own code (preview/h3-director/ui-bridge.mjs).

The canvas, the shot chain of «Собрать сцену» (keyframes, continuations from the last frame, QC) and the
project draft live in the browser tab. A Studio agent (Codex or Claude, h3-studio MCP tools studio_*) puts
a command here; the first open Studio tab claims it, runs it like a click of the user and posts the result,
which the tool waits for. Nothing runs without an open tab — the tool then says so.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

ACTIONS = ("board", "apply_draft", "apply_scene", "build_scene", "stop_build", "reset_scene", "select_scene", "new_scene", "set_start_frame")
READ_ACTIONS = {"board"}
CLAIM_TTL_S = 20      # a tab that claimed and went silent loses the command
TAB_SEEN_S = 10       # a tab polls every ~1.5 s
KEEP_S = 600
JOURNAL_KEEP_S = 7 * 24 * 60 * 60


class Command(BaseModel):
    action: str = Field(pattern=f"^({'|'.join(ACTIONS)})$")
    payload: dict = Field(default_factory=dict)
    source: str = Field(default="", max_length=80)


class Result(BaseModel):
    ok: bool
    result: dict | list | str | None = None
    error: str = Field(default="", max_length=2000)
    tab: str = Field(default="", max_length=64)


def make_router(data_root: Path | None = None) -> APIRouter:
    if data_root is None:
        from .config import DATA_ROOT
        data_root = DATA_ROOT
    data_root.mkdir(parents=True, exist_ok=True)
    journal = data_root / "ui-command-journal.sqlite3"
    with sqlite3.connect(journal) as db:
        db.execute("CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        commands = {row[0]: json.loads(row[1]) for row in db.execute("SELECT id,payload FROM commands")}

    def persist(command: dict) -> None:
        with sqlite3.connect(journal) as db:
            db.execute("INSERT INTO commands VALUES (?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (command["id"], json.dumps(command, ensure_ascii=False)))

    for command in commands.values():
        if command["state"] == "running":
            if command["action"] in READ_ACTIONS:
                command.update(state="pending", claimed_by="", claimed_at=0.0)
            else:
                command.update(state="needs_reconciliation", error="Studio перезапущена после выдачи команды; сверь холст и очередь перед повтором")
            persist(command)
    router = APIRouter(prefix="/api/ui")
    tabs: dict[str, float] = {}
    changed = asyncio.Condition()

    def sweep() -> None:
        now = time.monotonic()
        expired = [key for key, c in commands.items() if c["state"] in {"done", "failed"}
                   and time.time() - c["created"] > JOURNAL_KEEP_S]
        if expired:
            with sqlite3.connect(journal) as db:
                db.executemany("DELETE FROM commands WHERE id=?", [(key,) for key in expired])
            for key in expired:
                commands.pop(key, None)
        for key in [k for k, seen in tabs.items() if now - seen > 60]:
            tabs.pop(key, None)

    def view(command: dict) -> dict:
        return {k: command[k] for k in ("id", "action", "state", "result", "error")}

    @router.get("/tabs")
    async def open_tabs() -> dict:
        now = time.monotonic()
        return {"open": sum(1 for seen in tabs.values() if now - seen < TAB_SEEN_S)}

    @router.post("/commands", status_code=201)
    async def add(payload: Command) -> dict:
        sweep()
        command = {"id": uuid.uuid4().hex, **payload.model_dump(), "state": "pending", "claimed_by": "",
                   "claimed_at": 0.0, "created": time.time(), "result": None, "error": ""}
        commands[command["id"]] = command
        persist(command)
        return view(command)

    @router.get("/commands/next", response_model=None)
    async def claim(tab: str) -> Response | dict:
        """The tab's poll: heartbeat plus the oldest command nobody is carrying out."""
        now = time.monotonic()
        tabs[tab[:64]] = now
        for command in sorted(commands.values(), key=lambda c: c["created"]):
            stale = command["state"] == "running" and time.time() - command["claimed_at"] > CLAIM_TTL_S
            if stale and command["action"] not in READ_ACTIONS:
                # The old tab may have saved the draft or queued GPU work before losing its reply.
                # Reissuing a write would duplicate the scene or render.
                command.update(state="needs_reconciliation", error="Вкладка не подтвердила действие; сверь холст и очередь перед повтором")
                persist(command)
                continue
            if command["state"] == "pending" or stale:
                command.update(state="running", claimed_by=tab[:64], claimed_at=time.time())
                persist(command)
                return {"id": command["id"], "action": command["action"], "payload": command["payload"]}
        return Response(status_code=204)

    @router.post("/commands/{command_id}/result")
    async def report(command_id: str, payload: Result) -> dict:
        command = commands.get(command_id)
        if not command:
            raise HTTPException(404, "Команда не найдена")
        if not payload.tab:
            raise HTTPException(409, "Вкладка Studio устарела: обнови страницу, затем сверь холст и очередь")
        if not command["claimed_by"] or payload.tab != command["claimed_by"]:
            raise HTTPException(409, "Результат может подтвердить только вкладка, взявшая команду")
        if command["state"] in {"done", "failed"}:
            return view(command)
        command.update(state="done" if payload.ok else "failed", result=payload.result, error=payload.error)
        persist(command)
        async with changed:
            changed.notify_all()
        return view(command)

    @router.get("/commands/{command_id}")
    async def wait(command_id: str, wait: float = 0) -> dict:
        command = commands.get(command_id)
        if not command:
            raise HTTPException(404, "Команда не найдена")
        deadline = time.monotonic() + min(max(wait, 0), 60)
        while command["state"] in {"pending", "running"} and time.monotonic() < deadline:
            async with changed:
                try:
                    await asyncio.wait_for(changed.wait(), max(0.05, deadline - time.monotonic()))
                except TimeoutError:
                    break
        return view(command)

    @router.post("/commands/{command_id}/cancel")
    async def cancel(command_id: str) -> dict:
        command = commands.get(command_id)
        if not command:
            raise HTTPException(404, "Команда не найдена")
        if command["state"] == "pending":
            command.update(state="failed", error="Отменено: ни одна вкладка Studio не взяла команду")
            persist(command)
        return view(command)

    return router
