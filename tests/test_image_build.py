"""Offline image-build mode (H3_IMAGE_BUILD=1) of the GPU installers: stub runs of the flag gates.

Default behaviour (flag unset) must stay exactly as before; with the flag the installers skip GPU and
driver checks, record weights instead of downloading them and never touch supervisor.
"""
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GPU_ENV = ROOT / "vendor" / "minimax-h3-local-studio" / "scripts" / "h3_gpu_env.sh"
BASE = ROOT / "vendor" / "minimax-h3-local-studio" / "scripts" / "deploy_h3_local_studio.sh"
OVERLAY = ROOT / "scripts" / "install_h3_h200_overlay.sh"
QWEN = ROOT / "scripts" / "install_h3_qwen_director.sh"
NEXT = ROOT / "scripts" / "install_h3_scenarist_next.sh"
ACTING = ROOT / "scripts" / "install_h3_acting.sh"
BASH = shutil.which("bash") or "/bin/bash"

# Logs every call as one line "name arg1 arg2 ..." to $H3_STUB_LOG and succeeds.
LOGGING_STUB = '#!/bin/sh\necho "$(basename "$0") $*" >> "$H3_STUB_LOG"\n'


def stub(bindir: Path, name: str, body: str = LOGGING_STUB) -> None:
    path = bindir / name
    path.write_text(body)
    path.chmod(0o755)


def function_source(script: Path, name: str) -> str:
    """The text of one shell function, so it can be exercised alone."""
    match = re.search(rf"^{name}\(\) {{\n.*?^}}\n", script.read_text(), re.S | re.M)
    assert match, f"{name} not found in {script}"
    return match.group(0)


class StubBox:
    """Temporary H3 root + PATH of stubs (nvidia-smi absent unless added)."""

    def __init__(self, tmp: str):
        self.tmp = Path(tmp)
        self.root = self.tmp / "h3"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.root.mkdir()
        self.log = self.tmp / "calls.log"
        self.log.touch()
        self.weights = self.tmp / "expected-weights.tsv"
        # GNU df -PB1 / stat -c are not on macOS: a fixed amount of free space, sizes via Python.
        stub(self.bin, "df", "#!/bin/sh\nprintf 'Filesystem 1-blocks Used Available\\nx 1 1 900000000000 1%% /\\n'\n")

    def env(self, image_build: bool, **extra) -> dict:
        env = {"PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin", "H3_ROOT": str(self.root),
               "H3_STUB_LOG": str(self.log), "H3_IMAGE_WEIGHTS": str(self.weights), "HOME": str(self.tmp)}
        if image_build:
            env["H3_IMAGE_BUILD"] = "1"
        env.update(extra)
        return env

    def run(self, args, image_build: bool, **extra):
        return subprocess.run([BASH, *args], env=self.env(image_build, **extra), capture_output=True,
                              text=True, timeout=60)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()

    def recorded(self) -> list[list[str]]:
        if not self.weights.exists():
            return []
        return [line.split("\t") for line in self.weights.read_text().splitlines()]


