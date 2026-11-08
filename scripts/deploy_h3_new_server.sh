#!/usr/bin/env bash
set -euo pipefail

host=''
port=''
identity="$HOME/.ssh/id_ed25519"
apply=0
skip_base=0
usage() {
  cat <<'EOF'
Usage: scripts/deploy_h3_new_server.sh --host root@HOST --port PORT [--identity PATH] [--apply] [--skip-base]
Dry run by default. With --apply: deploy pinned full BF16 Ref2VA H3, GPU API,
verified H3 style/combat/face LoRAs and X2 Detail VAE, then repoint the existing Mac SSH tunnel.
No generation is started. Existing Mac queue pause state is preserved.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:?}"; shift 2 ;;
    --port) port="${2:?}"; shift 2 ;;
    --identity) identity="${2:?}"; shift 2 ;;
    --apply) apply=1; shift ;;
    --skip-base) skip_base=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "$host" =~ ^root@[A-Za-z0-9.-]+$ ]] || { echo "Use --host root@HOST" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || {
  echo "Invalid --port" >&2; exit 2;
}
[[ -f "$identity" && "$identity" != *'"'* && "$identity" != *$'\n'* ]] || {
  echo "Invalid local SSH key path" >&2; exit 2;
}
workspace="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base="$workspace/vendor/minimax-h3-local-studio/scripts/deploy_remote_h3_local_studio.sh"
[[ -x "$base" ]] || { echo "Pinned H3 base deploy script missing: $base" >&2; exit 2; }
echo "Target: $host:$port"
echo "Base: pinned BF16 Ref2VA ComfyUI + H3 Studio"
echo "Overlay: Mac queue GPU API, four LoRA adapter modes, X2 Detail VAE, camera/color nodes, H3 Safe cache"
echo "Mac data and previous outputs are preserved."
if [[ "$apply" == 0 ]]; then
  echo "Dry run. Add --apply after receiving the NEW instance's SSH address."
  exit 0
fi

# Refuse a new-server install until its available disk can be checked by the overlay installer.
ssh_args_check=(-i "$identity" -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
remote_free="$(ssh "${ssh_args_check[@]}" "$host" 'df -PB1 /workspace/h3 | awk "NR==2 {print \$4}"')"
[[ "$remote_free" =~ ^[0-9]+$ ]] || { echo "Cannot determine free space on $host:/workspace/h3" >&2; exit 3; }
(( remote_free >= 1000000000 )) || {
  echo "At least 1 GB free is required to stage the H3 overlay; available: $remote_free bytes" >&2
  exit 3
}

# A server switch while an old GPU job is in flight can lose its exact status
# and cause a second paid render. Stop before touching either host in that case.
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:18187/api/jobs', timeout=8) as response:
    jobs = json.load(response)
with urllib.request.urlopen('http://127.0.0.1:18187/api/queue', timeout=8) as response:
    queue_paused = bool(json.load(response)['paused'])
active = [(job['job_id'], job['state']) for job in jobs
          if job['state'] in {'uploading', 'running', 'downloading', 'needs_reconciliation'}
          or (job['state'] == 'queued_gpu' and not queue_paused)]
held = [job['job_id'] for job in jobs if job['state'] == 'queued_gpu' and queue_paused]
if held:
    print('Queued jobs remain paused for manual reconciliation after the server switch:', held)
# 'stopping' = the user already cancelled it. Its old server is gone; once the tunnel points
# to the new GPU, the worker sees an unknown remote ID and marks it cancelled. Nothing to lose.
stopping = [job['job_id'] for job in jobs if job['state'] == 'stopping']
if stopping:
    print('Cancelled jobs still waiting for the old GPU will close as cancelled:', stopping)
if active:
    raise SystemExit('Reconcile in-flight Mac jobs before server switch: ' + repr(active))
PY

ssh_args=("${ssh_args_check[@]}")
scp_args=(-i "$identity" -P "$port" -o BatchMode=yes -o IdentitiesOnly=yes
  -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
ssh "${ssh_args[@]}" "$host" 'if test -f /etc/vast-agents-guide.md; then cat /etc/vast-agents-guide.md; fi'
ssh "${ssh_args[@]}" "$host" 'bash -s' < "$workspace/infra/h3_target_preflight.sh"

if [[ "$skip_base" == 0 ]]; then
  bash "$base" --host "$host" --port "$port" --identity "$identity" --fast-rental --ref-only --apply
else
  ssh "${ssh_args[@]}" "$host" \
    'test -s /workspace/h3/ComfyUI/models/diffusion_models/minimax_h3_ref2va_bf16.safetensors &&
     test -s /workspace/h3/ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors &&
     curl -fsS --max-time 5 http://127.0.0.1:18188/system_stats >/dev/null'
fi

archive="$(mktemp -t h3-new-server.XXXXXX.tar.gz)"
trap 'rm -f "$archive"' EXIT
# Same bundle as the offline image build (infra/h3_image_build.sh).
bash "$workspace/scripts/h3_overlay_bundle.sh" "$archive"
ssh "${ssh_args[@]}" "$host" 'mkdir -p /tmp/h3-h200-overlay'
scp "${scp_args[@]}" "$archive" "$host:/tmp/h3-h200-overlay/bundle.tar.gz"
ssh "${ssh_args[@]}" "$host" \
  'tar -xzf /tmp/h3-h200-overlay/bundle.tar.gz -C /tmp/h3-h200-overlay &&
   H3_SCENARIST_27B=0 H3_SKIP_QWEN_DIRECTOR='"${H3_SKIP_QWEN_DIRECTOR:-1}"' bash /tmp/h3-h200-overlay/scripts/install_h3_h200_overlay.sh --apply'

tunnel="$HOME/Library/Application Support/MiniMax H3/tunnel.sh"
[[ -f "$tunnel" ]] || { echo "Remote ready, but Mac tunnel file missing: $tunnel" >&2; exit 3; }
backup="$tunnel.before-new-server-$(date -u +%Y%m%dT%H%M%SZ)"
cp -p "$tunnel" "$backup"
python3 - "$tunnel" "${host#root@}" "$port" "$identity" <<'PY'
from pathlib import Path
import re
import sys
path = Path(sys.argv[1])
text = path.read_text()
for name, value in (("SSH_KEY", sys.argv[4]), ("SERVER_HOST", sys.argv[2]), ("SERVER_PORT", sys.argv[3])):
    pattern = rf'^{name}="[^"\n]*"$'
    text, count = re.subn(pattern, f'{name}="{value}"', text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"Missing {name} in tunnel config")
path.write_text(text)
PY
launchctl kickstart -k "gui/$(id -u)/com.minimax.h3-tunnel"
python3 scripts/install_h3_server_queue_tunnel.py
for _ in $(seq 1 30); do
  if curl -fsS --max-time 4 http://127.0.0.1:18190/api/health >/dev/null; then
    studio_plist="$HOME/Library/LaunchAgents/com.minimax.h3-mac-studio.plist"
    if [[ -f "$studio_plist" ]]; then
      live_workspace="$(plutil -extract EnvironmentVariables.H3_STUDIO_SOURCE raw -o - "$studio_plist" 2>/dev/null || plutil -extract WorkingDirectory raw -o - "$studio_plist")"
      # 06.11.2026: a deploy run from an old R2 snapshot / temp worktree copied its code over the live
      # Studio and rolled 38 files back. Copy only when this checkout is the live HEAD or a descendant of it
      # and the live tree has no uncommitted work; otherwise keep the live code and say so.
      copy_code=0
      if [[ "$workspace" != "$live_workspace" ]]; then
        src_head="$(git -C "$workspace" rev-parse HEAD 2>/dev/null || true)"
        live_head="$(git -C "$live_workspace" rev-parse HEAD 2>/dev/null || true)"
        if [[ -z "$live_head" ]]; then
          copy_code=1
        elif [[ -z "$src_head" ]]; then
          echo "SKIP code copy: $workspace is not a git checkout, cannot prove it is newer than live Studio ($live_head)" >&2
        elif ! git -C "$workspace" merge-base --is-ancestor "$live_head" "$src_head" 2>/dev/null; then
          echo "SKIP code copy: $src_head does not contain live Studio HEAD $live_head (older snapshot?)" >&2
        elif [[ -n "$(git -C "$live_workspace" status --porcelain -- app local_studio preview/h3-director 2>/dev/null)" ]]; then
          echo "SKIP code copy: live Studio has uncommitted changes in app/local_studio/preview" >&2
        else
          copy_code=1
        fi
      fi
      if [[ "$copy_code" == 1 ]]; then
        code_backup="$HOME/Library/Application Support/H3 Studio/code-backups/$(date -u +%Y%m%dT%H%M%SZ)"
        mkdir -p "$code_backup" "$live_workspace/preview"
        for part in app local_studio preview/h3-director; do
          [[ ! -e "$live_workspace/$part" ]] || {
            mkdir -p "$code_backup/$(dirname "$part")"
            cp -R "$live_workspace/$part" "$code_backup/$part"
          }
          cp -R "$workspace/$part/." "$live_workspace/$part/"
        done
        echo "Mac code backup: $code_backup"
      fi
      cp -p "$studio_plist" "$studio_plist.before-new-server-$(date -u +%Y%m%dT%H%M%SZ)"
      python3 - "$studio_plist" <<'PY'
from pathlib import Path
import plistlib
import sys
path = Path(sys.argv[1])
with path.open("rb") as source:
    settings = plistlib.load(source)
settings.setdefault("EnvironmentVariables", {})["H3_ENABLED_STYLE_ADAPTERS"] = "style3d,cartoon2d,combat,realism_people,character_swap,character_swap_v15"
with path.open("wb") as output:
    plistlib.dump(settings, output)
PY
      launchctl bootout "gui/$(id -u)" "$studio_plist" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$studio_plist"
    fi
    echo "READY: GPU API through Mac tunnel; rollback: $backup"
    echo "Studio: http://127.0.0.1:18187/"
    exit 0
  fi
  sleep 2
done
echo "Remote services installed, but Mac tunnel health is not yet ready. Rollback: $backup" >&2
exit 4
