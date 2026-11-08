"""Portable Studio/ComfyUI extensions use the same files on CPU and GPU hosts."""
import subprocess
import sys
import tarfile
from pathlib import Path

from app.studio_extensions import extension_problems, install_extensions, runtime_problems

ROOT = Path(__file__).resolve().parents[1]


def test_install_and_check_preserve_user_files_and_detect_source_drift(tmp_path):
    comfy = tmp_path / 'ComfyUI'
    custom = comfy / 'custom_nodes/h3-studio-bridge'
    custom.mkdir(parents=True)
    (custom / 'user-note.json').write_text('{}')
    assert extension_problems(ROOT, comfy)
    install_extensions(ROOT, comfy, tmp_path / 'backup')
    assert extension_problems(ROOT, comfy) == []
    assert (custom / 'user-note.json').is_file()
    installed = custom / '__init__.py'
    original = installed.read_bytes()
    installed.write_bytes(b'# old')
    assert any('h3-studio-bridge/__init__.py' in p for p in extension_problems(ROOT, comfy))
    install_extensions(ROOT, comfy, tmp_path / 'backup2')
    assert (tmp_path / 'backup2/h3-studio-bridge/__init__.py').read_bytes() == b'# old'
    assert installed.read_bytes() == original


def test_runtime_check_requires_bridge_and_observer_handlers():
    requested = []
    def fetch(path):
        requested.append(path)
        return 404 if '/execution/' in path else 400
    assert len(runtime_problems(fetch)) == 1
    assert all('not-a-uuid' in path for path in requested)
    assert runtime_problems(lambda _: 400) == []


def test_overlay_bundle_can_install_extensions_without_checkout_or_models(tmp_path):
    archive = tmp_path / 'overlay.tar.gz'
    result = subprocess.run(['bash', 'scripts/h3_overlay_bundle.sh', str(archive)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    bundle = tmp_path / 'bundle'
    with tarfile.open(archive) as tf:
        tf.extractall(bundle, filter='data')
    comfy = tmp_path / 'fresh/ComfyUI'
    comfy.mkdir(parents=True)
    command = [sys.executable, str(bundle / 'scripts/install_h3_studio_extensions.py'), '--comfy', str(comfy)]
    check = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert check.returncode == 1  # read-only by default
    apply = subprocess.run([*command, '--apply'], capture_output=True, text=True, timeout=10)
    assert apply.returncode == 0, apply.stderr
    assert subprocess.run(command, capture_output=True, text=True, timeout=10).returncode == 0
    assert (bundle / 'scripts/h3_attest_weights.py').is_file()
