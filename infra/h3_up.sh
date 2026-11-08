#!/usr/bin/env bash
# New GPU server from the golden image: no build, ~10-15 min from R2 (longer from a local snapshot).
#   bash infra/h3_up.sh --host root@HOST --port PORT [--stamp STAMP] [--identity KEY]      image from Cloudflare R2
#        [--keep-paused] [--qwen sleep|guard]   passed on to scripts/h3_new_server.sh
#   bash infra/h3_up.sh --host root@HOST --port PORT --snapshot DIR [--identity KEY]       image from a snapshot folder
#
# --snapshot DIR: a folder like ~/H3-Snapshots/h3-<stamp> or "/Volumes/NEO Storage/H3-Snapshots/h3-..."
# (infra/h3_snapshot_pull.sh or infra/h3_image_build.sh) when R2 is not set up yet: software.tar.zst is
# uploaded over SSH with resume and checked by SHA-256; weights come from their sources in weights.tsv
# (Hugging Face pinned revision / URL, each checked by SHA-256; HF token from $HF_TOKEN or the Keychain).
# Server: Vast template on vastai/base-image:cuda-12.8.1-auto pinned by the digest in SNAPSHOT.txt,
# disk >= 500 GB. Steps: software archive (SHA-256) -> weights in parallel, each verified by SHA-256 ->
# scripts/h3_new_server.sh over it (current master: code, configs, new packages/weights; Qwen mode;
# verify_h3_server.sh; Mac tunnel). Any snapshot ends up = master.
# Never deletes anything on the server; refuses a non-empty /workspace/h3 unless it is the same stamp.
set -euo pipefail
host=''; port=''; stamp='LATEST'; identity="$HOME/.ssh/id_ed25519"; snapshot=''
pass=()  # expanded as ${pass[@]+...}: Bash 3.2 nounset fails on an empty array
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;; --stamp) stamp="$2"; shift 2 ;;
    --identity) identity="$2"; shift 2 ;; --snapshot) snapshot="$2"; shift 2 ;;
    --keep-paused) pass+=("$1"); shift ;; --qwen) pass+=("$1" "$2"); shift 2 ;;
    *) sed -n '2,15p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { sed -n '2,15p' "$0" >&2; exit 2; }
cd "$(dirname "$0")/.."
# shellcheck source=h3_r2_common.sh
. infra/h3_r2_common.sh
# shellcheck source=h3_snapshot_spec.sh
. infra/h3_snapshot_spec.sh
ssh_cmd=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 "$host")
started=$(date +%s)
step() { printf '\n=== %s (%d с)\n' "$1" "$(( $(date +%s) - started ))"; }

# Deploy an immutable commit. If the user's checkout has code/test edits, use a
# temporary detached worktree at HEAD; never stage, stash, or overwrite that work.
deploy_paths=(app local_studio preview/h3-director scripts infra vendor deploy tests)
dirty_deploy="$(git status --porcelain -- "${deploy_paths[@]}")"
if [[ -n "$dirty_deploy" ]]; then
  if [[ -n "${H3_UP_CLEAN_WORKTREE:-}" ]]; then
    echo "Стоп: временная рабочая копия развёртывания неожиданно изменилась:" >&2
    git status --short -- "${deploy_paths[@]}" >&2
    exit 2
  fi
  echo "В рабочей копии есть незакоммиченный код; запускаю последний commit $(git rev-parse --short HEAD) отдельно. Ваши файлы останутся нетронуты."
  clean_worktree="$(mktemp -d "${TMPDIR:-/tmp}/h3-up-worktree.XXXXXX")"
  rmdir "$clean_worktree"
  cleanup_worktree() {
    git worktree remove --force "$clean_worktree" >/dev/null 2>&1 || rm -rf "$clean_worktree"
  }
  trap cleanup_worktree EXIT
  git worktree add --quiet --detach "$clean_worktree" HEAD
  args=(--host "$host" --port "$port" --identity "$identity")
  if [[ -n "$snapshot" ]]; then args+=(--snapshot "$snapshot"); else args+=(--stamp "$stamp"); fi
  args+=(${pass[@]+"${pass[@]}"})
  set +e
  H3_UP_CLEAN_WORKTREE=1 bash "$clean_worktree/infra/h3_up.sh" "${args[@]}"
  result=$?
  set -e
  exit "$result"
