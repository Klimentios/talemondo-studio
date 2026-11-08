#!/usr/bin/env python3
"""Restore LoRA links from weights-snapshot metadata without overwriting files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath


def _relative_path(value: str, root: Path, allowed_prefix: str) -> Path:
    """Accept snapshot-relative paths and old absolute /workspace/h3 paths."""
    if "\x00" in value or "\n" in value or "\r" in value or "\t" in value:
        raise ValueError("path contains a control character")
    prefix = "/workspace/h3/"
    if value.startswith(prefix):
        value = value[len(prefix) :]
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not value.startswith(allowed_prefix):
        raise ValueError(f"path is outside {allowed_prefix}: {value}")
    return root.joinpath(*path.parts)


def _link_target(value: str, root: Path, link: Path) -> Path:
    """Handle root-relative targets and the relative targets emitted by readlink."""
    if value.startswith("/workspace/h3/") or value.startswith("LoRA_Library/"):
        return _relative_path(value, root, "LoRA_Library/")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", "."} for part in path.parts):
        raise ValueError(f"invalid relative LoRA target: {value}")
    return link.parent.joinpath(*path.parts)


def restore(manifest: Path, root: Path) -> int:
    root = root.resolve(strict=True)
    restored = 0
    with manifest.open(encoding="utf-8") as rows:
        for number, line in enumerate(rows, 1):
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 2:
                raise ValueError(f"{manifest}:{number}: expected link<TAB>target")
            link = _relative_path(fields[0], root, "ComfyUI/models/")
            target = _link_target(fields[1], root, link)
            resolved_target = target.resolve(strict=True)
            library = (root / "LoRA_Library").resolve(strict=True)
            if not resolved_target.is_file() or not resolved_target.is_relative_to(library):
                raise ValueError(f"{manifest}:{number}: target is not a file inside LoRA_Library")
            link.parent.mkdir(parents=True, exist_ok=True)
            models = (root / "ComfyUI/models").resolve(strict=True)
            if not link.parent.resolve(strict=True).is_relative_to(models):
                raise ValueError(f"{manifest}:{number}: link parent escapes ComfyUI/models")
            if link.is_symlink():
                if link.resolve(strict=True) == resolved_target:
                    continue
                raise FileExistsError(f"refusing to replace a different symlink: {link}")
            if link.exists():
                raise FileExistsError(f"refusing to replace an existing path: {link}")
            os.symlink(resolved_target, link)
            restored += 1
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("root", type=Path, nargs="?", default=Path("/workspace/h3"))
    args = parser.parse_args()
    try:
        count = restore(args.manifest, args.root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Restored LoRA symlinks: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
