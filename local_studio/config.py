"""Process setup and settings read once at start: PATH, access-log filter, data folder, GPU addresses."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

# launchd starts Studio with PATH=/usr/bin:/bin, so Homebrew ffmpeg/ffprobe
# (thumbnails, editor export) were reported as missing.
os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", "")] + [p for p in ("/opt/homebrew/bin", "/usr/local/bin")
                                    if p not in os.environ.get("PATH", "").split(os.pathsep)])


class _QuietPolling(logging.Filter):
    """Drop the UI's successful 1-3 s polling lines (≈95% of a 32 MB service.log); keep errors."""

    _POLLED = re.compile(r'"GET /api/(?:jobs|queue|health|audio/capabilities|upscale|audio/jobs|gpu/activity|side-jobs)'
                         r'(?:/[^/" ]+/(?:preview|thumbnail))?(?:\?[^" ]*)? HTTP/[0-9.]+" (?:200|304|404)')

    def filter(self, record: logging.LogRecord) -> bool:
        return not self._POLLED.search(record.getMessage())


logging.getLogger("uvicorn.access").addFilter(_QuietPolling())


def trim_log(path: Path, limit: int = 20 * 1024 * 1024, keep: int = 4000) -> None:
    """launchd keeps StandardOutPath open, so trim in place (a rename would keep growing)."""
    try:
        if path.stat().st_size <= limit:
            return
        tail = path.read_bytes()[-keep * 200:].split(b"\n", 1)[-1]
        path.with_suffix(path.suffix + ".1").write_bytes(tail)
        with path.open("r+b") as handle:
            handle.truncate(0)
    except OSError:
        pass


ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("H3_MAC_DATA_DIR", str(Path.home() / "Library/Application Support/H3 Studio")))


def _saved(key: str) -> str:
    """Addresses saved on «Первый запуск» (setup.html); the LaunchAgent's environment wins over them."""
    import json
    try:
        value = json.loads((DATA_ROOT / "settings.json").read_text(encoding="utf-8")).get(key, "")
        return value if isinstance(value, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


GPU_URL = (os.environ.get("H3_GPU_API_URL", "") or _saved("gpu_url")).rstrip("/")
# ComfyUI queue of the same GPU: the SSH tunnel forwards it next to the GPU API (scripts/tunnel.sh).
COMFY_URL = (os.environ.get("H3_COMFY_URL", "") or _saved("comfy_url")).rstrip("/") or (
    "http://127.0.0.1:18188" if GPU_URL == "http://127.0.0.1:18189" else "")