fi
hf_probe="${HF_TOKEN:-$(security find-generic-password -s h3-hf-token -a h3 -w 2>/dev/null || true)}"
[[ "$hf_probe" =~ ^hf_[A-Za-z0-9]+$ ]] || {
  unset hf_probe
  echo "Нет приватного Hugging Face токена в HF_TOKEN или связке ключей h3-hf-token; остановка до загрузки." >&2
  exit 2
}
unset hf_probe

# HF token over stdin only (never argv/disk on the Mac): curl + aria2 configs, 0600, same files the installers use.
push_hf_token() {
  local hf_line="${HF_TOKEN:-$(security find-generic-password -s h3-hf-token -w 2>/dev/null || true)}"
  [[ "$hf_line" =~ ^hf_[A-Za-z0-9]+$ ]] || { echo "  токена Hugging Face нет — всё качается из R2"; return 0; }
  printf '%s\n' "$hf_line" | "${ssh_cmd[@]}" 'umask 077; mkdir -p /root/.cache; read -r t;
    printf "header = \"Authorization: Bearer %s\"\n" "$t" > /root/.cache/h3-hf-curl.conf;
    printf "header=Authorization: Bearer %s\n" "$t" > /root/.cache/h3-hf-aria2.conf'
}

restore_model_links() {
  "${ssh_cmd[@]}" 'cat > /tmp/h3_restore_symlinks.py' < infra/h3_restore_symlinks.py
  "${ssh_cmd[@]}" 'python3 /tmp/h3_restore_symlinks.py /tmp/h3-symlinks.tsv /workspace/h3'
}

preflight_gpu() {
  "${ssh_cmd[@]}" 'bash -s' < infra/h3_target_preflight.sh
}

# Space and "same image" guard; expects /tmp/h3-weights.tsv and /tmp/h3-snapshot.txt on the server.
# weights.tsv: path, size, sha256 (+ source, revision, source_path in images built by h3_image_build.sh).
check_server='stamp=$1
# Count only files that still need to be fetched; a prefetch on the target must not be counted twice.
need=$(while IFS=$(printf '\t') read -r p size rest; do
  [[ -n "$p" ]] || continue
  path="/workspace/h3/$p"
  [[ -f "$path" && "$(stat -c %s "$path")" == "$size" ]] || printf "%s\n" "$size"
done < /tmp/h3-weights.tsv | awk -v margin="$2" "{s+=\$1} END {printf \"%d\", s + margin*1e9}")
mkdir -p /workspace
avail=$(df -B1 --output=avail /workspace | tail -1)
[[ $avail -gt $need ]] || { echo "Мало места: свободно $((avail/1000000000)) ГБ, нужно $((need/1000000000)) ГБ" >&2; exit 4; }
if [[ -f /workspace/h3/.h3-image-stamp && "$(cat /workspace/h3/.h3-image-stamp)" != "$stamp" ]]; then
  echo "На сервере уже другой образ ($(cat /workspace/h3/.h3-image-stamp)) — не трогаю" >&2; exit 3; fi
if [[ ! -f /workspace/h3/.h3-image-stamp && -n "$(ls -A /workspace/h3 2>/dev/null)" &&
      "$(cat /tmp/h3-stamp 2>/dev/null || true)" != "$stamp" ]]; then
  echo "/workspace/h3 не пуст и не из образа — не трогаю" >&2; exit 3; fi
