"""First run («Первый запуск», setup.html): what this Mac has, what is missing, one fix per line.

Writes: PUT /api/setup/settings (GPU/ComfyUI addresses → <data>/settings.json, read by config.py at start when the
LaunchAgent sets no H3_GPU_API_URL / H3_COMFY_URL); POST /api/setup/ssh-key (creates ~/.ssh/id_ed25519 when absent);
PUT /api/setup/secret (Hugging Face token → macOS Keychain h3-hf-token, Vast key → Keychain, MiniMax key → <data>);
POST /api/setup/agents/{claude|codex} (runs that CLI's «mcp add h3-studio» once). Secrets are never returned.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

LOCAL_URL = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?")


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gpu_url: str = Field(default="", max_length=200)
    comfy_url: str = Field(default="", max_length=200)

    @field_validator("gpu_url", "comfy_url")
    @classmethod
    def _url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not LOCAL_URL.fullmatch(value):
            raise ValueError("Адрес вида http://127.0.0.1:18190 (без пути)")
        return value


def saved_settings(data_root: Path) -> dict:
    try:
        data = json.loads((data_root / "settings.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data_root: Path, settings: SettingsIn) -> dict:
    data = {**saved_settings(data_root), **settings.model_dump()}
    path = data_root / "settings.json"
    tmp = path.with_name(".settings.json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return data


def _mcp_registered() -> dict:
    found = {}
    for agent, path in (("claude", Path.home() / ".claude.json"), ("codex", Path.home() / ".codex" / "config.toml")):
        try:
            found[agent] = "h3-studio" in path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            found[agent] = False
    return found


async def _get(url: str) -> int | None:
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            return (await client.get(url)).status_code
    except httpx.HTTPError:
        return None


async def status(ctx) -> dict:
    root, data = Path(ctx.root), Path(ctx.data_root)
    usage = shutil.disk_usage(data)
    mcp = root / "scripts" / "h3_studio_mcp.py"
    registered = _mcp_registered()
    gpu = await _get(f"{ctx.gpu_url}/api/health") if ctx.gpu_url else None
    comfy = await _get(f"{ctx.comfy_url}/queue") if ctx.comfy_url else None
    bridge = await _get(f"{ctx.comfy_url}/h3/studio/workflows") if comfy == 200 else None
    server = ctx.compute_manager.current_server()
    ssh_key = (Path.home() / ".ssh" / "id_ed25519").is_file()
    hf_token = _keychain_has(ctx, "hf")
    cloud = getattr(getattr(ctx, "gpu_transport", None), "cloud", None)
    minimax_key = bool(cloud and cloud.configured())
    env = {"gpu_url": bool(os.environ.get("H3_GPU_API_URL")), "comfy_url": bool(os.environ.get("H3_COMFY_URL"))}

    def item(key, label, ok, detail, fix=""):
        return {"key": key, "label": label, "ok": ok, "detail": detail, "fix": "" if ok else fix}

    checks = [
        item("python", "Python", sys.version_info >= (3, 11), sys.version.split()[0], "Нужен Python 3.11+"),
        item("data", "Папка данных", os.access(data, os.W_OK) and usage.free > 5 * 2**30,
             f"{data} · свободно {usage.free / 2**30:.0f} ГБ", "Освободите место (нужно больше 5 ГБ) или проверьте права"),
        item("ffmpeg", "ffmpeg", bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
             shutil.which("ffmpeg") or "не найден", "brew install ffmpeg"),
        item("gpu", "GPU API", gpu == 200, ctx.gpu_url or "адрес не задан",
             "Поднимите сервер («GPU и модели») и задайте адрес ниже" if not ctx.gpu_url else
             "Сервер не отвечает: проверьте туннель («GPU и модели» → Проверить)"),
        item("comfy", "ComfyUI", comfy == 200, ctx.comfy_url or "адрес не задан",
             "Задайте адрес ComfyUI ниже" if not ctx.comfy_url else "ComfyUI не отвечает: туннель или сервер"),
        item("bridge", "Мост Studio в ComfyUI", bridge == 200,
             {200: "актуален", 404: "старая версия"}.get(bridge, "не проверен (нет ComfyUI)"),
             "scripts/install_h3_studio_extensions.py --apply на GPU и перезапуск ComfyUI"),
        item("server", "Текущий GPU-сервер", bool(server.get("configured")),
             f"{server['host']}:{server['port']}" if server.get("configured") else "не выбран",
             "«GPU и модели»: арендуйте или подключите сервер — установка одной кнопкой"),
        item("ssh", "SSH-ключ для сервера", ssh_key, "~/.ssh/id_ed25519" if ssh_key else "нет",
             "Кнопка «Создать SSH-ключ» ниже, затем добавьте открытый ключ в аккаунт провайдера GPU"),
        item("agents", "Агенты (MCP h3-studio)", registered["claude"] or registered["codex"],
             ", ".join(name for name, ok in registered.items() if ok) or "не подключены",
             "Выполните одну из команд подключения ниже"),
    ]
    python = sys.executable
    return {
        "checks": checks, "ready": all(check["ok"] for check in checks),
        "keys": {"ssh_public": _public_key(), "hf_token": hf_token, "minimax_api": minimax_key,
                 "vast": _keychain_has(ctx, "vast")},
        "settings": {"gpu_url": ctx.gpu_url, "comfy_url": ctx.comfy_url, "saved": saved_settings(data),
                     "from_launch_agent": env},
        "agents": {
            "claude": f'claude mcp add --scope user h3-studio -- "{python}" "{mcp}"',
            "codex": f'codex mcp add h3-studio -- "{python}" "{mcp}"',
            "registered": registered,
        },
        "source": str(root), "data": str(data),
    }


KEYCHAIN_ITEMS = {"hf": ("h3-hf-token", "h3"), "vast": ("com.studio.local-studio.compute", "vast")}


def _keychain_has(ctx, provider: str) -> bool:
    """Presence only (no -w: the secret is never read); `security` answers in milliseconds, the Swift helper
    compiles on every call."""
    service, account = KEYCHAIN_ITEMS[provider]
    try:
        return subprocess.run(["security", "find-generic-password", "-s", service, "-a", account],
                              capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _public_key() -> str:
    path = Path.home() / ".ssh" / "id_ed25519.pub"
    return path.read_text().strip() if path.is_file() else ""


def create_ssh_key() -> str:
    """~/.ssh/id_ed25519 without a passphrase (the deploy scripts run unattended); an existing key is kept."""
    private = Path.home() / ".ssh" / "id_ed25519"
    if not private.is_file():
        private.parent.mkdir(mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "h3-studio", "-f", str(private)],
                       check=True, capture_output=True, timeout=30)
    return _public_key()


class SecretIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(hf_token|vast|minimax_api)$")
    value: str = Field(min_length=8, max_length=4096)


def save_secret(ctx, kind: str, value: str) -> None:
    value = value.strip()
    if kind == "hf_token":
        if not re.fullmatch(r"hf_[A-Za-z0-9]{20,}", value):
            raise ValueError("Токен Hugging Face начинается с hf_")
        ctx.compute_manager.keychain.set("hf", value)
    elif kind == "vast":
        ctx.compute_manager.keychain.set("vast", value)
    else:
        path = Path(ctx.data_root) / "minimax-api-key"
        path.write_text(value + "\n")
        path.chmod(0o600)


def connect_agent(ctx, agent: str) -> dict:
    """Run «claude mcp add …» or «codex mcp add …» when that CLI is installed."""
    cli = shutil.which(agent)
    if not cli:
        raise ValueError(f"{'Claude Code' if agent == 'claude' else 'Codex'} не установлен на этом Mac")
    mcp = str(Path(ctx.root) / "scripts" / "h3_studio_mcp.py")
    args = [cli, "mcp", "add", "--scope", "user", "h3-studio", "--", sys.executable, mcp] if agent == "claude" else \
        [cli, "mcp", "add", "h3-studio", "--", sys.executable, mcp]
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 and "already exists" not in (result.stderr + result.stdout):
        raise ValueError((result.stderr or result.stdout).strip()[:300] or "Команда подключения завершилась с ошибкой")
    return {"connected": True}


def make_router(ctx):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.get("/api/setup/status")
    async def get_status():
        return await status(ctx)

    @router.put("/api/setup/settings")
    async def put_settings(settings: SettingsIn):
        saved = save_settings(Path(ctx.data_root), settings)
        return {"saved": saved, "restart_needed": True,
                "note": "Адреса применятся после перезапуска Studio. Переменные LaunchAgent важнее файла."}

    @router.post("/api/setup/ssh-key")
    async def post_ssh_key():
        try:
            return {"public_key": create_ssh_key()}
        except (OSError, subprocess.SubprocessError) as error:
            raise HTTPException(500, f"Не удалось создать ключ: {error}") from None

    @router.put("/api/setup/secret")
    async def put_secret(payload: SecretIn):
        try:
            save_secret(ctx, payload.kind, payload.value)
        except Exception as error:  # Keychain errors are sanitized; never echo the secret
            raise HTTPException(422, str(error) if isinstance(error, ValueError) else "Связка ключей macOS недоступна") from None
        return {"saved": payload.kind}

    @router.post("/api/setup/agents/{agent}")
    async def post_agent(agent: str):
        if agent not in {"claude", "codex"}:
            raise HTTPException(404, "Неизвестный агент")
        try:
            return connect_agent(ctx, agent)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            raise HTTPException(422, str(error)) from None

    return router
