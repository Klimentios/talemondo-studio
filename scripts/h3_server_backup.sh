#!/usr/bin/env bash
# Резервная копия ВСЕХ настроек GPU-сервера на Mac: код API, скрипты, конфиги supervisor,
# кастомные ноды ComfyUI, workflows, база задач, input/output, плюс опись того, что не копируется
# (веса, venv): списки моделей с размерами, симлинки LoRA, pip freeze каждого venv, git-коммиты
# и локальные правки каждого репозитория. На сервере НИЧЕГО не меняет.
#   bash scripts/h3_server_backup.sh                       # хост/порт берутся из tunnel.sh
#   bash scripts/h3_server_backup.sh --host root@HOST --port PORT [--out DIR] [--identity KEY]
# Результат: ~/H3-Backups/server-<host>-<port>-<дата>/ (files.tar.gz + inventory/ + RESTORE.md).
# Восстановление на сервер: bash scripts/h3_server_restore.sh --backup DIR --host ... --port ...
set -euo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"; out_root="$HOME/H3-Backups"
tunnel="$HOME/Library/Application Support/MiniMax H3/tunnel.sh"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;;
    --identity) identity="$2"; shift 2 ;; --out) out_root="$2"; shift 2 ;;
    *) echo "Usage: $0 [--host root@HOST --port PORT] [--out DIR] [--identity KEY]"; exit 2 ;;
  esac
done
if [[ -z "$host" && -f "$tunnel" ]]; then
  host="root@$(sed -n 's/^SERVER_HOST="\(.*\)"/\1/p' "$tunnel")"
  port="$(sed -n 's/^SERVER_PORT="\(.*\)"/\1/p' "$tunnel")"
fi
[[ -n "$host" && -n "$port" ]] || { echo "Не знаю сервер: передай --host и --port"; exit 2; }
hostname_only="${host#*@}"
known="$HOME/.ssh/known_hosts_h3_${hostname_only}_${port}"
ssh_cmd=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=15
         -o UserKnownHostsFile="$known" -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "$host")

dest="$out_root/server-${hostname_only}-${port}-$(date +%Y%m%d-%H%M)"
mkdir -p "$dest/inventory"
chmod 700 "$out_root" "$dest"
echo "Сервер: $host:$port → $dest"

# Всё, что копируется: пути от / ; файлы >25 МБ не берём (это веса/архивы) — они попадают в big-files.tsv.
remote_script='
set -u
cd /
H=workspace/h3
roots="$H/api $H/api-data $H/deploy $H/scripts $H/backup $H/api-backups $H/audit-backups $H/input $H/output
  $H/ComfyUI/custom_nodes $H/ComfyUI/user $H/ComfyUI/extra_model_paths.yaml $H/ComfyUI/models/configs
  $H/H3-Studio $H/LivePortrait $H/vendor $H/audio/ACE-Step-1.5 $H/audio/CosyVoice $H/extra-loras-download.log
  etc/supervisor etc/caddy etc/cron.d opt/supervisor-scripts root/onstart.sh root/.bashrc root/.profile
  root/.config root/.condarc root/.ssh/authorized_keys workspace/CLAUDE.md workspace/AGENTS.md"
existing=""
for r in $roots; do [ -e "$r" ] && existing="$existing $r"; done
find $existing \( -name .git -o -name __pycache__ -o -name node_modules -o -name "*venv*" \) -prune -o \( -type f -o -type l \) -size -25M -print 2>/dev/null \
  | grep -v -E "\.pyc$" > /tmp/h3-backup-files.txt
tar -czf - --no-recursion -T /tmp/h3-backup-files.txt 2>/dev/null
'
echo "1/2 Копирую файлы…"
"${ssh_cmd[@]}" "bash -c $(printf %q "$remote_script")" > "$dest/files.tar.gz"
tar -tzf "$dest/files.tar.gz" > "$dest/inventory/files-in-archive.txt"

inventory_script='
set -u
H=/workspace/h3
section() { echo; echo "===== $1 ====="; }
{
  section host; hostname; date -u; uname -a; cat /etc/os-release 2>/dev/null | head -3
  section gpu; nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv 2>/dev/null; nvcc --version 2>/dev/null | tail -1
  section disk; df -h / /workspace 2>/dev/null
  section supervisor; supervisorctl status 2>/dev/null
  section ports; ss -ltnp 2>/dev/null | awk "NR==1||/LISTEN/"
  section crontab; crontab -l 2>/dev/null
  section env-names; env | cut -d= -f1 | sort   # только имена: значения могут быть секретами
} > /tmp/inv-system.txt 2>&1
# Веса: путь, размер, mtime. Симлинки отдельно (LoRA_Library -> models/loras).
find $H/ComfyUI/models $H/LoRA_Library $H/hf-cache $H/audio/ace-checkpoints $H/LivePortrait/pretrained_weights \
  -type f -printf "%p\t%s\t%TY-%Tm-%Td\n" 2>/dev/null | sort > /tmp/inv-weights.tsv