echo "$stamp" > /tmp/h3-stamp
echo "  снимок $stamp: $(wc -l < /tmp/h3-weights.tsv) весов, $(awk -F"\t" "{s+=\$2} END {printf \"%.0f\", s/1e9}" /tmp/h3-weights.tsv) ГБ"
grep -h "^base_image\|^build" /tmp/h3-snapshot.txt || true'

# Restore on disk, never in /dev/shm (a 258 GB RAM limit restarted the container once).
unpack_server='stamp=$(cat /tmp/h3-stamp)
(cd /workspace && sed "s#software.tar.zst#h3-software.tar.zst#" /tmp/h3-software.sha256 | sha256sum -c --quiet)
zstd -dc -T0 /workspace/h3-software.tar.zst | tar -x -C / --numeric-owner
rm -f /workspace/h3-software.tar.zst
# Older software archives can carry --high-ram, which caused host swap. Keep it off
# until a separate RAM/VRAM measurement justifies enabling it for this instance.
if [[ -f /workspace/h3/comfy.env ]]; then
  sed -i "s/^H3_COMFY_EXTRA_ARGS=.*/H3_COMFY_EXTRA_ARGS=\"\"/" /workspace/h3/comfy.env
fi
# Snapshots come from Vast containers, where supervisor logs go to /dev/stdout. On a plain VM (Nebius,
# no /.dockerenv) systemd-run supervisord fails on that with ENXIO, so log to files there.
if [[ ! -f /.dockerenv ]]; then
  sed -i "s#^stdout_logfile=/dev/stdout#stdout_logfile=/var/log/%(program_name)s.log#; s#^stdout_logfile_maxbytes=0#stdout_logfile_maxbytes=50MB#" /etc/supervisor/conf.d/h3-*.conf 2>/dev/null || true
fi
echo "$stamp" > /workspace/h3/.h3-image-stamp
echo "  софт на месте"'

# SHA-256 of every weight; corrupt ones (same size, wrong bytes) are moved aside so a rerun fetches them again.
verify_weights='cd /workspace/h3
awk -F"\t" "NF>=3 {print \$3 \"  \" \$1}" /tmp/h3-weights.tsv | xargs -d "\n" -n 64 -P 8 sh -c '"'"'printf "%s\n" "$@" | sha256sum -c --quiet'"'"' _ \
  > /tmp/h3-sha.log 2>&1 || true
bad=$(sed -n "s/: FAILED.*//p" /tmp/h3-sha.log)
if [[ -n "$bad" ]]; then
  while IFS= read -r path; do mv -f "$path" "$path.bad"; done <<<"$bad"
  echo "Стоп: SHA-256 не совпал у $(wc -l <<<"$bad") файл(ов), отложены как .bad — запусти h3_up.sh ещё раз:" >&2
  sed "s/^/  /" <<<"$bad" >&2; exit 5
fi
# The archive excludes ComfyUI/models: files git tracks there (put_*_here placeholders, configs) come
# back from git, otherwise the base installer sees a dirty pinned checkout and refuses (step 4).
git -c safe.directory="*" -C ComfyUI checkout -- models
echo "  все $(wc -l < /tmp/h3-weights.tsv) весов совпали по SHA-256"'

if [[ -z "$snapshot" ]]; then
  step "1/4 Снимок в R2 и место на сервере"
  h3_r2_ssh 'stamp=$1
# No LATEST object yet (2026-10-08). rclone cat of a missing object prints nothing and exits 0, so an
# empty answer (not the exit code) means: take the newest dated snapshot that has software.sha256.
if [[ "$stamp" == LATEST ]]; then
  stamp=$("${RC[@]}" cat "$R2/snapshots/LATEST" 2>/dev/null | tr -d "[:space:]" || true)
  if [[ -z "$stamp" ]]; then
    dirs=$("${RC[@]}" lsf --dirs-only "$R2/snapshots/" | tr -d / | { grep -E "^[0-9]{8}-[0-9]{4}-" || true; } | sort -r)
    for s in $dirs; do
      if [[ -n "$("${RC[@]}" lsf --files-only --include software.sha256 "$R2/snapshots/$s/" 2>/dev/null)" ]]; then
        stamp=$s; break
      fi
    done
  fi
