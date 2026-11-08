"""Several subscriptions per agent CLI: when one Codex (or Claude) account hits its usage limit, the Studio agent
switches to another one with a click.

The main account is the CLI's own login (~/.codex, ~/.claude) and is never logged out from here: the ChatGPT and
Claude apps on this Mac share it. Extra accounts live in their own folders (CODEX_HOME / CLAUDE_CONFIG_DIR) under
~/.h3-agent-accounts; the active one is passed to every agent run through agent_brain.clean_env().
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import signal
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get("H3_AGENT_ACCOUNTS", str(Path.home() / ".h3-agent-accounts")))
ENV_KEY = {"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR"}
LOGIN_URL = re.compile(r"https://\S+")
_logins: dict[str, asyncio.subprocess.Process] = {}


def _state() -> dict:
    try:
        return json.loads((ROOT / "accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = ROOT / ".accounts.tmp"
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    temp.replace(ROOT / "accounts.json")


def folder(provider: str, account_id: str) -> Path:
    if provider not in ENV_KEY or not re.fullmatch(r"[a-f0-9]{8}", account_id):
        raise HTTPException(404, "Нет такого аккаунта")
    return ROOT / f"{provider}-{account_id}"


def accounts(provider: str) -> list[dict]:
    items = _state().get(provider, {}).get("items", [])
    return [{"id": "main", "label": "Основной (вход на этом Mac)"}] + [
        item for item in items if folder(provider, item["id"]).is_dir()]


def active(provider: str) -> str:
    chosen = _state().get(provider, {}).get("active", "main")
    return chosen if any(item["id"] == chosen for item in accounts(provider)) else "main"


def env_overrides() -> dict:
    """CODEX_HOME / CLAUDE_CONFIG_DIR of the chosen extra accounts; nothing for the main ones."""
    return {ENV_KEY[provider]: str(folder(provider, chosen)) for provider in ENV_KEY
            if (chosen := active(provider)) != "main"}


def _env(provider: str, account_id: str) -> dict:
    from .agent_brain import clean_env
    env = {key: value for key, value in clean_env().items() if key not in ENV_KEY.values()}
    if account_id != "main":
        env[ENV_KEY[provider]] = str(folder(provider, account_id))
    return env


async def _status(provider: str, account_id: str) -> dict:
    from .agent_brain import executable
    exe = executable(provider)
    if not exe:
        return {"logged_in": False, "login": "CLI не найден"}
    args = ["auth", "status"] if provider == "claude" else ["login", "status"]
    proc = await asyncio.create_subprocess_exec(exe, *args, env=_env(provider, account_id), stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 20)
    except asyncio.TimeoutError:
        proc.kill()
        return {"logged_in": False, "login": "CLI не ответил"}
    text = out.decode("utf-8", "replace").strip()
    if provider == "claude":
        try:
            data = json.loads(text)
            return {"logged_in": bool(data.get("loggedIn")), "login": data.get("email") or data.get("authMethod") or ""}
        except ValueError:
            pass
    return {"logged_in": proc.returncode == 0 and "not logged" not in text.lower(), "login": text[:160]}


class NewAccount(BaseModel):
    label: str = Field(default="", max_length=60)


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/agent-accounts")

    @router.get("/{provider}")
    async def list_accounts(provider: str) -> dict:
        if provider not in ENV_KEY:
            raise HTTPException(404, "Нет такого агента")
        items = accounts(provider)
        statuses = await asyncio.gather(*(_status(provider, item["id"]) for item in items))
        return {"provider": provider, "active": active(provider),
                "accounts": [{**item, **status, "logging_in": f"{provider}-{item['id']}" in _logins}
                             for item, status in zip(items, statuses)]}

    @router.post("/{provider}", status_code=201)
    async def add_account(provider: str, payload: NewAccount) -> dict:
        """A new empty account folder, then the CLI's own browser login into it (the user signs in there)."""
        if provider not in ENV_KEY:
            raise HTTPException(404, "Нет такого агента")
        account_id = secrets.token_hex(4)
        target = folder(provider, account_id)
        target.mkdir(mode=0o700, parents=True)
        if provider == "codex" and (Path.home() / ".codex/config.toml").is_file():
            # The Studio MCP server and model settings come along; the login (auth.json) does not.
            shutil.copy2(Path.home() / ".codex/config.toml", target / "config.toml")
        state = _state()
        entry = state.setdefault(provider, {})
        entry.setdefault("items", []).append({"id": account_id, "label": payload.label.strip() or f"Аккаунт {len(entry['items']) + 2}"})
        _save(state)
        return {"id": account_id, **(await _start_login(provider, account_id))}

    @router.post("/{provider}/{account_id}/login")
    async def login(provider: str, account_id: str) -> dict:
        if account_id == "main":
            raise HTTPException(409, "Основной аккаунт входит через приложение ChatGPT / Claude на этом Mac")
        folder(provider, account_id)
        return await _start_login(provider, account_id)

    @router.post("/{provider}/{account_id}/activate")
    async def activate(provider: str, account_id: str) -> dict:
        if account_id != "main":
            folder(provider, account_id)
            if not any(item["id"] == account_id for item in accounts(provider)):
                raise HTTPException(404, "Нет такого аккаунта")
            if not (await _status(provider, account_id))["logged_in"]:
                raise HTTPException(409, "Сначала войди в этот аккаунт")
        state = _state()
        state.setdefault(provider, {})["active"] = account_id
        _save(state)
        return {"provider": provider, "active": account_id}

    @router.post("/{provider}/{account_id}/logout")
    async def logout(provider: str, account_id: str) -> dict:
        """Signs an extra account out and forgets it; the main login of this Mac is never touched."""
        if account_id == "main":
            raise HTTPException(409, "Основной вход общий с приложением на этом Mac — выйти из него можно только там")
        from .agent_brain import executable
        target = folder(provider, account_id)
        exe = executable(provider)
        if exe:
            proc = await asyncio.create_subprocess_exec(exe, *(["auth", "logout"] if provider == "claude" else ["logout"]),
                                                        env=_env(provider, account_id), stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(proc.wait(), 30)
        state = _state()
        entry = state.setdefault(provider, {})
        entry["items"] = [item for item in entry.get("items", []) if item["id"] != account_id]
        if entry.get("active") == account_id:
            entry["active"] = "main"
        _save(state)
        shutil.rmtree(target, ignore_errors=True)
        return {"provider": provider, "active": active(provider)}

    return router


async def _start_login(provider: str, account_id: str) -> dict:
    """Runs the CLI's browser login in the account folder; returns the sign-in link it prints."""
    from .agent_brain import executable
    exe = executable(provider)
    if not exe:
        raise HTTPException(503, "CLI не найден")
    key = f"{provider}-{account_id}"
    previous = _logins.pop(key, None)
    if previous and previous.returncode is None:
        previous.send_signal(signal.SIGTERM)
    proc = await asyncio.create_subprocess_exec(exe, *(["auth", "login"] if provider == "claude" else ["login"]),
                                                env=_env(provider, account_id), stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    _logins[key] = proc
    url, seen = "", ""
    try:
        while not url:
            line = await asyncio.wait_for(proc.stdout.readline(), 15)
            if not line:
                break
            seen += line.decode("utf-8", "replace")
            match = LOGIN_URL.search(seen)
            url = match.group(0).rstrip(".,)") if match else ""
    except asyncio.TimeoutError:
        pass

    async def finish() -> None:  # the CLI waits for the browser callback, then exits
        try:
            await asyncio.wait_for(proc.communicate(), 900)  # drains the rest of its output
        except asyncio.TimeoutError:
            proc.kill()
        finally:
            _logins.pop(key, None)

    asyncio.create_task(finish())
    return {"login_url": url, "note": "Войди в аккаунт в открывшемся окне браузера; затем выбери его в списке."}