class GpuEnvTests(unittest.TestCase):
    def test_driver_check_needs_gpu_by_default_and_is_skipped_only_in_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            script = f'. "{GPU_ENV}"; h3_require_driver_cuda'
            default = box.run(["-c", script], image_build=False)
            self.assertEqual(default.returncode, 2)
            self.assertIn("Cannot detect the NVIDIA driver", default.stderr)
            image = box.run(["-c", script], image_build=True)
            self.assertEqual(image.returncode, 0, image.stderr)
            self.assertIn("driver check skipped", image.stdout)
            # Any other value than 1 is the default path.
            other = box.run(["-c", script], image_build=False, H3_IMAGE_BUILD="0")
            self.assertEqual(other.returncode, 2)

    def test_driver_check_default_unchanged_with_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            stub(box.bin, "nvidia-smi", "#!/bin/sh\necho '| NVIDIA-SMI 595.84  Driver Version: 595.84  CUDA Version: 13.2 |'\n")
            result = box.run(["-c", f'. "{GPU_ENV}"; h3_require_driver_cuda'], image_build=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "Driver CUDA 13.2 (>= 12.8): using pinned cu128 wheels\n")

    def test_record_weight_writes_relative_tsv_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            result = box.run(["-c", f'. "{GPU_ENV}"; h3_image_record_weight "$H3_ROOT/ComfyUI/models/vae/v.safetensors" 12 abc org/repo rev vae/v.safetensors'],
                             image_build=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(box.recorded(), [["ComfyUI/models/vae/v.safetensors", "12", "abc", "org/repo", "rev", "vae/v.safetensors"]])


class DownloadGateTests(unittest.TestCase):
    def fetch(self, box: StubBox, image_build: bool):
        func = function_source(OVERLAY, "fetch_checked")
        script = textwrap.dedent(f"""
            set -euo pipefail
            . "{GPU_ENV}"
            {func}
            fetch_checked Comfy-Org/SeedVR2 rev1 vae/x.safetensors "$H3_ROOT/ComfyUI/models/vae/x.safetensors" deadbeef
        """)
        return box.run(["-c", script], image_build=image_build)

    def test_overlay_fetch_downloads_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            stub(box.bin, "curl", '#!/bin/sh\necho "curl $*" >> "$H3_STUB_LOG"\nwhile [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n')
            stub(box.bin, "sha256sum")
            result = self.fetch(box, image_build=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            curl = [c for c in box.calls() if c.startswith("curl ")]
            self.assertEqual(len(curl), 1)
            self.assertIn("https://huggingface.co/Comfy-Org/SeedVR2/resolve/rev1/vae/x.safetensors", curl[0])
            self.assertTrue((box.root / "ComfyUI/models/vae/x.safetensors").exists())
            self.assertEqual(box.recorded(), [])

    def test_overlay_fetch_records_in_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            stub(box.bin, "curl")
            # A stale placeholder of the wrong hash must not stop the image build.
            (box.root / "ComfyUI/models/vae").mkdir(parents=True)
            (box.root / "ComfyUI/models/vae/x.safetensors").touch()
            result = self.fetch(box, image_build=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(box.calls(), [])
            self.assertEqual(box.recorded(), [["ComfyUI/models/vae/x.safetensors", "-", "deadbeef", "Comfy-Org/SeedVR2", "rev1", "vae/x.safetensors"]])

    def test_base_model_download_records_in_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = StubBox(tmp)
            stub(box.bin, "curl")
            stub(box.bin, "aria2c")
            func = function_source(BASE, "download_model")
            script = f'set -euo pipefail\n. "{GPU_ENV}"\ncomfy_repo="$H3_ROOT/ComfyUI"\n{func}\ndownload_model vae/minimax_h3_video_vae_fp16.safetensors 5207808496\n'
            result = box.run(["-c", script], image_build=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(box.calls(), [])
            self.assertEqual(box.recorded(), [["ComfyUI/models/vae/minimax_h3_video_vae_fp16.safetensors", "5207808496", "-",
                                               "Comfy-Org/MiniMax-H3", "main", "vae/minimax_h3_video_vae_fp16.safetensors"]])
            # Default: no HF config -> the original refusal, still before any download.
            default = box.run(["-c", script], image_build=False)
            self.assertEqual(default.returncode, 5)
            self.assertIn("Missing private Hugging Face authentication config", default.stderr)


class ScenaristNextTests(unittest.TestCase):
    def box_with_venv(self, tmp: str) -> StubBox:
        box = StubBox(tmp)
        venv_bin = box.root / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        # Base python: "-m venv DIR" makes a stub venv (python + uv); everything else is logged.
        stub(venv_bin, "python", textwrap.dedent("""\
            #!/bin/sh
            echo "python $*" >> "$H3_STUB_LOG"
            if [ "$1" = -m ] && [ "$2" = venv ]; then
              mkdir -p "$3/bin"
              for n in python uv vllm; do printf '#!/bin/sh\\necho "%s $*" >> "$H3_STUB_LOG"\\ncat >/dev/null\\n' "$n" > "$3/bin/$n"; chmod +x "$3/bin/$n"; done
            fi
        """))
        return box

    def test_default_needs_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box_with_venv(tmp)
            result = box.run([str(NEXT)], image_build=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Cannot detect the NVIDIA driver", result.stderr)

    def test_image_build_installs_venv_and_records_pinned_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box_with_venv(tmp)
            result = box.run([str(NEXT)], image_build=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            calls = box.calls()
            self.assertTrue(any("uv pip install" in c and "vllm==0.19.1" in c for c in calls), calls)
            # The CUDA/arch check script runs with the image-build marker, the download never runs.
            self.assertFalse(any("Qwen/Qwen3.8-27B-FP8" in c for c in calls), calls)
            manifest = [l.split() for l in (ROOT / "deploy/scenarist-models/qwen3.8-27b-fp8.sha256").read_text().splitlines()
                        if l.strip() and not l.startswith("#")]
            rows = box.recorded()
            self.assertEqual(len(rows), len(manifest))
            for (digest, name), row in zip(manifest, rows):
                self.assertEqual(row, [f"hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/blobs/{digest}", "-", digest,
                                       "Qwen/Qwen3.8-27B-FP8", "017b9c7af6b5689d5dd426a76e0bc077eb5ca20a", name])


class ActingTests(unittest.TestCase):
    def box(self, tmp: str) -> StubBox:
        box = StubBox(tmp)
        lp = box.root / "LivePortrait"
        (lp / ".git").mkdir(parents=True)
        ops = lp / "src/utils/dependencies/XPose/models/UniPose/ops"
        ops.mkdir(parents=True)
        (ops / "MultiScaleDeformableAttention.cpython-312-x86_64-linux-gnu.so").touch()
        (box.root / "api/app").mkdir(parents=True)
        (box.root / "api/app/main.py").write_text("app.include_router(acting_router)\n")
        venv_bin = box.root / "liveportrait-venv" / "bin"
        venv_bin.mkdir(parents=True)
        # The weights script (has arguments) runs for real; the import smoke (no arguments) is stubbed.
        stub(venv_bin, "python", textwrap.dedent(f"""\
            #!/bin/sh
            echo "venv-python $*" >> "$H3_STUB_LOG"
            if [ "$1" = - ] && [ $# -ge 2 ]; then exec "{shutil.which('python3')}" "$@"; fi
            cat >/dev/null
        """))
        manifest = ('[' + ','.join('{"type":"file","path":"%s","size":%d}' % (p, 5000 + i) for i, p in enumerate([
            "insightface/models/buffalo_l/2d106det.onnx", "insightface/models/buffalo_l/det_10g.onnx",
            "liveportrait/landmark.onnx", "liveportrait/retargeting_models/stitching_retargeting_module.pth",
            "liveportrait_animals/xpose.pth"] + [f"liveportrait/base_models/{n}.pth" for n in
            ("appearance_feature_extractor", "motion_extractor", "spade_generator", "warping_module")] +
            [f"liveportrait_animals/base_models_v1.1/{n}.pth" for n in
             ("appearance_feature_extractor", "motion_extractor", "spade_generator", "warping_module")])) + ']')
        stub(box.bin, "curl", f"#!/bin/sh\necho \"curl $*\" >> \"$H3_STUB_LOG\"\necho '{manifest}'\n")
        for name in ("git", "uv"):
            stub(box.bin, name)
        return box

    def test_default_still_requires_hf_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            result = box.run([str(ACTING)], image_build=False)
            self.assertEqual(result.returncode, 3)
            self.assertIn("h3-hf-curl.conf", result.stderr)

    def test_image_build_records_weights_without_token_or_gpu(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = self.box(tmp)
            result = box.run([str(ACTING)], image_build=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            curl = [c for c in box.calls() if c.startswith("curl ")]
            self.assertEqual(len(curl), 1, curl)  # only the public manifest, no download
            self.assertNotIn("-K", curl[0])
            rows = box.recorded()
            self.assertEqual(len(rows), 13)
            self.assertTrue(all(r[0].startswith("LivePortrait/pretrained_weights/") and r[3] == "KlingTeam/LivePortrait" for r in rows))
            self.assertTrue((box.root / "LivePortrait/.h3-acting-smoke-ok").exists())


class SupervisorGateTests(unittest.TestCase):
    """Every supervisor reread/restart sits behind an image-build exit or else-branch."""

    def test_restarts_are_after_image_build_gate(self):
        for script in (BASE, OVERLAY, QWEN):
            text = script.read_text()
            gate = text.find("if h3_image_build; then")
            self.assertGreater(gate, 0, script)
            for match in re.finditer(r"supervisorctl (reread|update|restart)", text):
                self.assertGreater(match.start(), gate, f"{script.name}: {match.group(0)} before the gate")

    def test_overlay_bundle_shared_with_deploy(self):
        deploy = (ROOT / "scripts" / "deploy_h3_new_server.sh").read_text()
        self.assertIn('scripts/h3_overlay_bundle.sh" "$archive"', deploy)
        self.assertNotIn("COPYFILE_DISABLE=1 tar -czf", deploy)


if __name__ == "__main__":
    unittest.main()


def test_legacy_native_app_mode_workflow_is_not_installed():
    vendor = ROOT / "vendor" / "minimax-h3-local-studio"
    assert (vendor / "assets" / "legacy" / "MiniMax H3 — Нативный.app.json").is_file()
    assert not (vendor / "assets" / "MiniMax H3 — Нативный.app.json").exists()
    deploy = (vendor / "scripts" / "deploy_h3_local_studio.sh").read_text()
    assert not re.search(r"install -m \d+ \S*assets/(legacy/)?MiniMax H3", deploy)
