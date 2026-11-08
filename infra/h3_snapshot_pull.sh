#!/usr/bin/env bash
# Снимок рабочего GPU-сервера H3 без весов → на Mac. Ничего не меняет на сервере (только читает).
#   bash infra/h3_snapshot_pull.sh --host root@HOST --port PORT [--out ~/H3-Snapshots] [--identity KEY]
#
# В папке снимка:
#   software.tar.zst      весь софт и конфиги: /workspace/h3 (без весов и данных), /.uv (Python 3.10/3.11 для аудио),
#                         /etc/supervisor/conf.d/h3-*.conf, /opt/supervisor-scripts/h3-*.sh
#   software.sha256       контрольная сумма архива
#   weights.tsv           каждый файл весов: путь, размер, SHA-256 (для выгрузки в хранилище и проверки при развёртывании)
#   lock/                 эталон «один в один» (версии пакетов, ревизии, веса) — тот же формат, что deploy/live-lock
#   SNAPSHOT.txt          базовый образ Vast (тег + digest), GPU, драйвер, коммит репозитория, дата
# Токены и ключи в снимок не попадают: перед упаковкой сервер проверяется на hf_*, приватные ключи и ключи AWS.
set -euo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"; out="$HOME/H3-Snapshots"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;;
    --identity) identity="$2"; shift 2 ;; --out) out="$2"; shift 2 ;;
    *) sed -n '2,14p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { sed -n '2,14p' "$0" >&2; exit 2; }
cd "$(dirname "$0")/.."
ssh_run() { ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 "$host" "$@"; }

stamp="$(date +%Y%m%d-%H%M)"
dir="$out/h3-$stamp"
mkdir -p "$dir/lock"
echo "Снимок: $dir"

# Состав архива и опись весов — общие с infra/h3_r2_push.sh.
# shellcheck source=h3_snapshot_spec.sh
. "infra/h3_snapshot_spec.sh"
TAR_ARGS="$H3_TAR_ARGS"; WEIGHT_DIRS="$H3_WEIGHT_DIRS"

echo "== 1/4 Проверка: в снимок не попадут токены и ключи"
leaks="$(ssh_run "$(h3_leak_scan_cmd)")"
if [[ -n "$leaks" ]]; then
  echo "Стоп: найдены файлы с токенами или ключами — снимок не делаю:" >&2; echo "$leaks" >&2; exit 3
fi
echo "  чисто"

echo "== 2/4 Опись: эталон пакетов/ревизий и список весов с SHA-256"
bash scripts/h3_server_lock.sh --host "$host" --port "$port" --identity "$identity" --out "$dir/lock" >/dev/null
ssh_run "$(h3_weights_inventory_cmd)" > "$dir/weights.tsv"
awk -F'\t' '{n++; s+=$2} END {printf "  весов: %d файлов, %.1f ГБ\n", n, s/1e9}' "$dir/weights.tsv"

echo "== 3/4 Паспорт снимка"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "server: $host:$port"
  echo "repo_commit: $(git rev-parse HEAD)"
  ssh_run 'echo "base_image: $(cd /opt/vast-cli 2>/dev/null && python3 vast.py show instance $CONTAINER_ID --raw --api-key "$CONTAINER_API_KEY" 2>/dev/null \
      | python3 -c "import json,sys; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d.get(\"image_uuid\"))" 2>/dev/null)"
    echo "os: $(. /etc/os-release; echo $PRETTY_NAME)"
    echo "gpu: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"
    echo "cuda_toolkit: $(nvcc --version | tail -1)"
    echo "python: $(python3 -V)"'
  echo "supervisor_programs:"; ssh_run 'ls /etc/supervisor/conf.d/h3-*.conf /opt/supervisor-scripts/h3-*.sh' | sed 's/^/  /'
} > "$dir/SNAPSHOT.txt"
# The base image tag is floating: pin the digest it pointed to. Read from Docker Hub on the Mac (the
# container cannot see its own image digest). If tag_last_pushed is later than the server's rent date,
# the server runs an older digest: take it from the Vast instance page by hand (docs/reference/H3_GOLDEN_IMAGE_PLAN.md).
base_tag="$(sed -n 's/^base_image: //p' "$dir/SNAPSHOT.txt")"
{ [[ "$base_tag" == */*:* ]] && curl -fsS --max-time 20 \
    "https://hub.docker.com/v2/repositories/${base_tag%%:*}/tags/${base_tag##*:}" 2>/dev/null \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("base_image_digest:", d["digest"]); print("base_image_tag_last_pushed:", d.get("tag_last_pushed"))' \
  || echo "base_image_digest: unknown (see docs/reference/H3_GOLDEN_IMAGE_PLAN.md)"; } >> "$dir/SNAPSHOT.txt"
cat "$dir/SNAPSHOT.txt"

echo "== 4/4 Архив софта: собрать на диске сервера, скачать с докачкой (SSH), удалить с сервера"
args="$(tr '\n' ' ' <<<"$TAR_ARGS")"
remote="/workspace/h3-snapshot-$stamp.tar.zst"
ssh_run "df -B1 --output=avail /workspace | tail -1 | awk '{exit !(\$1 > 30e9)}'" \
  || { echo "Стоп: на сервере меньше 30 ГБ свободно — архив не собрать" >&2; exit 4; }
# Архив пишется во временное имя и переименовывается только целым: обрыв SSH не оставит «готовый» битый файл.
ssh_run "cd / && tar --create $args workspace/h3 .uv \$(ls -d etc/supervisor/conf.d/h3-*.conf opt/supervisor-scripts/h3-*.sh) 2>/dev/null \
  | zstd -T0 -3 -q -o $remote.tmp && zstd -t -q $remote.tmp && mv $remote.tmp $remote && sha256sum $remote | cut -d' ' -f1" > "$dir/software.remote.sha256"
local_sha="$(bash infra/h3_fetch_resume.sh --host "$host" --port "$port" --identity "$identity" \
  --remote "$remote" --local "$dir/software.tar.zst")" || exit 5
if [[ "$local_sha" == "$(cat "$dir/software.remote.sha256")" ]]; then
  echo "$local_sha  software.tar.zst" > "$dir/software.sha256"; rm -f "$dir/software.remote.sha256"
  ssh_run "rm -f $remote"
  echo "  архив цел (SHA-256 совпал с сервером): $(du -h "$dir/software.tar.zst" | cut -f1)"
else
  echo "Стоп: SHA-256 архива на Mac не совпал с сервером — запустите снова (докачка продолжится)" >&2; exit 5
fi
echo
echo "ГОТОВО: $dir"
