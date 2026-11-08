#!/usr/bin/env bash
set -euo pipefail

host=''
port='22'
identity=''
apply=0
with_models=0
parallel_models=0
fast_rental=0
ref_only=0

usage() {
  cat <<'EOF'
Usage: deploy_remote_h3_local_studio.sh --host USER@HOST [options]

Options:
  --port PORT       SSH port (default: 22)
  --identity PATH   SSH private-key path (not copied to the server)
  --apply           Execute the remote deployment; otherwise show the plan
  --with-models     Download the five official full-quality model files
  --parallel-models Download the five model files concurrently
  --fast-rental     Ephemeral GPU mode: models + parallel downloads + preflight
  --ref-only        Install Ref2VA model set without separate FL2VA diffusion weights
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:?missing --host value}"; shift 2 ;;
    --port) port="${2:?missing --port value}"; shift 2 ;;
    --identity) identity="${2:?missing --identity value}"; shift 2 ;;
    --apply) apply=1; shift ;;
    --with-models) with_models=1; shift ;;
    --parallel-models) parallel_models=1; shift ;;
    --fast-rental) fast_rental=1; with_models=1; parallel_models=1; shift ;;
    --ref-only) ref_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ -n "$host" ]] || { printf '%s\n' 'Missing required --host USER@HOST' >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] || { printf 'Invalid SSH port: %s\n' "$port" >&2; exit 2; }
if [[ -n "$identity" && ! -f "$identity" ]]; then
  printf 'SSH identity file does not exist: %s\n' "$identity" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_dir="$(cd "$script_dir/.." && pwd)"
remote_dir='/tmp/minimax-h3-local-studio-deploy'
# Keep the session alive through long model downloads; a dropped SSH session
# used to abort the 2026-09-30 deployment mid-download.
keepalive=(-o ServerAliveInterval=30 -o ServerAliveCountMax=20 -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new)
ssh_args=(-p "$port" -o BatchMode=yes "${keepalive[@]}")
scp_args=(-P "$port" -o BatchMode=yes "${keepalive[@]}")
if [[ -n "$identity" ]]; then
  ssh_args+=(-i "$identity")
  scp_args+=(-i "$identity")
fi

printf '%s\n' \
  "Target: $host (SSH port $port)" \
  "Bundle: $skill_dir" \
  "Models requested: $with_models" \
  "Parallel model downloads: $parallel_models" \
  "Fast rental mode: $fast_rental" \
  'The SSH key is used locally and is never copied.'

if [[ "$apply" != 1 ]]; then
  printf '%s\n' 'Dry run only. Add --apply to upload and deploy.'
  exit 0
fi

for command in ssh scp tar; do
  command -v "$command" >/dev/null || { printf 'Missing command: %s\n' "$command" >&2; exit 2; }
done

printf '%s\n' 'Running remote hardware and storage preflight.'
ssh "${ssh_args[@]}" "$host" "set -e
command -v nvidia-smi >/dev/null
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
free -h | sed -n '1,2p'
df -h /workspace 2>/dev/null || df -h /
if [ '$with_models' = 1 ]; then
  target=\"\${H3_ROOT:-/workspace/h3}\"
  while [ ! -d \"\$target\" ]; do target=\$(dirname \"\$target\"); done
  available=\$(df -PB1 \"\$target\" | awk 'NR==2 {print \$4}')
  # Reserve room for isolated runtimes and the overlay, then add only model
  # files that are not already complete at their exact published size.
  required=70000000000
  models=\"\$target/ComfyUI/models\"
  for spec in \
    'diffusion_models/minimax_h3_ref2va_bf16.safetensors:66280487368' \
    'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors:51506295256' \
    'vae/minimax_h3_video_vae_fp16.safetensors:5207808496' \
    'vae/minimax_h3_audio_vae_fp32.safetensors:605254808'; do
    rel=\"\${spec%:*}\"; size=\"\${spec##*:}\"
    actual=\$(stat -c %s \"\$models/\$rel\" 2>/dev/null || echo 0)
    if [ \"\$actual\" != \"\$size\" ]; then
      partial=\$(stat -c %s \"\$models/\$rel.part\" 2>/dev/null || echo 0)
      # The downloader resumes .part files. Count only remaining bytes to avoid a false refusal.
      if [ \"\$partial\" -lt \"\$size\" ]; then
        required=\$((required + size - partial))
      else
        required=\$((required + size))
      fi
    fi
  done
  if [ '$ref_only' = 0 ]; then
    expected=66280487368
    actual=\$(stat -c %s \"\$models/diffusion_models/minimax_h3_fl2va_bf16.safetensors\" 2>/dev/null || echo 0)
    if [ \"\$actual\" != \"\$expected\" ]; then
      partial=\$(stat -c %s \"\$models/diffusion_models/minimax_h3_fl2va_bf16.safetensors.part\" 2>/dev/null || echo 0)
      if [ \"\$partial\" -lt \"\$expected\" ]; then
        required=\$((required + expected - partial))
      else
        required=\$((required + expected))
      fi
    fi
  fi
  if [ \"\$available\" -lt \"\$required\" ]; then
    echo \"Insufficient free disk for the clean BF16 stack: \$available bytes; require at least \$required\" >&2
    exit 6
  fi
fi"

archive="$(mktemp "${TMPDIR:-/tmp}/minimax-h3-local-studio.XXXXXX")"
cleanup() { rm -f "$archive"; }
trap cleanup EXIT

COPYFILE_DISABLE=1 tar \
  --exclude='._*' --exclude='*/._*' \
  --exclude='__pycache__' --exclude='*/__pycache__' --exclude='*.pyc' \
  -czf "$archive" -C "$skill_dir" .

ssh "${ssh_args[@]}" "$host" \
  "set -e; mkdir -p '$remote_dir'; find '$remote_dir' -mindepth 1 -depth -delete"
scp "${scp_args[@]}" "$archive" "$host:$remote_dir/bundle.tar.gz"
remote_flags='--apply'
[[ "$with_models" == 1 ]] && remote_flags+=' --with-models'
[[ "$parallel_models" == 1 ]] && remote_flags+=' --parallel-models'
[[ "$ref_only" == 1 ]] && remote_flags+=' --ref-only'
audit_flags=''
[[ "$ref_only" == 1 ]] && audit_flags=' --ref-only'
ssh "${ssh_args[@]}" "$host" \
  "set -e; tar -xzf '$remote_dir/bundle.tar.gz' -C '$remote_dir'; chmod +x '$remote_dir/scripts/'*.sh; '$remote_dir/scripts/deploy_h3_local_studio.sh' $remote_flags; '$remote_dir/scripts/audit_h3_local_studio.sh' $audit_flags"

printf '%s\n' \
  'Remote deployment and audit completed. No generation was started.' \
  'Tunnel (ComfyUI 18188, GPU API 18189):' \
  "ssh -N -o ExitOnForwardFailure=yes -L 18188:127.0.0.1:18188 -L 18189:127.0.0.1:18189 -p $port $host"
