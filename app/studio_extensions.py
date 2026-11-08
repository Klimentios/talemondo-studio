"""One portable file/route contract for the Studio–ComfyUI integration.

These extensions add graph exchange and provenance, not model loaders or weights.
Installing files never reloads a running ComfyUI process.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

# (repository path, path relative to ComfyUI/custom_nodes)
EXTENSION_FILES = (
    ('app/comfy_nodes/h3_studio_bridge/__init__.py', 'h3-studio-bridge/__init__.py'),
    ('app/comfy_nodes/h3_studio_bridge/web/studio_bridge.js', 'h3-studio-bridge/web/studio_bridge.js'),
    ('app/comfy_nodes/h3_execution_context/__init__.py', 'h3-execution-context/__init__.py'),
    ('app/comfy_nodes/h3_execution_context/runtime.py', 'h3-execution-context/runtime.py'),
    ('app/model_inventory.py', 'h3-execution-context/inventory.py'),
)
RUNTIME_PROBES = (
    '/h3/studio/graph/not-a-uuid',
    '/h3/studio/edited?source=not-a-uuid',
    '/h3/studio/execution/not-a-uuid',
)


def _destination(comfy: Path, relative: str) -> Path:
    target = comfy / 'custom_nodes' / relative
    if not target.resolve().is_relative_to(comfy.resolve()):
        raise ValueError(f'Extension path escapes ComfyUI: {relative}')
    return target


def extension_problems(source: Path, comfy: Path) -> list[str]:
    problems = []
    for origin, relative in EXTENSION_FILES:
        expected = source / origin
        actual = _destination(comfy, relative)
        if not expected.is_file():
            problems.append(f'Missing source: {origin}')
        elif not actual.is_file():
            problems.append(f'Not installed: {relative}')
        elif expected.read_bytes() != actual.read_bytes():
            problems.append(f'Outdated: {relative}')
    return problems


def install_extensions(source: Path, comfy: Path, backup: Path) -> None:
    prepared = []
    for origin, relative in EXTENSION_FILES:
        data = (source / origin).read_bytes()
        if relative.endswith('.py'):
            compile(data, origin, 'exec')
        prepared.append((relative, _destination(comfy, relative), data))
    for relative, target, data in prepared:
        if target.is_file() and target.read_bytes() == data:
            continue
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                tmp = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            tmp.chmod(0o644)
            tmp.replace(target)
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)


def runtime_problems(status: Callable[[str], int]) -> list[str]:
    problems = []
    for path in RUNTIME_PROBES:
        expected = 400
        try:
            received = status(path)
            if received != expected:
                problems.append(f'{path}: HTTP {received}, expected {expected}')
        except Exception as exc:
            problems.append(f'{path}: {exc}')
    return problems
