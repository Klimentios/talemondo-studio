#!/usr/bin/env bash
set -euo pipefail
# Repos under /workspace are often owned by another uid (Vast image, copied volume). As root, git then
# refuses them as "dubious ownership" (exit 128). Trust them for this script only, no global config.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'

apply=0
with_models=0
parallel_models=0
ref_only=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    --with-models) with_models=1 ;;
    --parallel-models) parallel_models=1 ;;
    --ref-only) ref_only=1 ;;
    -h|--help)
      printf '%s\n' \
        'Usage: deploy_h3_local_studio.sh [--apply] [--with-models] [--parallel-models] [--ref-only]' \
        'Without --apply, prints the pinned plan and changes nothing.'
      exit 0
      ;;
    *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

h3_root="${H3_ROOT:-/workspace/h3}"
comfy_repo="$h3_root/ComfyUI"
comfy_commit='b5cc8830279eae909a59de030af1e50761c36751'
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_dir="$(cd "$script_dir/.." && pwd)"

printf '%s\n' \
  "Target: $h3_root" \
  "ComfyUI: Comfy-Org/ComfyUI@$comfy_commit" \
  "Models requested: $with_models" \
  "Parallel model downloads: $parallel_models" \
  'No files will be deleted.'

if [[ "$apply" != 1 ]]; then
  printf '%s\n' 'Dry run only. Add --apply to execute.'
  exit 0
fi

for command in git python3 curl tar; do
  command -v "$command" >/dev/null || { printf 'Missing command: %s\n' "$command" >&2; exit 2; }
done
# shellcheck source=h3_gpu_env.sh
. "$script_dir/h3_gpu_env.sh"
h3_require_driver_cuda || exit 2

mkdir -p "$h3_root" "$h3_root/input" "$h3_root/output" \
  "$h3_root/hf-cache" "$h3_root/cache"

clone_pinned() {
  local url="$1" path="$2" commit="$3"
  if [[ ! -e "$path" ]]; then
    git clone --filter=blob:none "$url" "$path"
    git -C "$path" checkout --detach "$commit"
    return
  fi
  [[ -d "$path/.git" ]] || { printf 'Refusing non-Git path: %s\n' "$path" >&2; exit 3; }
  local head
  head="$(git -C "$path" rev-parse HEAD)"
  [[ "$head" == "$commit" ]] || {
    printf 'Refusing existing checkout at unexpected commit: %s (%s)\n' "$path" "$head" >&2
    exit 3
  }
}

clone_pinned 'https://github.com/Comfy-Org/ComfyUI.git' "$comfy_repo" "$comfy_commit"

unknown_comfy="$(git -C "$comfy_repo" status --short | grep -Ev '^( M|M |\?\?) (server\.py|server\.py\.pre-minimax-comfy-user-[0-9]+)$' || true)"
if [[ -n "$unknown_comfy" ]]; then
  printf '%s\n' 'Refusing to modify a checkout with unrelated changes:' "$unknown_comfy" >&2
  exit 3
fi

apply_git_patch() {
  local repo="$1" patch="$2"
  if git -C "$repo" apply --reverse --check "$patch" >/dev/null 2>&1; then
    printf 'Already applied: %s\n' "$patch"
  elif git -C "$repo" apply --check "$patch"; then
    git -C "$repo" apply "$patch"
    printf 'Applied: %s\n' "$patch"
  else
    printf 'Patch does not match pinned source: %s\n' "$patch" >&2
    exit 4
  fi
}

# ComfyUI runs without --enable-cors-header: only the GPU API (server-side Python) talks to it,
# and the default origin check keeps other browser tabs on the Mac away from the tunnelled port.
# (The former Comfy-User CORS patch served an embedded editor nobody uses; servers that still
# carry it keep working, the patch is simply no longer applied.)

