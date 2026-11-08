#!/usr/bin/env bash
# Delete old software snapshots in R2, keeping the one LATEST points to. Weights (weights/) are never touched.
#   bash infra/h3_r2_prune.sh --host root@HOST --port PORT [--identity KEY] [--yes]
# Without --yes it only lists what would be deleted. Deletion is permanent.
set -euo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"; yes=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;; --identity) identity="$2"; shift 2 ;;
    --yes) yes=1; shift ;;
    *) sed -n '2,4p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { sed -n '2,4p' "$0" >&2; exit 2; }
cd "$(dirname "$0")/.."
# shellcheck source=h3_snapshot_spec.sh
. infra/h3_snapshot_spec.sh
# shellcheck source=h3_r2_common.sh
. infra/h3_r2_common.sh
ssh_cmd=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o LogLevel=ERROR "$host")
h3_r2_ssh 'yes=$1
latest=$("${RC[@]}" cat "$R2/snapshots/LATEST" | tr -d "[:space:]")
[[ -n $latest ]] && "${RC[@]}" lsf "$R2/snapshots/$latest/software.tar.zst" | grep -q . \
  || { echo "Стоп: LATEST пуст или без software.tar.zst — ничего не удаляю" >&2; exit 4; }
echo "Оставляю: $latest"
"${RC[@]}" lsf --dirs-only "$R2/snapshots/" | sed "s#/\$##" | grep -vx "$latest" | while read -r d; do
  if [[ $yes == 1 ]]; then "${RC[@]}" purge "$R2/snapshots/$d" && echo "  удалён $d"; else echo "  к удалению: $d"; fi
done
[[ $yes == 1 ]] || echo "Проверка без удаления. Удалить: добавьте --yes"' "$yes"
