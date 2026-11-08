#!/usr/bin/env python3
"""Create a verified, self-contained local backup of Mac Studio data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DATA = Path(os.environ.get("H3_MAC_DATA_DIR", str(Path.home() / "Library/Application Support/H3 Studio")))
CONTENTS = ("assets", "results", "editor_exports", "editor_projects", "model-staging", "code-backups",
            "agent_chats", "runs", "scene_runs", "franchise_profiles")
DATABASES = ("studio.sqlite3", "jobs.sqlite3", "franchises.sqlite3", "analytics.sqlite3",
             "compute.sqlite3", "context-ir.sqlite3", "ui-command-journal.sqlite3")
FILES = ("draft.json",)
CONFIGS = (Path.home() / "Library/LaunchAgents/com.minimax.h3-mac-studio.plist",
           Path.home() / "Library/Application Support/MiniMax H3/tunnel.sh")
ACTIVE = {"queued_local", "uploading", "queued_gpu", "running", "downloading",
          "needs_reconciliation", "stopping"}


def _quiet_queue():
    for endpoint in ("/api/jobs", "/api/audio/jobs", "/api/upscale/jobs"):
        with urllib.request.urlopen(f"http://127.0.0.1:18187{endpoint}", timeout=8) as response:
            jobs = json.load(response)
        active = [job.get("job_id", job.get("upscale_id", "?")) for job in jobs
                  if job.get("state") in ACTIVE]
        if active:
            raise RuntimeError(f"Очередь {endpoint} не пуста: {len(active)} активных задач")


def _copy_database(source: Path, target: Path):
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as current:
        with sqlite3.connect(target) as backup:
            current.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Проверка SQLite не прошла: {source.name}")


def _manifest(root: Path):
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Символьная ссылка в копии: {path.relative_to(root)}")
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        entries.append({"path": str(path.relative_to(root)), "bytes": path.stat().st_size,
                        "sha256": digest.hexdigest()})
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path,
                        default=Path.home() / "Desktop/H3 Studio Backups")
    args = parser.parse_args()
    if not DATA.is_dir():
        parser.error(f"Данные Studio не найдены: {DATA}")
    try:
        _quiet_queue()
        destination = args.destination.expanduser().resolve()
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        if destination == DATA or destination.is_relative_to(DATA):
            raise RuntimeError("Каталог копий не должен находиться внутри данных Studio")
        sources = [DATA / name for name in CONTENTS if (DATA / name).exists()]
        sources += [DATA / name for name in DATABASES + FILES if (DATA / name).exists()]
        estimate = sum(path.stat().st_size for source in sources for path in
                       ([source] if source.is_file() else source.rglob("*")) if path.is_file())
        if shutil.disk_usage(destination).free < estimate * 1.2:
            raise RuntimeError("На диске недостаточно места для копии и проверки")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final = destination / f"H3-Studio-{stamp}"
        if final.exists():
            raise RuntimeError(f"Копия уже существует: {final}")
        with tempfile.TemporaryDirectory(prefix=".h3-studio-", dir=destination) as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            for name in CONTENTS:
                source = DATA / name
                if source.is_dir():
                    shutil.copytree(source, root / name, symlinks=True)
            for name in DATABASES:
                source = DATA / name
                if source.is_file():
                    _copy_database(source, root / name)
            for name in FILES:
                source = DATA / name
                if source.is_file():
                    shutil.copy2(source, root / name)
            config_dir = root / "config"
            config_dir.mkdir(mode=0o700)
            for source in CONFIGS:
                if source.is_file():
                    target = config_dir / source.name
                    shutil.copy2(source, target)
                    target.chmod(0o600)
            manifest = _manifest(root)
            (root / "manifest.json").write_text(json.dumps({"created_utc": stamp,
                "source": str(DATA), "files": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
            root.rename(final)
        print(f"BACKUP_OK {final} ({len(manifest)} файлов, {sum(x['bytes'] for x in manifest)} байт)")
    except (OSError, RuntimeError, ValueError, urllib.error.URLError) as error:
        print(f"BACKUP_FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