fi
[[ -n "$stamp" ]] || { echo "В R2 нет ни одного снимка" >&2; exit 3; }
"${RC[@]}" copyto "$R2/snapshots/$stamp/weights.tsv" /tmp/h3-weights.tsv
"${RC[@]}" copyto "$R2/snapshots/$stamp/symlinks.tsv" /tmp/h3-symlinks.tsv 2>/dev/null || : > /tmp/h3-symlinks.tsv
for f in /tmp/h3-weights.tsv /tmp/h3-symlinks.tsv; do { grep -viE '"'$H3_BANNED_WEIGHTS_RE'"' "$f" || true; } > "$f.ok"; mv "$f.ok" "$f"; done
"${RC[@]}" copyto "$R2/snapshots/$stamp/software.sha256" /tmp/h3-software.sha256
"${RC[@]}" copyto "$R2/snapshots/$stamp/SNAPSHOT.txt" /tmp/h3-snapshot.txt
set -- "$stamp" "$2"
'"$check_server" "$stamp" "${H3_UP_MARGIN_GB:-60}"

  step "0/4 Проверка NVIDIA/CUDA и VRAM до скачивания"
  preflight_gpu

  # One parallel stage, no waiting between sources (2026-10-07: the serial archive -> unpack -> R2 weights
  # path took 3 h at ~30 MB/s): the software archive (then unpack), the H3 models from Hugging Face
  # (Comfy-Org/MiniMax-H3, aria2c 16 connections, resumable) and every other weight from R2 run at once.
  # Every file is still checked against the R2 manifest SHA-256 at the end.
  step "2/4 Софт + LoRA/веса из R2 + модели H3 с Hugging Face — одновременно"
  push_hf_token
  h3_r2_ssh 'stamp=$(cat /tmp/h3-stamp)
cd /workspace/h3 2>/dev/null || { mkdir -p /workspace/h3; cd /workspace/h3; }
command -v aria2c >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq aria2 >/dev/null 2>&1 \
  || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq aria2 >/dev/null; }
export R2
: > /tmp/h3-q-r2; : > /tmp/h3-q-hf
awk -F"\t" "NF>=3 {print \$2 \"\t\" \$1 \"\t\" \$3}" /tmp/h3-weights.tsv | sort -t$'"'"'\t'"'"' -k1,1rn | while IFS=$'"'"'\t'"'"' read -r size path sha; do
  [[ -f "$path" && "$(stat -c %s "$path")" == "$size" ]] && continue
  case "$path" in
    ComfyUI/models/diffusion_models/minimax_h3_*|ComfyUI/models/text_encoders/qwen3vl_32b_minimax_h3_*|ComfyUI/models/vae/minimax_h3_*)
      [[ -s /root/.cache/h3-hf-aria2.conf ]] && { printf "%s\n%s\n" "https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/main/${path#ComfyUI/models/}" "$path" >> /tmp/h3-q-hf; continue; } ;;
  esac
  printf "%s\n%s\n%s\n" "$sha" "$path" "$size" >> /tmp/h3-q-r2
