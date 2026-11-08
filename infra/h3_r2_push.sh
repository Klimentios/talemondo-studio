#!/usr/bin/env bash
# Golden image -> Cloudflare R2, run from the Mac against a verified reference server.
#   bash infra/h3_r2_push.sh --host root@HOST --port PORT [--identity KEY] [--no-weights] [--weights-from STAMP]
#   --no-weights: software only; weights.tsv is taken from --weights-from or current LATEST.
#
# Precondition: the server passed scripts/verify_h3_server.sh («ИТОГ: всё OK») and made an accepted
# test render. Uploads go straight from the server to R2 (nothing large passes through the Mac).
# Weights are content-addressed: files already in the bucket are not uploaded again.
# LATEST moves only after the whole snapshot is uploaded and verified.
set -euo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"; staging=0; no_weights=0; weights_from='LATEST'
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;; --identity) identity="$2"; shift 2 ;;
    --staging) staging=1; shift ;;
    --no-weights) no_weights=1; shift ;;
    --weights-from) weights_from="${2:?}"; shift 2 ;;
    *) sed -n '2,9p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { sed -n '2,9p' "$0" >&2; exit 2; }
[[ "$weights_from" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "Некорректный stamp весов" >&2; exit 2; }
cd "$(dirname "$0")/.."
# shellcheck source=h3_snapshot_spec.sh
. infra/h3_snapshot_spec.sh
# shellcheck source=h3_r2_common.sh
. infra/h3_r2_common.sh
ssh_cmd=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 "$host")
commit="${H3_SNAPSHOT_COMMIT:-HEAD}"  # what the server actually runs, if behind the repo
stamp="$(date -u +%Y%m%d-%H%M)-$(git rev-parse --short "$commit")"
echo "Снимок в R2: $H3_R2_BUCKET/snapshots/$stamp"

echo "== 1/5 Токены и ключи в софте"
leaks="$("${ssh_cmd[@]}" "$(h3_leak_scan_cmd)")"
[[ -z "$leaks" ]] || { echo "Стоп: в софте найдены токены или ключи:" >&2; echo "$leaks" >&2; exit 3; }
echo "  чисто"

echo "== 2/5 Опись весов (SHA-256) и паспорт"
work="$(mktemp -d -t h3-r2push.XXXXXX)"; trap 'rm -rf "$work"' EXIT
if [[ $no_weights == 1 ]]; then
  echo "  --no-weights: опись весов из $weights_from"
  h3_r2_ssh 'prev=$1; if [[ "$prev" == LATEST ]]; then prev=$("${RC[@]}" cat "$R2/snapshots/LATEST" | tr -d "[:space:]"); fi
[[ -n $prev ]] || { echo "Стоп: нет снимка весов" >&2; exit 4; }
"${RC[@]}" cat "$R2/snapshots/$prev/weights.tsv"' "$weights_from" > "$work/weights.tsv"
else
  "${ssh_cmd[@]}" "$(h3_weights_inventory_cmd)" > "$work/weights.tsv"
fi
"${ssh_cmd[@]}" 'cd /workspace/h3 && find ComfyUI/models -type l -printf "%p\t%l\n" | sort' | { grep -viE "$H3_BANNED_WEIGHTS_RE" || true; } > "$work/symlinks.tsv"
[[ -s "$work/weights.tsv" ]] || { echo "Стоп: на сервере не найдено весов" >&2; exit 4; }
awk -F'\t' '{n++; s+=$2} END {printf "  весов: %d файлов, %.1f ГБ\n", n, s/1e9}' "$work/weights.tsv"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "repo_commit: $(git rev-parse "$commit")"; echo "server: $host:$port"
  "${ssh_cmd[@]}" 'echo "os: $(. /etc/os-release; echo $PRETTY_NAME)"; echo "gpu: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null)"'
  grep -h '^base_image' "$(ls -d "$HOME"/H3-Snapshots/h3-* 2>/dev/null | sort | tail -n 1)/SNAPSHOT.txt" 2>/dev/null || true
} > "$work/SNAPSHOT.txt"