find $H -path "*/venv*" -prune -o -type l -printf "%p\t%l\n" 2>/dev/null | sort > /tmp/inv-symlinks.tsv
# Большие файлы вне весов, которые не попали в архив.
find $H \( -path "$H/ComfyUI/models" -o -path "$H/hf-cache" -o -path "$H/LoRA_Library" -o -name "*venv*" -o -name .git \) -prune -o -type f -size +25M -printf "%p\t%s\n" 2>/dev/null | sort > /tmp/inv-big-files.tsv
# Git: репозиторий, remote, коммит, ветка + патч локальных правок (чтобы их можно было наложить заново).
mkdir -p /tmp/inv-git; : > /tmp/inv-git.tsv
for d in $H/ComfyUI $H/ComfyUI/custom_nodes/* $H/LivePortrait $H/audio/ACE-Step-1.5 $H/audio/CosyVoice $H/H3-Studio $H/vendor/*; do
  [ -d "$d/.git" ] || continue
  c=$(git -C "$d" rev-parse HEAD 2>/dev/null); b=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
  r=$(git -C "$d" remote get-url origin 2>/dev/null | sed -E "s#//[^/@]*@#//#")
  n=$(git -C "$d" status --porcelain 2>/dev/null | wc -l)
  printf "%s\t%s\t%s\t%s\t%s\n" "$d" "$r" "$c" "$b" "$n" >> /tmp/inv-git.tsv
  safe=$(echo "${d#$H/}" | tr / _)
  git -C "$d" diff HEAD > /tmp/inv-git/$safe.patch 2>/dev/null; [ -s /tmp/inv-git/$safe.patch ] || rm -f /tmp/inv-git/$safe.patch
done
for d in $H/ComfyUI/custom_nodes/*; do [ -d "$d" ] && [ ! -d "$d/.git" ] && printf "%s\t(no git — код целиком в архиве)\n" "$d"; done >> /tmp/inv-git.tsv
# pip freeze каждого venv/conda-окружения.
mkdir -p /tmp/inv-pip
for py in $H/venv/bin/python $H/api-venv/bin/python $H/qwen-venv/bin/python $H/qwen-next-venv/bin/python \
          $H/liveportrait-venv/bin/python $H/audio/cosy-venv/bin/python $H/audio/ACE-Step-1.5/.venv/bin/python; do
  [ -x "$py" ] || continue
  name=$(echo "${py#$H/}" | sed "s#/bin/python##; s#/#_#g")
  # Не во всех venv есть pip (uv) — тогда список берём из importlib.metadata.
  { "$py" --version; "$py" -m pip freeze 2>/dev/null || "$py" -c "import importlib.metadata as m; print(chr(10).join(sorted(d.metadata[\"Name\"]+\"==\"+d.version for d in m.distributions())))"; } > /tmp/inv-pip/$name.txt 2>&1
done
dpkg-query -W -f="\${Package}\t\${Version}\n" 2>/dev/null > /tmp/inv-dpkg.tsv
cd /tmp && tar -czf - inv-system.txt inv-weights.tsv inv-symlinks.tsv inv-big-files.tsv inv-git.tsv inv-git inv-pip inv-dpkg.tsv
rm -rf /tmp/inv-* /tmp/h3-backup-files.txt
'
echo "2/2 Собираю опись (веса, пакеты, git)…"
"${ssh_cmd[@]}" "bash -c $(printf %q "$inventory_script")" | tar -xzf - -C "$dest/inventory"
( cd "$dest/inventory" && for f in inv-*; do mv "$f" "${f#inv-}"; done )

# Секреты в архив не кладём намеренно (HF/vast токены): проверим, не просочились ли.
if tar -xzOf "$dest/files.tar.gz" 2>/dev/null | grep -a -q -E 'hf_[A-Za-z0-9]{30,}'; then
  echo "ВНИМАНИЕ: в архиве найден HF-токен (hf_…). Папка закрыта chmod 700, не публикуй её."
fi

repo_commit="$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo '?')"
cat > "$dest/RESTORE.md" <<EOF
# Резервная копия сервера H3

- Сервер: \`$host -p $port\`, снято $(date '+%F %T') (репозиторий на Mac: $repo_commit)
- \`files.tar.gz\` — настройки, код и скрипты, пути от \`/\` (список: \`inventory/files-in-archive.txt\`)
- \`inventory/system.txt\` — GPU, драйвер, supervisor status, порты, crontab
- \`inventory/weights.tsv\` — все веса (путь, байты, дата); сами веса — в R2 / докачиваются h3_new_server.sh
- \`inventory/symlinks.tsv\` — симлинки (LoRA и т.п.), восстанавливать infra/h3_restore_symlinks.py
- \`inventory/git.tsv\` + \`inventory/git/*.patch\` — коммит каждого репозитория и его локальные правки
- \`inventory/pip/*.txt\` — pip freeze каждого venv; \`inventory/dpkg.tsv\` — системные пакеты
- \`inventory/big-files.tsv\` — файлы >25 МБ, которые в архив не вошли
- Секретов (HF-токен, ключ vast) в копии нет — они берутся из Keychain при установке.

## Как вернуть
1. Новый сервер целиком: \`bash scripts/h3_new_server.sh --host root@HOST --port PORT\` (или R2 \`infra/h3_up.sh\`).
2. Потом наложить настройки из этой копии поверх:
   \`bash scripts/h3_server_restore.sh --backup "$dest" --host root@HOST --port PORT\`
   (без \`--apply\` только показывает, что изменится; с \`--apply\` — копирует и перезапускает h3-сервисы).
3. Проверить: \`bash scripts/verify_h3_server.sh ...\`
EOF
chmod -R go-rwx "$dest"
size="$(du -sh "$dest" | cut -f1)"
echo "Готово: $dest ($size, файлов в архиве: $(wc -l < "$dest/inventory/files-in-archive.txt" | tr -d ' '), весов в описи: $(wc -l < "$dest/inventory/weights.tsv" | tr -d ' '))"
