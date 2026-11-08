#!/usr/bin/env python3
"""Install/check only Studio–ComfyUI integration files, without weights or service restarts."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.studio_extensions import extension_problems, install_extensions, runtime_problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comfy', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true', help='Copy files with backups; never restart ComfyUI')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--url', help='Also check loaded ComfyUI HTTP handlers (read-only)')
    args = parser.parse_args()
    if not args.comfy.is_dir():
        parser.error('--comfy must point to an existing ComfyUI directory')
    try:
        if args.apply:
            backup = args.backup or args.comfy.parent / 'backup' / (
                'studio-extensions-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
            install_extensions(args.source, args.comfy, backup)
            print(f'Files installed; backups when needed: {backup}. Running process was not reloaded.')
        problems = extension_problems(args.source, args.comfy)
        if args.url:
            def status(path):
                try:
                    with urllib.request.urlopen(args.url.rstrip('/') + path, timeout=5) as response:
                        return response.status
                except urllib.error.HTTPError as exc:
                    return exc.code
            problems.extend(runtime_problems(status))
        for problem in problems:
            print('FAIL', problem)
        if not problems:
            print('OK Studio–ComfyUI extension contract' + (' (files and loaded routes)' if args.url else ' (files only)'))
        return int(bool(problems))
    except (ValueError, OSError) as exc:
        print(f'FAIL {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
