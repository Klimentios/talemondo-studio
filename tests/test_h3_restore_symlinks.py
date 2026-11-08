from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "infra" / "h3_restore_symlinks.py"
SPEC = importlib.util.spec_from_file_location("h3_restore_symlinks", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def make_root(tmp_path: Path) -> Path:
    root = tmp_path / "h3"
    (root / "ComfyUI/models/loras").mkdir(parents=True)
    (root / "LoRA_Library").mkdir()
    (root / "LoRA_Library/actor.safetensors").write_bytes(b"weights")
    return root


def test_restores_relative_snapshot_paths_and_is_idempotent(tmp_path: Path):
    root = make_root(tmp_path)
    manifest = tmp_path / "symlinks.tsv"
    manifest.write_text(
        "ComfyUI/models/loras/actor.safetensors\tLoRA_Library/actor.safetensors\n",
        encoding="utf-8",
    )

    assert MODULE.restore(manifest, root) == 1
    link = root / "ComfyUI/models/loras/actor.safetensors"
    assert link.is_symlink()
    assert link.resolve() == (root / "LoRA_Library/actor.safetensors").resolve()
    assert MODULE.restore(manifest, root) == 0


def test_restores_readlink_style_relative_target_and_legacy_absolute_link(tmp_path: Path):
    root = make_root(tmp_path)
    manifest = tmp_path / "symlinks.tsv"
    link = "/workspace/h3/ComfyUI/models/loras/actor.safetensors"
    manifest.write_text(
        f"{link}\t../../../LoRA_Library/actor.safetensors\n", encoding="utf-8"
    )

    assert MODULE.restore(manifest, root) == 1


@pytest.mark.parametrize(
    "row",
    [
        "ComfyUI/models/../escape\tLoRA_Library/actor.safetensors",
        "ComfyUI/models/loras/link\t/etc/passwd",
        "ComfyUI/models/loras/link\tLoRA_Library/missing.safetensors",
    ],
)
def test_rejects_unsafe_or_missing_targets(tmp_path: Path, row: str):
    root = make_root(tmp_path)
    manifest = tmp_path / "symlinks.tsv"
    manifest.write_text(row + "\n", encoding="utf-8")

    with pytest.raises((ValueError, FileNotFoundError)):
        MODULE.restore(manifest, root)


def test_does_not_overwrite_an_existing_path(tmp_path: Path):
    root = make_root(tmp_path)
    link = root / "ComfyUI/models/loras/actor.safetensors"
    link.write_bytes(b"user data")
    manifest = tmp_path / "symlinks.tsv"
    manifest.write_text(
        "ComfyUI/models/loras/actor.safetensors\tLoRA_Library/actor.safetensors\n",
        encoding="utf-8",
    )

    with pytest.raises(FileExistsError):
        MODULE.restore(manifest, root)
    assert link.read_bytes() == b"user data"
