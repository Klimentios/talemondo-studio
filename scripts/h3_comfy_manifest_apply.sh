#!/usr/bin/env bash
# Studio ComfyUI manifest (nodes + models the user added in «Узлы и модели») onto the GPU server.
#   bash scripts/h3_comfy_manifest_apply.sh --host root@HOST --port PORT --manifest FILE [--identity KEY] [--apply]
# Without --apply: plan only. With --apply: installs, then restarts h3-comfyui only if something changed
# and ComfyUI's queue is empty (otherwise prints the restart command). The «Применить на GPU» runbook.
set -euo pipefail
host=''; port=22; identity="$HOME/.ssh/id_ed25519"; manifest=''; apply=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:?}"; shift 2 ;; --port) port="${2:?}"; shift 2 ;;
    --identity) identity="${2:?}"; shift 2 ;; --manifest) manifest="${2:?}"; shift 2 ;;
    --apply) apply=1; shift ;;
    *) sed -n '2,6p' "$0" >&2; exit 2 ;;
  esac
done
[[ "$host" == *@* && -f "$manifest" ]] || { sed -n '2,6p' "$0" >&2; exit 2; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ssh_opts=(-i "$identity" -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10
  -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new)
remote_dir=/workspace/h3/studio-manifest
echo "== манифест → $host:$remote_dir"
ssh "${ssh_opts[@]}" "$host" "mkdir -p $remote_dir"
scp -q -i "$identity" -P "$port" -o BatchMode=yes -o IdentitiesOnly=yes \
  "$manifest" "$here/install_comfy_manifest.py" "$host:$remote_dir/"
flag=''; [[ "$apply" == 1 ]] && flag='--apply'
echo "== установка ${flag:-(план)}"
ssh "${ssh_opts[@]}" "$host" "bash -s" <<REMOTE
set -euo pipefail
root=\${H3_ROOT:-/workspace/h3}
out=\$(python3 $remote_dir/install_comfy_manifest.py --comfy "\$root/ComfyUI" --python "\$root/venv/bin/python" \
  --manifest $remote_dir/$(basename "$manifest") $flag) || { code=\$?; echo "\$out"; exit \$code; }
echo "\$out"
changed=\$(sed -n 's/^CHANGED=//p' <<<"\$out")
if [[ "$apply" == 1 && "\${changed:-0}" != 0 ]]; then
  q=\$(curl -fsS --max-time 5 http://127.0.0.1:18188/queue | python3 -c 'import sys,json;d=json.load(sys.stdin);print(len(d["queue_running"])+len(d["queue_pending"]))' 2>/dev/null || echo "?")
  if [[ "\$q" == 0 ]]; then
    supervisorctl restart h3-comfyui >/dev/null
    for _ in \$(seq 1 90); do curl -fsS --max-time 3 http://127.0.0.1:18188/queue >/dev/null 2>&1 && break; sleep 2; done
    echo "ComfyUI перезапущен: новые узлы загружены"
  else
    echo "В очереди ComfyUI \$q задач — перезапуск отложен: supervisorctl restart h3-comfyui"
  fi
fi
REMOTE
echo "ГОТОВО"