native_node_dir="$comfy_repo/custom_nodes/h3_native_helpers"
native_workflow_dir="$comfy_repo/user/default/workflows"
native_backup="$h3_root/backup/native-comfy-$(date +%Y%m%d-%H%M%S)"
if [[ -e "$native_node_dir/__init__.py" || \
      -e "$native_workflow_dir/MiniMax H3 — Нативный.app.json" ]]; then
  mkdir -p "$native_backup"
  [[ ! -e "$native_node_dir/__init__.py" ]] || \
    cp -a "$native_node_dir/__init__.py" "$native_backup/h3_native_helpers.py"
  [[ ! -e "$native_workflow_dir/MiniMax H3 — Нативный.app.json" ]] || \
    cp -a "$native_workflow_dir/MiniMax H3 — Нативный.app.json" "$native_backup/"
  printf 'Native rollback: %s\n' "$native_backup"
fi
mkdir -p "$native_node_dir" "$native_workflow_dir"
install -m 0644 "$skill_dir/assets/h3_native_helpers.py" \
  "$native_node_dir/__init__.py"
python3 -m py_compile "$native_node_dir/__init__.py"
# Legacy: the native App Mode workflow (assets/legacy/) is no longer installed in any mode. Neither
# Studio nor the GPU API uses it (app/graph.py builds its own BF16 graph); its model metadata still
# names the upstream int8/nvfp4 files and the 4-step turbo LoRA, and it needs FL2VA weights that
# --ref-only never downloads. An installed copy is removed (backup above).
rm -f "$native_workflow_dir/MiniMax H3 — Нативный.app.json"
printf '%s\n' 'Native App Mode workflow: legacy, not installed.'

# PEP 668: never pip-install into the system Python. Every package goes into a venv.
# Ubuntu images often lack python3-venv: `python3 -m venv` then leaves a half-made
# venv without pip, and the next run fails with "externally-managed-environment".
ensure_venv() {
  local dir="$1"; shift
  if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    local pyver; pyver="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "python${pyver}-venv" python3-venv python3-pip || \
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip
  fi
  if [[ -x "$dir/bin/python" ]] && ! "$dir/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Recreating broken venv without pip: $dir" >&2
    rm -rf "$dir"
  fi
  [[ -x "$dir/bin/python" ]] || python3 -m venv "$@" "$dir"
}
# Self-contained venv (no --system-site-packages): the floating vastai/base-image tag must not
# change ComfyUI's numpy/pillow/requests/... Former system packages are pinned in comfy_pins.
comfy_pins="$skill_dir/assets/comfy-venv-requirements.txt"
ensure_venv "$h3_root/venv"
"$h3_root/venv/bin/python" -m pip install --upgrade pip
# Pin the known-good CUDA 12.8 torch BEFORE ComfyUI requirements, so pip keeps it
# instead of pulling a newer CUDA build the driver cannot run (PTX / spawn errors).
"$h3_root/venv/bin/python" -m pip install --index-url "$H3_TORCH_INDEX" "${H3_TORCH_PINS[@]}"
"$h3_root/venv/bin/python" -m pip install -r "$comfy_repo/requirements.txt" -c "$comfy_pins"
h3_comfy_venv_isolate "$h3_root/venv" "$comfy_pins" || exit 3
H3_IMAGE_BUILD="${H3_IMAGE_BUILD:-0}" "$h3_root/venv/bin/python" - <<'PY'
import os, torch
if not torch.__version__.startswith("2.11.0") or torch.version.cuda != "12.8":
    raise SystemExit(f"Unexpected torch build after requirements: {torch.__version__} / CUDA {torch.version.cuda}")
if os.environ["H3_IMAGE_BUILD"] == "1":
    # Offline image build: no GPU here; the build assertion above is what the image guarantees.
    print("ComfyUI torch", torch.__version__, "CUDA", torch.version.cuda, "(image build: GPU check skipped)")
    raise SystemExit(0)
if not torch.cuda.is_available():
    raise SystemExit("torch cannot use the GPU on this host")
print("ComfyUI torch", torch.__version__, "CUDA", torch.version.cuda, torch.cuda.get_device_name(0))
PY