done
total=$(awk -F"\t" "NF>=3 {s+=\$2} END {printf \"%d\", s}" /tmp/h3-weights.tsv)
echo "  R2: $(( $(wc -l < /tmp/h3-q-r2) / 3 )) файлов, Hugging Face: $(( $(wc -l < /tmp/h3-q-hf) / 2 )) файлов"
pids=()
if [[ ! -f /workspace/h3/.h3-image-stamp ]]; then
  ( "${RC[@]}" --multi-thread-streams 16 copyto "$R2/snapshots/$stamp/software.tar.zst" /workspace/h3-software.tar.zst && echo "  архив софта скачан, распаковка"
'"$unpack_server"' ) > /tmp/h3-dl-soft.log 2>&1 & pids+=($!)
fi
xargs -r -n 3 -P 8 sh -c '\''mkdir -p "$(dirname "$1")" && rclone --retries 5 --low-level-retries 20 --multi-thread-streams 16 --multi-thread-cutoff 64M --buffer-size 64M --stats 0 copyto "$R2/weights/$0" "$1.part" && mv "$1.part" "$1"'\'' < /tmp/h3-q-r2 > /tmp/h3-dl-r2.log 2>&1 & pids+=($!)
xargs -r -d "\n" -n 2 -P 4 sh -c '\''mkdir -p "$(dirname "$1")" && aria2c -q --continue=true -x 16 -s 16 -k 64M --file-allocation=none --max-tries=10 --retry-wait=5 --conf-path=/root/.cache/h3-hf-aria2.conf --dir="$(dirname "$1")" --out="$(basename "$1").part" "$0" && mv "$1.part" "$1"'\'' < /tmp/h3-q-hf > /tmp/h3-dl-hf.log 2>&1 & pids+=($!)
iface=$(ip route | awk "/default/ {print \$5; exit}")
rx() { cat "/sys/class/net/$iface/statistics/rx_bytes"; }
start=$(date +%s); r0=$(rx); prev=$r0
while [[ -n "$(jobs -rp)" ]]; do
  sleep 30; now=$(rx)
  echo "  $(( ($(date +%s) - start) / 60 )) мин: получено $(( (now - r0) / 1000000000 )) ГБ из ~$(( total / 1000000000 + 22 )), сейчас $(( (now - prev) / 30 / 1000000 )) МБ/с"
  prev=$now
done
fail=0; for p in "${pids[@]}"; do wait "$p" || fail=1; done
cat /tmp/h3-dl-soft.log 2>/dev/null | tail -3
(( fail == 0 )) || { echo "Стоп: загрузка оборвалась (логи /tmp/h3-dl-*.log). Запустите h3_up.sh снова — продолжит с места." >&2; tail -5 /tmp/h3-dl-r2.log /tmp/h3-dl-hf.log >&2; exit 5; }
'"$verify_weights"
else
  for f in software.tar.zst software.sha256 weights.tsv SNAPSHOT.txt; do
    [[ -f "$snapshot/$f" ]] || { echo "Нет $snapshot/$f" >&2; exit 2; }
  done
  stamp="$(basename "$snapshot")"
  step "1/4 Снимок $snapshot и место на сервере"
  { grep -viE "$H3_BANNED_WEIGHTS_RE" "$snapshot/weights.tsv" || true; } \
    | "${ssh_cmd[@]}" 'cat > /tmp/h3-weights.tsv'
  { grep -viE "$H3_BANNED_WEIGHTS_RE" "$snapshot/symlinks.tsv" 2>/dev/null || true; } \
    | "${ssh_cmd[@]}" 'cat > /tmp/h3-symlinks.tsv'
  "${ssh_cmd[@]}" 'cat > /tmp/h3-snapshot.txt' < "$snapshot/SNAPSHOT.txt"
  "${ssh_cmd[@]}" 'cat > /tmp/h3-software.sha256' < "$snapshot/software.sha256"
  "${ssh_cmd[@]}" "bash -s -- $stamp ${H3_UP_MARGIN_GB:-60}" <<<"set -euo pipefail
