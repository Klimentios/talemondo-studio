#!/usr/bin/env bash
set -euo pipefail

# Installs/updates the local Qwen Director (vLLM, loopback only). Restarting it
# interrupts nothing in ComfyUI, but a running Director plan would fail, so the
# script refuses while ComfyUI is busy unless H3_FORCE=1.
root="${H3_ROOT:-/workspace/h3}"
base_python="$root/venv/bin/python"
qwen_venv="$root/qwen-venv"
bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
service_template="$bundle/deploy/h3-qwen-director.conf"
launcher="$bundle/scripts/run_h3_qwen_director.sh"
# shellcheck source=../vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh
. "$bundle/vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh"
h3_require_driver_cuda || exit 2
if [[ "${H3_FORCE:-0}" != 1 ]] && ! h3_comfy_queue_empty; then
  echo "ComfyUI queue is busy; rerun when idle (or H3_FORCE=1)" >&2
  exit 2
fi

[[ -x "$base_python" && -f "$service_template" && -f "$launcher" ]] || {
  echo "Pinned H3 base Python or Qwen Supervisor template is missing" >&2
  exit 2
}
available="$(df -PB1 "$root" | awk 'NR==2 {print $4}')"
# Image build (H3_IMAGE_BUILD=1, h3_gpu_env.sh): the ~17 GB model cache is recorded, not downloaded.
need=26000000000
if h3_image_build; then need=8000000000; fi
if ((available < need)); then
  echo "Need $((need / 1000000000)) GB free under $root for Qwen vLLM environment and model cache; available: $available" >&2
  exit 2
fi

# A venv left without pip (missing python3-venv) would make pip hit PEP 668: recreate it.
if [[ -x "$qwen_venv/bin/python" ]] && ! "$qwen_venv/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "Recreating broken venv without pip: $qwen_venv" >&2
  rm -rf "$qwen_venv"
fi
if [[ ! -x "$qwen_venv/bin/python" ]]; then
  "$base_python" -m venv "$qwen_venv"
fi
"$qwen_venv/bin/python" -m pip install --upgrade uv
# Known-good pair (2026-09-30): vllm 0.15.0 on cu128. "auto" pulled vllm 0.30 +
# CUDA 13 wheels and failed with libcudart.so.13 on a CUDA 12.8 driver.
"$qwen_venv/bin/uv" pip install --python "$qwen_venv/bin/python" \
  --torch-backend="$H3_VLLM_TORCH_BACKEND" "$H3_VLLM_PIN" "huggingface_hub>=0.30"
"$qwen_venv/bin/python" -c 'import vllm; print("Qwen Director vLLM", vllm.__version__)'
# vLLM 0.15.x bug: `with pool_ctx(tag="weights") and set_current_vllm_config(...)` enters only the
# second context, so weights bypass the sleep pool and sleep(level=1) keeps ~18 GB on the GPU.
"$qwen_venv/bin/python" - <<'PY'
import pathlib, vllm.v1.worker.gpu_worker as worker
path = pathlib.Path(worker.__file__)
source = path.read_text()
broken = ('        with self._maybe_get_memory_pool_context(\n            tag="weights"\n'
          '        ) and set_current_vllm_config(self.vllm_config):')
if broken in source:
    path.write_text(source.replace(broken, broken.replace(") and set_current", "), set_current"), 1))
    print("Patched vLLM sleep weights pool:", path)
PY
mkdir -p "$root/hf-cache"
if h3_image_build; then
  # Whole snapshot (revision main, like snapshot_download below); infra/h3_image_weights.py expands it.
  h3_image_record_weight "$root/hf-cache/hub/models--Qwen--Qwen3-VL-8B-Instruct" - - Qwen/Qwen3-VL-8B-Instruct main '*'
else
[[ -s /root/.cache/h3-hf-curl.conf ]] || {
  echo 'Missing private Hugging Face authentication config' >&2; exit 3;
}
HF_HOME="$root/hf-cache" "$qwen_venv/bin/python" - <<'PY'
from pathlib import Path
import re
from huggingface_hub import snapshot_download
source = Path('/root/.cache/h3-hf-curl.conf').read_text()
match = re.search(r'Bearer (hf_[A-Za-z0-9]+)', source)
if not match:
    raise SystemExit('Invalid private Hugging Face authentication config')
snapshot_download('Qwen/Qwen3-VL-8B-Instruct', token=match.group(1))
print('Authenticated Qwen Director model download complete')
PY
fi

h3_ensure_supervisor
mkdir -p "$root/scripts"
install -m 0755 "$launcher" "$root/scripts/run_h3_qwen_director.sh"
service="/etc/supervisor/conf.d/h3-qwen-director.conf"
mkdir -p "$(dirname "$service")"
sed "s|/workspace/h3|$root|g" "$service_template" > "$service"
if h3_image_build; then
  echo "Image build: Qwen Director installed (config written); start and smoke check run on the server."
  exit 0
fi
supervisorctl reread
supervisorctl update
supervisorctl restart h3-qwen-director

python3 - <<'PY'
import json, time, urllib.request
root = "http://127.0.0.1:8000"
def get_json(path):
    with urllib.request.urlopen(root + path, timeout=10) as response:
        return json.load(response)
def post(path):
    request = urllib.request.Request(root + path, data=b"{}",
                                     headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status
def sleeping():
    payload = get_json("/is_sleeping")
    if isinstance(payload, bool):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("is_sleeping"), bool):
        return payload["is_sleeping"]
    raise RuntimeError("Unknown vLLM /is_sleeping response")

deadline = time.time() + 600
while time.time() < deadline:
    try:
        models = get_json("/v1/models").get("data", [])
        if any(item.get("id") == "Qwen/Qwen3-VL-8B-Instruct" for item in models):
            break
    except Exception:
        pass
    time.sleep(3)
else:
    raise SystemExit("Qwen Director failed to become ready; inspect /var/log/h3-qwen-director.err.log")

if sleeping():
    post("/wake_up")
deadline = time.time() + 120
while time.time() < deadline and sleeping():
    time.sleep(1)
if sleeping():
    raise SystemExit("Qwen Director wake_up did not complete")
post("/sleep?level=1")
deadline = time.time() + 120
while time.time() < deadline and not sleeping():
    time.sleep(1)
if not sleeping():
    raise SystemExit("Qwen Director did not release VRAM after smoke check")
print("Qwen Director ready, local-only, and sleeping to release GPU memory")
PY