download_model() {
  local rel="$1" expected="$2"
  local dest="$comfy_repo/models/$rel"
  local url="https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/$rel"
  if h3_image_build; then
    # SHA-256 comes from the published LFS entry (infra/h3_image_weights.py); size is pinned here.
    h3_image_record_weight "$dest" "$expected" - Comfy-Org/MiniMax-H3 main "$rel"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]]; then
    local size
    size="$(stat -c '%s' "$dest")"
    [[ "$size" == "$expected" ]] || {
      printf 'Refusing to overwrite wrong-size model: %s (%s, expected %s)\n' "$dest" "$size" "$expected" >&2
      exit 5
    }
    printf 'Model already complete: %s\n' "$rel"
    return
  fi
  [[ -s /root/.cache/h3-hf-curl.conf ]] || {
    printf 'Missing private Hugging Face authentication config\n' >&2
    exit 5
  }
  if command -v aria2c >/dev/null 2>&1; then
    [[ -s /root/.cache/h3-hf-aria2.conf ]] || {
      printf 'Missing private Hugging Face aria2 authentication config\n' >&2
      exit 5
    }
    aria2c --continue=true --max-connection-per-server=8 --split=8 \
      --min-split-size=32M --file-allocation=none --max-tries=8 \
      --retry-wait=5 --conf-path=/root/.cache/h3-hf-aria2.conf --dir="$(dirname "$dest")" \
      --out="$(basename "$dest").part" "$url"
  else
    curl_args=(-fL --retry 8 --retry-delay 5 --continue-at -
      --config /root/.cache/h3-hf-curl.conf)
    curl "${curl_args[@]}" --output "$dest.part" "$url"
  fi
  local size
  size="$(stat -c '%s' "$dest.part")"
  [[ "$size" == "$expected" ]] || {
    printf 'Incomplete model download: %s (%s, expected %s)\n' "$rel" "$size" "$expected" >&2
    exit 5
  }
  verify_hf_sha256 "$rel" "$dest.part" || exit 5
  mv "$dest.part" "$dest"
}

# A resumed download can have the right size but corrupted bytes. Compare with
# the LFS SHA-256 that Hugging Face publishes for the same file.
verify_hf_sha256() {
  local rel="$1" file="$2" expected_sha
  expected_sha="$(python3 - "$rel" <<'PY'
import json, re, sys, urllib.request
from pathlib import Path
rel = sys.argv[1]
headers = {}
try:
    match = re.search(r'Bearer (hf_[A-Za-z0-9]+)', Path('/root/.cache/h3-hf-curl.conf').read_text())
    if match:
        headers["Authorization"] = "Bearer " + match.group(1)
except OSError:
    pass
folder = rel.rsplit("/", 1)[0]
url = f"https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/tree/main/{folder}"
try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        entries = json.load(response)
except Exception:
    sys.exit(0)
for entry in entries:
    if entry.get("path") == rel and entry.get("lfs", {}).get("oid"):
        print(entry["lfs"]["oid"])
PY
)"
  if [[ -z "$expected_sha" ]]; then
    printf 'Warning: no published SHA-256 for %s; size check only\n' "$rel" >&2
    return 0
  fi
  printf 'Verifying SHA-256 of %s\n' "$rel"
  echo "$expected_sha  $file" | sha256sum -c --status || {
    printf 'SHA-256 mismatch: %s (download removed, rerun to fetch again)\n' "$rel" >&2
    rm -f "$file"
    return 1
  }
}

if [[ "$with_models" == 1 ]]; then
  model_specs=(
    'diffusion_models/minimax_h3_ref2va_bf16.safetensors:66280487368'
    'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors:51506295256'
    'vae/minimax_h3_video_vae_fp16.safetensors:5207808496'
    'vae/minimax_h3_audio_vae_fp32.safetensors:605254808'
  )
  if [[ "$ref_only" == 0 ]]; then
    model_specs+=('diffusion_models/minimax_h3_fl2va_bf16.safetensors:66280487368')
  fi
  if [[ "$parallel_models" == 1 ]]; then
    pids=()
    labels=()
    for spec in "${model_specs[@]}"; do
      rel="${spec%%:*}"
      expected="${spec##*:}"
      download_model "$rel" "$expected" &
      pids+=("$!")
      labels+=("$rel")
    done
    download_failed=0
    for index in "${!pids[@]}"; do
      if ! wait "${pids[$index]}"; then
        printf 'Model download failed: %s\n' "${labels[$index]}" >&2
        download_failed=1
      fi
    done
    [[ "$download_failed" == 0 ]] || exit 5
  else
    for spec in "${model_specs[@]}"; do
      download_model "${spec%%:*}" "${spec##*:}"
    done
  fi
