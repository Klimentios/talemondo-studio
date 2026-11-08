#!/usr/bin/env bash
set -euo pipefail

# Second Scenarist/Director model, installed next to the default one but NOT activated.
# install_h3_h200_overlay.sh runs it after install_h3_qwen_director.sh (skip: H3_SCENARIST_27B=0).
# It only adds an isolated venv and a pinned model snapshot; no Supervisor config, no restart.
# The default qwen-venv (vLLM 0.15.0 + sleep patch) and Qwen3-VL-8B stay untouched and active.
# Activate / roll back without reinstalling: scripts/h3_scenarist_switch.sh --profile ...
#
#   H3_SCENARIST_NEXT_PROFILE=qwen3.8-27b-fp8 bash scripts/install_h3_scenarist_next.sh
#
# vLLM 0.19.1: Qwen3_5ForConditionalGeneration (both profiles) is supported since 0.17.0; 0.19 adds
# the Qwen3.5 GDN-quantized and FP8 fixes, the SM120 blockwise-FP8 CUTLASS GEMM and the sleep-mode
# memory-leak fix, and keeps the packaging of our proven 0.15.0: PyPI wheel with CUDA 12.9 kernels
# (sm_120 included) on torch 2.10.0 from the cu128 index, so it runs where 0.15.0 runs.
# 0.20+ moved to torch 2.11 + CUDA 13 by default (driver >= 580; only a cu129 variant on GitHub).
profile="${H3_SCENARIST_NEXT_PROFILE:-qwen3.8-27b-fp8}"
case "$profile" in
  qwen3.8-27b-fp8) model=Qwen/Qwen3.8-27B-FP8; revision=017b9c7af6b5689d5dd426a76e0bc077eb5ca20a ;;
  qwen3.6-27b-fp8) model=Qwen/Qwen3.6-27B-FP8; revision=e89b16ebf1988b3d6befa7de50abc2d76f26eb09 ;;
  *) echo "H3_SCENARIST_NEXT_PROFILE: qwen3.8-27b-fp8 or qwen3.6-27b-fp8" >&2; exit 2 ;;
esac
root="${H3_ROOT:-/workspace/h3}"
base_python="$root/venv/bin/python"
next_venv="$root/qwen-next-venv"
vllm_pin="${H3_VLLM_NEXT_PIN:-vllm==0.19.1}"
torch_backend="${H3_VLLM_NEXT_TORCH_BACKEND:-cu128}"
bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
manifest="$bundle/deploy/scenarist-models/$profile.sha256"
# shellcheck source=../vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh
. "$bundle/vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh"
h3_require_driver_cuda || exit 2
[[ -x "$base_python" && -f "$manifest" ]] || { echo "Base Python or $manifest is missing" >&2; exit 2; }

if h3_image_build; then
  cc=image-build  # no GPU: the CUDA/arch check below is skipped (H3_IMAGE_BUILD=1, h3_gpu_env.sh)
else
total_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
cc="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1 | tr -d ' ')"
echo "GPU: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -n1), compute $cc, ${total_mib} MiB"
# Awake budget ≈ 40 GB (31 GB FP8 weights + vision tower + KV/GDN state for 32k context).
((total_mib >= 80000)) || echo "WARNING: ${total_mib} MiB GPU — the 27B profile needs ~40 GB awake; keep the default profile" >&2
fi

snapshot="$root/hf-cache/hub/models--${model//\//--}/snapshots/$revision"
need=0
[[ -x "$next_venv/bin/vllm" ]] || need=$((need + 12000000000))
h3_image_build || [[ -f "$snapshot/model.safetensors.index.json" ]] || need=$((need + 31500000000))
available="$(df -PB1 "$root" | awk 'NR==2 {print $4}')"
if ((available < need)); then
  echo "Need $need bytes free under $root for the $profile venv + weights; available: $available." >&2
  echo "Small disk: H3_SCENARIST_27B=0 skips this optional profile." >&2
  exit 2
fi

if [[ -x "$next_venv/bin/python" ]] && ! "$next_venv/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "Recreating broken venv without pip: $next_venv" >&2
  rm -rf "$next_venv"
fi
[[ -x "$next_venv/bin/python" ]] || "$base_python" -m venv "$next_venv"
"$next_venv/bin/python" -m pip install --upgrade uv
"$next_venv/bin/uv" pip install --python "$next_venv/bin/python" --torch-backend="$torch_backend" "$vllm_pin"
# No sleep patch: the `pool_ctx(...) and set_current_vllm_config(...)` bug patched in the 0.15 venv
# is fixed upstream (gpu_worker.load_model enters both contexts since 0.16). Checked here.
"$next_venv/bin/python" - "$cc" <<'PY'
import sys, pathlib, torch, vllm, vllm.v1.worker.gpu_worker as worker
print("Scenarist vLLM", vllm.__version__, "torch", torch.__version__, "CUDA runtime", torch.version.cuda)
if ") and set_current_vllm_config" in pathlib.Path(worker.__file__).read_text():
    raise SystemExit("This vLLM loads weights outside the sleep pool; pick a version >= 0.16")
if sys.argv[1] == "image-build":
    raise SystemExit(0)  # offline image build: no GPU to check
if not torch.cuda.is_available():
    raise SystemExit("torch in qwen-next-venv cannot see the GPU (driver/CUDA runtime mismatch)")
arch = "sm_" + sys.argv[1].replace(".", "")
if arch not in torch.cuda.get_arch_list():
    raise SystemExit(f"torch {torch.__version__} has no {arch} kernels: {torch.cuda.get_arch_list()}")
PY

# Pinned snapshot into the shared HF cache, then every LFS file against the committed SHA-256 list.
mkdir -p "$root/hf-cache"
if h3_image_build; then
  # LFS blobs are stored under their SHA-256 in the HF cache; small non-LFS files come with
  # snapshot_download on the server (it skips blobs already present).
  grep -v '^#' "$manifest" | while read -r digest name; do
    [[ -n "$digest" ]] || continue
    h3_image_record_weight "${snapshot%/snapshots/*}/blobs/$digest" - "$digest" "$model" "$revision" "$name"
  done
  echo "Image build: $profile venv installed, weights recorded (not downloaded)."
  exit 0
fi
[[ -s /root/.cache/h3-hf-curl.conf ]] || { echo 'Missing private Hugging Face authentication config' >&2; exit 3; }
HF_HOME="$root/hf-cache" "$next_venv/bin/python" - "$model" "$revision" "$manifest" <<'PY'
import hashlib, re, sys
from pathlib import Path
from huggingface_hub import snapshot_download
model, revision, manifest = sys.argv[1:4]
match = re.search(r'Bearer (hf_[A-Za-z0-9]+)', Path('/root/.cache/h3-hf-curl.conf').read_text())
if not match:
    raise SystemExit('Invalid private Hugging Face authentication config')
folder = Path(snapshot_download(model, revision=revision, token=match.group(1)))
if folder.name != revision:
    raise SystemExit(f"Snapshot {folder} is not the pinned revision {revision}")
bad = []
for line in Path(manifest).read_text().splitlines():
    if not line.strip() or line.startswith('#'):
        continue
    digest, name = line.split(None, 1)
    h = hashlib.sha256()
    with (folder / name.strip()).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 24), b''):
            h.update(chunk)
    if h.hexdigest() != digest:
        bad.append(name)
if bad:
    raise SystemExit('SHA-256 mismatch: ' + ', '.join(bad))
print(f'{model}@{revision}: snapshot verified ({folder})')
PY
echo "Installed, not active: $profile. Activate: bash $root/scripts/h3_scenarist_switch.sh --profile $profile"
