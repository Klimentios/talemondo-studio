#!/usr/bin/env bash
# Скачать большой файл с сервера по SSH с докачкой: при обрыве запрашивается только недостающий хвост.
# Не нужен rsync на сервере (в образе Vast он может быть отключён).
#   bash infra/h3_fetch_resume.sh --host root@HOST --port PORT --remote /path/file --local ./file [--identity KEY]
# Выход 0 — размер и SHA-256 совпали с сервером.
set -euo pipefail
host=''; port=''; remote=''; local_file=''; identity="$HOME/.ssh/id_ed25519"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;; --remote) remote="$2"; shift 2 ;;
    --local) local_file="$2"; shift 2 ;; --identity) identity="$2"; shift 2 ;;
    *) sed -n '2,6p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" && -n "$remote" && -n "$local_file" ]] || { sed -n '2,6p' "$0" >&2; exit 2; }
ssh_run() { ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=4 "$host" "$@" 2>/dev/null; }

total="$(ssh_run "stat -c %s '$remote'")"
[[ "$total" =~ ^[0-9]+$ ]] || { echo "Нет файла на сервере: $remote" >&2; exit 3; }
touch "$local_file"
for attempt in $(seq 1 50); do
  have="$(stat -f %z "$local_file" 2>/dev/null || stat -c %s "$local_file")"
  (( have < total )) || break
  (( attempt == 1 )) || echo "  обрыв на $((have / 1048576)) МиБ из $((total / 1048576)) — докачка (попытка $attempt)"
  # tail -c +N отдаёт файл начиная с байта N (нумерация с 1).
  ssh_run "tail -c +$((have + 1)) '$remote'" >> "$local_file" || sleep 10
done
have="$(stat -f %z "$local_file" 2>/dev/null || stat -c %s "$local_file")"
(( have == total )) || { echo "Стоп: скачано $have из $total байт" >&2; exit 4; }
remote_sha="$(ssh_run "sha256sum '$remote' | cut -d' ' -f1")"
local_sha="$(shasum -a 256 "$local_file" | cut -d' ' -f1)"
[[ "$remote_sha" == "$local_sha" ]] || { echo "Стоп: SHA-256 не совпал (сервер $remote_sha, Mac $local_sha)" >&2; exit 5; }
echo "$local_sha"