fi

if h3_ensure_supervisor; then
  launcher_dir="$(H3_ROOT="$h3_root" h3_launcher_dir)"
  mkdir -p "$launcher_dir"
  install -m 0755 /dev/stdin "$launcher_dir/h3-comfyui.sh" <<EOF
#!/bin/bash
set -eo pipefail
utils=/opt/supervisor-scripts/utils
[[ ! -f "\${utils}/logging.sh" ]] || . "\${utils}/logging.sh"
[[ ! -f "\${utils}/environment.sh" ]] || . "\${utils}/environment.sh"
export HF_HOME="$h3_root/hf-cache"
export XDG_CACHE_HOME="$h3_root/cache"
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# Optional A/B tuning without editing this script, e.g. H3_COMFY_EXTRA_ARGS="--highvram"
# in $h3_root/comfy.env, then: supervisorctl restart h3-comfyui
H3_COMFY_EXTRA_ARGS=""
[[ ! -f "$h3_root/comfy.env" ]] || . "$h3_root/comfy.env"
cd "$comfy_repo"
# shellcheck disable=SC2086  # extra args are intentionally word-split
exec "$h3_root/venv/bin/python" main.py --listen 127.0.0.1 --port 18188 --disable-auto-launch --input-directory "$h3_root/input" --output-directory "$h3_root/output" \${H3_COMFY_EXTRA_ARGS}
EOF
  install -m 0644 /dev/stdin /etc/supervisor/conf.d/h3-comfyui.conf <<EOF
[program:h3-comfyui]
environment=PROC_NAME="%(program_name)s"
command=$launcher_dir/h3-comfyui.sh
autostart=true
; Restart on any exit (ComfyUI also exits 0, e.g. after a Manager restart or a stray SIGTERM).
autorestart=true
startsecs=10
startretries=10
; SIGINT = ComfyUI's own clean shutdown (KeyboardInterrupt); 30 s to release CUDA before SIGKILL.
stopsignal=INT
stopwaitsecs=30
stopasgroup=true
killasgroup=true
$(h3_supervisor_log_lines h3-comfyui)
EOF
  # The legacy gradio Studio (:18190) is retired: Mac Studio -> GPU API is the only client path.
  if [[ -f /etc/supervisor/conf.d/h3-git-studio.conf ]]; then
    h3_image_build || supervisorctl stop h3-git-studio >/dev/null 2>&1 || true
    rm -f /etc/supervisor/conf.d/h3-git-studio.conf "$launcher_dir/h3-git-studio.sh"
  fi
  if h3_image_build; then
    printf '%s\n' 'Image build: supervisor configs written; services start on the server.'
  else
  # Check the queue BEFORE reread/update: update alone may restart a changed program.
  h3_comfy_queue_empty || { printf '%s\n' 'Refusing service restart: ComfyUI queue is not empty' >&2; exit 7; }
  supervisorctl reread
  supervisorctl update
  supervisorctl restart h3-comfyui

  ready=0
  for _ in $(seq 1 180); do
    if curl -fsS --max-time 2 http://127.0.0.1:18188/system_stats >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  [[ "$ready" == 1 ]] || { printf '%s\n' 'H3 services did not become ready within 180 seconds.' >&2; exit 7; }
  fi
else
  printf '%s\n' 'Supervisor integration skipped: unsupported host layout.'
fi

printf '%s\n' \
  'Deployment complete. No generation was started.' \
  'READY native=http://127.0.0.1:18188'
