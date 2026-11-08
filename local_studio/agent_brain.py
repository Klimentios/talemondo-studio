"""Studio brain through the user's own logged-in Claude Code and Codex CLIs (subscription, no API keys).

The CLIs are the official headless entry points: `claude -p` and `codex exec`. Each call gets a clean
environment, so an API key or a parent agent session never leaks in and billing stays on the
subscription the CLI is logged in with. Read-only by default: the agent may read the repo (docs,
skills, franchise bibles) but never edits files or runs commands.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

REPO = Path(__file__).resolve().parents[1]
CODEX_FALLBACKS = ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
                   str(Path.home() / ".codex/plugins/.plugin-appserver/codex-cli/bin/codex"))
CLAUDE_FALLBACKS = (str(Path.home() / ".local/bin/claude"), "/opt/homebrew/bin/claude")
# Explicit: the user's ~/.claude/settings.json default can name a model the CLI login cannot use (fable[1m]);
# `claude -p` then hangs until the timeout instead of failing.
CLAUDE_DEFAULT = "opus"
# Only what a CLI needs to find its own login; everything else (API keys, parent agent sessions,
# proxy base URLs) is dropped on purpose.
ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL")
PATH = ":".join(("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/opt/homebrew/bin",
                 str(Path.home() / ".local/bin")))
MAX_PARALLEL = 2
_slots = asyncio.Semaphore(MAX_PARALLEL)


class BrainRequest(BaseModel):
    provider: str = Field(pattern="^(claude|codex)$")
    prompt: str = Field(min_length=1, max_length=200_000)
    # JSON Schema of the answer; without it the answer is plain text.
    schema_: dict | None = Field(default=None, alias="schema")
    read_repo: bool = True
    model: str = Field(default="", max_length=80)
    effort: str = Field(default="", pattern="^(|low|medium|high|xhigh|max)$")
    timeout_s: int = Field(default=600, ge=10, le=3600)
    # Pictures the agent must look at (absolute paths, e.g. reference previews or video frames).
    images: list[str] = Field(default_factory=list, max_length=30)


    @model_validator(mode="after")
    def supported_effort(self):
        if self.provider == "claude" and self.effort in {"xhigh", "max"}:
            raise ValueError("Claude поддерживает effort low, medium или high")
        return self


class BrainError(RuntimeError):
    pass


def executable(provider: str) -> str | None:
    name, fallbacks = ("claude", CLAUDE_FALLBACKS) if provider == "claude" else ("codex", CODEX_FALLBACKS)
    found = shutil.which(name, path=PATH)
    if found:
        return found
    return next((path for path in fallbacks if os.access(path, os.X_OK)), None)


def clean_env() -> dict:
    from .agent_accounts import env_overrides  # the extra subscription chosen in the Studio, if any
    env = {key: os.environ[key] for key in ENV_KEEP if key in os.environ}
    env.update(env_overrides())
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = PATH
    env.setdefault("LANG", "en_US.UTF-8")
    return env


def claude_command(exe: str, req: BrainRequest) -> list[str]:
    cmd = [exe, "-p", req.prompt, "--output-format", "json", "--no-session-persistence",
           "--tools", "Read,Glob,Grep" if req.read_repo else ""]
    if req.read_repo:
        cmd += ["--allowedTools", "Read", "Glob", "Grep", "--permission-mode", "dontAsk"]
    if not req.read_repo:
        # Payload-only helpers need no inherited integrations or their tool schemas.
        cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    if req.effort:
        cmd += ["--effort", req.effort]
    if req.schema_ is not None:
        cmd += ["--json-schema", json.dumps(req.schema_)]
    if req.images:  # Claude opens the pictures itself with Read: allow their folders
        if not req.read_repo:
            cmd += ["--tools", "Read", "--allowedTools", "Read", "--permission-mode", "dontAsk"]
        for folder in sorted({str(Path(path).parent) for path in req.images}):
            cmd += ["--add-dir", folder]
    return cmd + ["--model", req.model or CLAUDE_DEFAULT]


def codex_command(exe: str, req: BrainRequest, work: Path) -> list[str]:
    cmd = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
           "--color", "never", "--json", "-C", str(REPO if req.read_repo else work),
           "-o", str(work / "answer.txt")]
    if req.schema_ is not None:
        (work / "schema.json").write_text(json.dumps(req.schema_))
        cmd += ["--output-schema", str(work / "schema.json")]
    if req.model:
        cmd += ["-m", req.model]
    if req.effort:
        cmd += ["-c", f'model_reasoning_effort="{req.effort}"']
    return cmd + [f"--image={path}" for path in req.images] + [req.prompt]


def parse_claude(stdout: str) -> tuple[str, object]:
    try:
        result = json.loads(stdout)
    except ValueError as exc:
        raise BrainError(f"Claude вернул не JSON: {stdout[:300]}") from exc
    if result.get("is_error") or result.get("subtype") != "success":
        raise BrainError(_claude_hint(str(result.get("result") or result.get("subtype") or "ошибка Claude")))
    return str(result.get("result") or ""), result.get("structured_output")


def _claude_hint(message: str) -> str:
    if "401" in message or "revoked" in message or "authenticat" in message.lower():
        return "Claude Code не вошёл в подписку: в Терминале выполни `claude auth login` и повтори. " + message[:200]
    return message[:500]


def parse_codex(answer: str, has_schema: bool) -> tuple[str, object]:
    text = answer.strip()
    if not has_schema:
        return text, None
    try:
        return text, json.loads(text)
    except ValueError as exc:
        raise BrainError(f"Codex вернул ответ не по схеме: {text[:300]}") from exc


def reported_usage(provider: str, stdout: str) -> dict | None:
    """Only CLI-reported counts; missing telemetry is unknown, never zero."""
    try:
        if provider == "claude":
            usage = json.loads(stdout).get("usage")
        else:
            events = []
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "turn.completed":
                    events.append(event)
            usage = events[-1].get("usage") if events else None
        if not isinstance(usage, dict):
            return None
        counts = {key: value for key, value in usage.items() if key in {
            "input_tokens", "output_tokens", "cached_input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}
                  and type(value) is int and value >= 0}
        return counts or None
    except (ValueError, AttributeError):
        return None


async def _run(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(cwd), env=clean_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def ask(req: BrainRequest) -> dict:
    exe = executable(req.provider)
    if not exe:
        raise BrainError("Не найден " + ("Claude Code (`claude`)" if req.provider == "claude"
                                         else "Codex: установи ChatGPT Desktop или Codex CLI"))
    started = time.monotonic()
    async with _slots:
        with tempfile.TemporaryDirectory(prefix="h3-brain-") as tmp:
            work = Path(tmp)
            if req.provider == "claude":
                cwd = REPO if req.read_repo else work
                code, out, err = await _run(claude_command(exe, req), cwd, req.timeout_s)
                if code != 0 and not out.strip():
                    raise BrainError(_claude_hint(err.strip() or f"claude exit {code}"))
                text, data = parse_claude(out)
            else:
                code, out, err = await _run(codex_command(exe, req, work), work, req.timeout_s)
                answer = work / "answer.txt"
                if code != 0 or not answer.is_file():
                    tail = (err.strip() or out.strip())[-400:]
                    if "login" in tail.lower() or "401" in tail:
                        tail = "Codex не вошёл в ChatGPT: выполни `codex login`. " + tail
                    raise BrainError(tail or f"codex exit {code}")
                text, data = parse_codex(answer.read_text(), req.schema_ is not None)
    return {"provider": req.provider, "text": text, "data": data,
            "usage": reported_usage(req.provider, out),
            "input_summary": {"prompt_characters": len(req.prompt), "images": len(req.images), "read_repo": req.read_repo},
            "seconds": round(time.monotonic() - started, 1)}


async def status() -> dict:
    result = {}
    for provider in ("claude", "codex"):
        exe = executable(provider)
        entry = {"installed": bool(exe), "path": exe or ""}
        if exe:
            args = ["auth", "status"] if provider == "claude" else ["login", "status"]
            try:
                code, out, err = await _run([exe, *args], Path.home(), 20)
                entry["login"] = (out or err).strip()[:300]
                entry["logged_in"] = code == 0 and "not logged" not in (out + err).lower()
            except asyncio.TimeoutError:
                entry["logged_in"] = False
        result[provider] = entry
    return result


def make_router() -> APIRouter:
    router = APIRouter(prefix="/api/brain", tags=["brain"])

    @router.get("/status")
    async def brain_status() -> dict:
        return await status()

    @router.post("/ask")
    async def brain_ask(payload: BrainRequest) -> dict:
        try:
            return await ask(payload)
        except asyncio.TimeoutError as exc:
            raise HTTPException(504, f"{payload.provider} не ответил за {payload.timeout_s} с") from exc
        except BrainError as exc:
            raise HTTPException(502, str(exc)) from exc

    return router