echo "== 3/5 Архив софта на сервере -> R2"
args="$(tr '\n' ' ' <<<"$H3_TAR_ARGS")"
h3_r2_ssh "stamp=\$1
tmp=/workspace/h3-r2-\$stamp.tar.zst
df -B1 --output=avail /workspace | tail -1 | awk -v min=\$2 '{exit !(\$1 > min*1e9)}' || { echo \"Меньше \$2 ГБ свободно для архива\" >&2; exit 4; }
cd / && tar --create $args workspace/h3 .uv \$(ls -d etc/supervisor/conf.d/h3-*.conf opt/supervisor-scripts/h3-*.sh 2>/dev/null) 2>/dev/null \
  | zstd -T0 -3 -q -o \$tmp.part && zstd -t -q \$tmp.part && mv \$tmp.part \$tmp
sha=\$(sha256sum \$tmp | cut -d' ' -f1)
\"\${RC[@]}\" --s3-chunk-size 100M --s3-upload-cutoff 100M --s3-upload-concurrency 8 copyto \$tmp \"\$R2/snapshots/\$stamp/software.tar.zst\"
printf '%s  software.tar.zst\n' \"\$sha\" > /tmp/h3-software.sha256
\"\${RC[@]}\" copyto /tmp/h3-software.sha256 \"\$R2/snapshots/\$stamp/software.sha256\"
rm -f \$tmp
echo \"  архив: \$sha\"" "$stamp" "${H3_MIN_FREE_GB:-30}"

echo "== 4/5 Веса -> R2 (только отсутствующие в бакете)"
if [[ $no_weights == 1 ]]; then echo "  пропущено (--no-weights)"; fi
"${ssh_cmd[@]}" 'cat > /tmp/h3-weights.tsv' < "$work/weights.tsv"
"${ssh_cmd[@]}" 'cat > /tmp/h3-symlinks.tsv' < "$work/symlinks.tsv"
"${ssh_cmd[@]}" 'cat > /tmp/h3-snapshot.txt' < "$work/SNAPSHOT.txt"
[[ $no_weights == 1 ]] || h3_r2_ssh 'cd /workspace/h3
"${RC[@]}" lsf "$R2/weights/" 2>/dev/null | sort > /tmp/h3-have.txt || : > /tmp/h3-have.txt
# path<TAB>sha of every weight whose SHA is not in the bucket yet (duplicates uploaded once).
awk -F"\t" "NF==3 && !seen[\$3]++ {print \$1 \"\t\" \$3}" /tmp/h3-weights.tsv \
  | while IFS=$'"'"'\t'"'"' read -r path sha; do grep -qx "$sha" /tmp/h3-have.txt || printf "%s\t%s\n" "$path" "$sha"; done > /tmp/h3-upload.tsv
echo "  к загрузке: $(wc -l < /tmp/h3-upload.tsv) файлов"
export R2
# 4 files in parallel, each with rclone multi-thread streams.
tr "\t" "\n" < /tmp/h3-upload.tsv | xargs -r -n 2 -P 4 sh -c '"'"'rclone --retries 5 --low-level-retries 20 --multi-thread-streams 8 --stats 0 copyto "$0" "$R2/weights/$1" </dev/null'"'"'
rm -f /tmp/h3-have.txt /tmp/h3-upload.tsv'

echo "== 5/5 Опись и паспорт, проверка полноты, LATEST"
h3_r2_ssh 'stamp=$1
"${RC[@]}" copyto /tmp/h3-weights.tsv "$R2/snapshots/$stamp/weights.tsv"
"${RC[@]}" copyto /tmp/h3-symlinks.tsv "$R2/snapshots/$stamp/symlinks.tsv"
"${RC[@]}" copyto /tmp/h3-snapshot.txt "$R2/snapshots/$stamp/SNAPSHOT.txt"
"${RC[@]}" lsf "$R2/weights/" | sort > /tmp/h3-have.txt
absent=$(cut -f3 /tmp/h3-weights.tsv | sort -u | comm -23 - /tmp/h3-have.txt | wc -l)
rm -f /tmp/h3-weights.tsv /tmp/h3-symlinks.tsv /tmp/h3-snapshot.txt /tmp/h3-have.txt
[[ $absent == 0 ]] || { echo "Стоп: в бакете нет $absent весов — LATEST не трогаю" >&2; exit 5; }
if [[ $2 == 0 ]]; then
  printf "%s\n" "$stamp" > /tmp/h3-latest
  "${RC[@]}" copyto /tmp/h3-latest "$R2/snapshots/LATEST"
  echo "  LATEST = $stamp"
else
  echo "  staging: LATEST не изменён"
fi' "$stamp" "$staging"

echo
echo "ВЫГРУЖЕНО: $H3_R2_BUCKET/snapshots/$stamp (staging=$staging)."
