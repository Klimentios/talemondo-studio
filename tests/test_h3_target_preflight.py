from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "infra" / "h3_target_preflight.sh"


def run_preflight(tmp_path: Path, *, driver: str = "12.8", toolkit: str = "12.8", memory: int = 90000,
                  with_nvcc: bool = True) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    nvidia_smi = bindir / "nvidia-smi"
    nvidia_smi.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *memory.total*) printf '%s\\n' \"$H3_TEST_MEMORY\" ;;\n"
        "  *query-gpu*) printf 'Test GPU, %s MiB, 590.00\\n' \"$H3_TEST_MEMORY\" ;;\n"
        "  *) printf '| NVIDIA-SMI 590.00 Driver Version: 590.00 CUDA Version: %s |\\n' \"$H3_TEST_DRIVER\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    nvidia_smi.chmod(0o755)
    if with_nvcc:
        nvcc = bindir / "nvcc"
        nvcc.write_text(
            "#!/bin/sh\nprintf 'Cuda compilation tools, release %s, V12.8.1\\n' \"$H3_TEST_TOOLKIT\"\n",
            encoding="utf-8",
        )
        nvcc.chmod(0o755)
    env = os.environ | {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "H3_TEST_DRIVER": driver,
        "H3_TEST_TOOLKIT": toolkit,
        "H3_TEST_MEMORY": str(memory),
    }
    return subprocess.run(["bash", str(PREFLIGHT)], env=env, capture_output=True, text=True, timeout=10)


def test_accepts_supported_target_gpu_and_toolkit(tmp_path: Path):
    result = run_preflight(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "CUDA driver 12.8 / toolkit 12.8" in result.stdout


def test_rejects_old_driver_before_download(tmp_path: Path):
    result = run_preflight(tmp_path, driver="12.7")
    assert result.returncode == 7
    assert "драйвер" in result.stderr.lower()


def test_rejects_insufficient_vram_before_download(tmp_path: Path):
    result = run_preflight(tmp_path, memory=89999)
    assert result.returncode == 7
    assert "VRAM" in result.stderr


def test_requires_cuda_compiler_for_target_specific_extension(tmp_path: Path):
    result = run_preflight(tmp_path, with_nvcc=False)
    assert result.returncode == 7
    assert "nvcc" in result.stderr
