#!/usr/bin/env bash
# Возвращает настройки из копии scripts/h3_server_backup.sh на сервер (обычно — на свежий, поднятый
# h3_new_server.sh). По умолчанию только показывает, что изменится; --apply копирует и перезапускает h3-сервисы.
#   bash scripts/h3_server_restore.sh --backup ~/H3-Backups/server-... --host root@HOST --port PORT [--apply]
# Веса и venv не трогает. Перед заменой сервер сам сохраняет свои текущие файлы в /workspace/h3-pre-restore-<дата>.tgz.
# input/output (медиа пользователя) возвращаются только с --with-media.
set -euo pipefail
backup=''; host=''; port=''; identity="$HOME/.ssh/id_ed25519"; apply=0; media=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --backup) backup="$2"; shift 2 ;; --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;;
    --identity) identity="$2"; shift 2 ;; --apply) apply=1; shift ;; --with-media) media=1; shift ;;
    *) echo "Usage: $0 --backup DIR --host root@HOST --port PORT [--apply] [--with-media]"; exit 2 ;;
  esac
done
[[ -f "$backup/files.tar.gz" && -n "$host" && -n "$port" ]] || { echo "Usage: $0 --backup DIR --host root@HOST --port PORT [--apply]"; exit 2; }
known="$HOME/.ssh/known_hosts_h3_${host#*@}_${port}"
ssh_cmd=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=15
         -o UserKnownHostsFile="$known" -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "$host")

work="$(mktemp -d -t h3-restore.XXXXXX)"; trap 'rm -rf "$work"' EXIT
# authorized_keys и sshd не трогаем: можно потерять доступ к серверу.
exclude=(--exclude 'root/.ssh' --exclude 'workspace/CLAUDE.md' --exclude 'workspace/AGENTS.md')
[[ $media == 1 ]] || exclude+=(--exclude 'workspace/h3/input' --exclude 'workspace/h3/output')
tar -xzf "$backup/files.tar.gz" -C "$work" "${exclude[@]}"
( cd "$work" && find . -type f | sed 's#^\./##' | sort ) > "$work.list"

# Сравнение: сервер считает md5 тех же путей, Mac — своих; отличие или отсутствие на сервере = будет записано.
"${ssh_cmd[@]}" 'cd / && xargs -d "\n" md5sum 2>/dev/null; true' < "$work.list" > "$work.remote"
changed="$(python3 - "$work" "$work.list" "$work.remote" <<'PY'
import hashlib, os, sys
root, listing, remote = sys.argv[1:]
have = {}
for line in open(remote, errors="replace"):
    digest, _, path = line.rstrip("\n").partition("  ")
    have[path] = digest
for path in open(listing).read().splitlines():
    local = os.path.join(root, path)
    if os.path.islink(local):
        continue
    if hashlib.md5(open(local, "rb").read()).hexdigest() != have.get(path):
        print(path)
PY
)"
n="$(printf '%s' "$changed" | grep -c . || true)"
echo "Файлов в копии: $(wc -l < "$work.list" | tr -d ' '); отличаются от сервера или отсутствуют там: $n"
printf '%s\n' "$changed" | head -60
[[ $n -gt 60 ]] && echo "  … и ещё $((n-60))"
[[ $n -eq 0 ]] && { echo "Сервер уже совпадает с копией."; exit 0; }
if [[ $apply != 1 ]]; then echo; echo "Это просмотр. Чтобы записать на сервер: добавь --apply"; exit 0; fi

stamp="$(date +%Y%m%d-%H%M)"
printf '%s\n' "$changed" > "$work.changed"
echo "Сохраняю текущие версии на сервере в /workspace/h3-pre-restore-$stamp.tgz…"
"${ssh_cmd[@]}" "cd / && xargs -d '\n' tar -czf /workspace/h3-pre-restore-$stamp.tgz --ignore-failed-read 2>/dev/null; true" < "$work.changed"
echo "Копирую…"
( cd "$work" && tar -czf - -T "$work.changed" ) | "${ssh_cmd[@]}" 'tar -xzf - -C /'
# Перезапуск только с пустой очередью ComfyUI: иначе обрываем чужой рендер.
"${ssh_cmd[@]}" 'q=$(curl -s 127.0.0.1:18188/queue | python3 -c "import sys,json;d=json.load(sys.stdin);print(len(d[\"queue_running\"])+len(d[\"queue_pending\"]))" 2>/dev/null || echo 0)
  if [ "$q" != "0" ]; then echo "В очереди ComfyUI $q задач — сервисы НЕ перезапущены. Перезапусти позже: supervisorctl reread && supervisorctl update && supervisorctl restart h3-api h3-comfyui"; exit 0; fi
  supervisorctl reread >/dev/null && supervisorctl update >/dev/null; supervisorctl restart h3-api h3-comfyui; supervisorctl status | grep h3-'
echo "Готово. Откат: ssh … 'tar -xzf /workspace/h3-pre-restore-$stamp.tgz -C /'"
