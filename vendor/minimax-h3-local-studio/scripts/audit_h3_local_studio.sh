#!/usr/bin/env bash
set -euo pipefail
# Repos under /workspace are often owned by another uid (Vast image, copied volume). As root, git then
# refuses them as "dubious ownership" (exit 128). Trust them for this script only, no global config.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'

h3_root="${H3_ROOT:-/workspace/h3}"
comfy_url="${COMFY_URL:-http://127.0.0.1:18188}"
ref_only=0
for arg in "$@"; do
  case "$arg" in
    --ref-only) ref_only=1 ;;
    -h|--help)
      printf '%s\n' 'Usage: audit_h3_local_studio.sh [--ref-only]' \
        'Default checks the complete five-file baseline; --ref-only omits the unused FL2VA diffusion checkpoint.'
      exit 0
      ;;
    *) printf 'Unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

printf '%s\n' '== identity =='
hostname
date -Is
printf 'h3_root=%s\ncomfy_url=%s\n' "$h3_root" "$comfy_url"

printf '%s\n' '== storage and GPU =='
df -h "$h3_root" 2>/dev/null || df -h /
command -v nvidia-smi >/dev/null && nvidia-smi \
  --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu \
  --format=csv,noheader || true

if command -v vast-capabilities >/dev/null; then
  printf '%s\n' '== Vast workspace durability =='
  vast-capabilities 2>/dev/null | python3 -c \
    'import json,sys; d=json.load(sys.stdin); print("workspace_is_volume=", d.get("instance",{}).get("workspace_is_volume"))' \
    || true
fi

printf '%s\n' '== services =='
if command -v supervisorctl >/dev/null; then
  supervisorctl status h3-comfyui 2>/dev/null || true
fi

printf '%s\n' '== queue and HTTP =='
python3 - "$comfy_url" <<'PY'
import json, sys, urllib.request
comfy = sys.argv[1]
for name, url in (
    ("system_stats", comfy + "/system_stats"),
    ("object_info", comfy + "/object_info"),
    ("queue", comfy + "/queue"),
):
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            body = response.read()
            print(f"{name}: HTTP {response.status}, {len(body)} bytes")
            if name == "queue":
                data = json.loads(body)
                print(json.dumps({
                    "running": len(data.get("queue_running", [])),
                    "pending": len(data.get("queue_pending", [])),
                }))
    except Exception as exc:
        print(f"{name}: ERROR {type(exc).__name__}: {exc}")
PY

printf '%s\n' '== source revisions and dirty state =='
for repo in "$h3_root/ComfyUI"; do
  if [[ -d "$repo/.git" ]]; then
    printf '%s\n' "[$repo]"
    git -C "$repo" rev-parse HEAD
    git -C "$repo" status --short
  else
    printf '%s\n' "missing repository: $repo"
  fi
done

printf '%s\n' '== required clean model set =='
required=(
  'diffusion_models/minimax_h3_ref2va_bf16.safetensors:66280487368'
  'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors:51506295256'
  'vae/minimax_h3_video_vae_fp16.safetensors:5207808496'
  'vae/minimax_h3_audio_vae_fp32.safetensors:605254808'
)
if [[ "$ref_only" == 0 ]]; then
  required+=( 'diffusion_models/minimax_h3_fl2va_bf16.safetensors:66280487368' )
fi
for item in "${required[@]}"; do
  rel="${item%%:*}"
  expected="${item##*:}"
  path="$h3_root/ComfyUI/models/$rel"
  if [[ -f "$path" ]]; then
    actual="$(stat -c '%s' "$path" 2>/dev/null || stat -f '%z' "$path")"
    if [[ "$actual" == "$expected" ]]; then
      printf 'OK  %s (%s bytes)\n' "$rel" "$actual"
    else
      printf 'BAD %s (%s bytes, expected %s)\n' "$rel" "$actual" "$expected"
    fi
  else
    printf 'MISSING %s\n' "$rel"
  fi
done

printf '%s\n' '== native workflow inventory (advanced ComfyUI only) =='
find "$h3_root/ComfyUI/user/default/workflows" -maxdepth 1 -type f -printf '%f\n' 2>/dev/null | sort || true

printf '%s\n' '== native H3 App Mode contract =='
python3 - "$h3_root" "$comfy_url" "$ref_only" <<'PY'
import json, pathlib, sys, urllib.request

root = pathlib.Path(sys.argv[1])
comfy = sys.argv[2]
ref_only = sys.argv[3] == "1"
workflow = root / "ComfyUI/user/default/workflows/MiniMax H3 — Нативный.app.json"
node = root / "ComfyUI/custom_nodes/h3_native_helpers/__init__.py"
if not node.is_file():
    raise SystemExit("MISSING native node")
with urllib.request.urlopen(comfy + "/object_info/H3NativeReferenceComposer", timeout=15) as response:
    info = json.load(response)
assert "H3NativeReferenceComposer" in info
# The App Mode workflow is legacy (assets/legacy/) and no longer installed in any mode.
if workflow.is_file():
    print("WARN legacy native App Mode workflow still installed:", workflow.name)
print("Native App Mode: legacy, not installed; composer registered")
PY