$check_server"

  step "0/4 Проверка NVIDIA/CUDA и VRAM до загрузки"
  preflight_gpu

  step "2/4 Софт: загрузка архива по SSH с докачкой, SHA-256, распаковка"
  # Resume: ask the server how much it has and send only the tail (no rsync needed on either side;
  # works from an exFAT external disk). Retries on a dropped connection.
  remote=/workspace/h3-software.tar.zst
  total="$(stat -f %z "$snapshot/software.tar.zst")"
  for attempt in $(seq 1 50); do
    have="$("${ssh_cmd[@]}" "stat -c %s $remote 2>/dev/null || echo 0")"
    (( have < total )) || break
    echo "  отправлено $((have / 1048576)) из $((total / 1048576)) МиБ (попытка $attempt)"
    tail -c +$((have + 1)) "$snapshot/software.tar.zst" | "${ssh_cmd[@]}" "cat >> $remote" || sleep 10
  done
  (( have == total )) || { echo "Стоп: архив не догружен ($have из $total байт) — запустите снова" >&2; exit 5; }
  "${ssh_cmd[@]}" "bash -s" <<<"set -euo pipefail
command -v zstd >/dev/null || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd >/dev/null; }
$unpack_server"

  step "3/4 Веса из источников weights.tsv (Hugging Face по ревизии / URL), каждая по SHA-256"
  push_hf_token
  "${ssh_cmd[@]}" "bash -s" <<<'set -euo pipefail
cd /workspace/h3
# Columns 4-6: source (HF repo or URL), revision, path in the repo; "-" = only on a server/R2 copy.
awk -F"\t" "NF>=6" /tmp/h3-weights.tsv | while IFS=$'"'"'\t'"'"' read -r path size sha source rev src_path; do
  [[ -f "$path" && "$(stat -c %s "$path")" == "$size" ]] && continue
  case "$source" in
    -) echo "  нет источника, пропуск: $path" >&2; continue ;;
    https://*) url="$source" ;;
    *) url="https://huggingface.co/$source/resolve/$rev/$src_path" ;;
  esac
  printf "%s\n%s\n" "$url" "$path"
done | xargs -r -d "\n" -n 2 -P 6 sh -c '"'"'mkdir -p "$(dirname "$1")"
  conf=""; case "$0" in https://huggingface.co/*) [ -s /root/.cache/h3-hf-curl.conf ] && conf="--config /root/.cache/h3-hf-curl.conf" ;; esac
  curl -fsSL --retry 8 --retry-delay 5 --continue-at - $conf -o "$1.part" "$0" && mv "$1.part" "$1"'"'"'
# Only rows with a source are fetched and checked here; the rest (an older 3-column weights.tsv, HF
# cache metadata) come from the installers in step 4.
awk -F"\t" "NF>=6 && \$4 != \"-\"" /tmp/h3-weights.tsv > /tmp/h3-weights.fetchable && mv /tmp/h3-weights.fetchable /tmp/h3-weights.tsv
'"$verify_weights"
fi

restore_model_links

# The archive already contains ComfyUI. Start it before the overlay's
# --skip-base check; a clean reinstall would need 70 GB of transient space.
"${ssh_cmd[@]}" 'set -e; supervisorctl reread >/dev/null; supervisorctl update >/dev/null
for i in $(seq 1 90); do
  curl -fsS --max-time 3 http://127.0.0.1:18188/system_stats >/dev/null 2>&1 && exit 0
  sleep 2
done
echo "Restored ComfyUI did not become ready" >&2; exit 5'

step "4/4 Код и конфиги из master поверх образа (тот же установщик, идемпотентно)"
# The image is the heavy, rarely changing layer (OS packages, ComfyUI, nodes, venvs); the repo is the
# source of truth for code and configs. The regular installer runs over the restored tree: what is
# already there is skipped, only what master added since the snapshot is installed (code, configs,
# node packs, extra venvs/weights), and what master removed (e.g. CORS flag, gradio Studio) is undone.
# It then sets the Qwen mode, runs verify_h3_server.sh and switches the Studio tunnel.
bash scripts/h3_new_server.sh --host "$host" --port "$port" --identity "$identity" --skip-base ${pass[@]+"${pass[@]}"}
echo "ГОТОВО за $(( ($(date +%s) - started) / 60 )) мин. Рендер — только после «ИТОГ: всё OK» выше."
