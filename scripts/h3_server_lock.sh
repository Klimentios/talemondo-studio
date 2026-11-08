#!/usr/bin/env bash
# Эталон рабочего GPU-сервера: версии пакетов во всех venv, ревизии репозиториев, веса с точным размером.
# verify_h3_server.sh сравнивает с ним новый сервер («один в один»). Ничего не меняет на сервере.
#   bash scripts/h3_server_lock.sh --host root@HOST --port PORT [--identity KEY] [--out DIR]
#   bash scripts/h3_server_lock.sh --docker CONTAINER [--context CTX] --out DIR   (образ, собранный на стенде)
# По умолчанию пишет в deploy/live-lock/.
set -euo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"; out=''; container=''; context=colima-h3
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;;
    --identity) identity="$2"; shift 2 ;; --out) out="$2"; shift 2 ;;
    --docker) container="$2"; shift 2 ;; --context) context="$2"; shift 2 ;;
    *) echo "Usage: $0 --host root@HOST --port PORT [--identity PATH] [--out DIR]"; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" || -n "$container" ]] || { echo "Usage: $0 --host root@HOST --port PORT"; exit 2; }
cd "$(dirname "$0")/.."
out="${out:-deploy/live-lock}"
mkdir -p "$out"
ssh_run() { ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" "$@" 2>/dev/null; }
# Same commands inside a local container (infra/h3_image_build.sh): the tree is at the server paths.
[[ -z "$container" ]] || ssh_run() { docker --context "$context" exec -i "$container" sh -c "$*" 2>/dev/null; }

for v in venv qwen-venv qwen-next-venv api-venv liveportrait-venv audio/cosy-venv audio/ACE-Step-1.5/.venv; do
  ssh_run "[ -x /workspace/h3/$v/bin/python ] && /workspace/h3/$v/bin/python -" > "$out/${v//\//_}.txt" <<'PY' || true
import os, sys
from importlib import metadata
# Only the venv's own packages: servers deployed before 2026-10-04 run the ComfyUI venv with
# --system-site-packages, and the base image's system Python (jupyter, twisted, ...) is not ours to pin.
prefix = os.path.realpath(sys.prefix) + os.sep
for n in sorted({f"{d.metadata['Name'].lower().replace('_', '-')}=={d.version}"
                 for d in metadata.distributions()
                 if d.metadata['Name'] and os.path.realpath(str(d.locate_file(''))).startswith(prefix)}):
    print(n)
PY
  echo "  $v: $(wc -l < "$out/${v//\//_}.txt" | tr -d ' ') пакетов"
done

ssh_run 'cd /workspace/h3
for d in ComfyUI ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache ComfyUI/custom_nodes/h3-3D-camera-guide \
         ComfyUI/custom_nodes/ComfyUI-KJNodes ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite \
         ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation LivePortrait vendor/sam2; do
  [ -d "$d/.git" ] && echo "repo $(basename "$d") $(git -C "$d" -c safe.directory="*" rev-parse HEAD)"
done
find ComfyUI/models ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation/ckpts LivePortrait/pretrained_weights hf-cache/hub -type f -size +1M ! -name "*.part" ! -name "*.incomplete" \
  -printf "file %s %p\n" 2>/dev/null | sort -k3' > "$out/components.txt"
echo "  компоненты: $(grep -c '^repo' "$out/components.txt") репозиториев, $(grep -c '^file' "$out/components.txt") файлов весов"
echo "Эталон: $out"
