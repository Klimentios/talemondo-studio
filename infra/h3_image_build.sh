#!/usr/bin/env bash
# Новый образ софта H3 БЕЗ GPU и БЕЗ весов, на локальном стенде: старый снимок + текущий коммит,
# поставленный обычными установщиками (H3_IMAGE_BUILD=1) → проверка → упаковка как у h3_snapshot_pull.sh.
#
#   bash infra/h3_image_build.sh [--snapshot DIR] [--out DIR] [--base auto|overlay|fresh] [--volume NAME] [--keep]
#
#   --snapshot  старый снимок (по умолчанию самый новый в ~/H3-Snapshots), из него software.tar.zst и weights.tsv
#   --out       куда положить новый снимок h3-<YYYYMMDD-HHMM>-<sha>/ (по умолчанию ~/H3-Snapshots;
#               можно внешний диск: архив пишется туда потоком, распакованное дерево — только в VM)
#   --base      fresh: распаковать старый архив в новый том (нужно ~60 ГБ в VM);
#               overlay: нижний слой — уже распакованный том h3root стенда (только чтение, не меняется),
#               сверенный с архивом (tar --diff: отличия перезаписаны из архива, лишнее скрыто) — ~15 ГБ;
#               auto (по умолчанию): fresh, если в VM хватает места, иначе overlay
#   --volume    продолжить сборку в существующем томе (готовые шаги пропускаются); --keep — не удалять том
#   Флаги необязательных весов передаются установщикам как есть:
#   H3_SEEDVR2_7B=1, H3_CAMERA_EFFECTS=1, H3_SCENARIST_27B=0.
#
# Colima-профиль h3 / docker context colima-h3. Не трогает том h3root (только чтение) и контейнер h3-local,
# не ходит по SSH, не переключает туннель Mac, не скачивает веса (только пакеты и git по закреплённым версиям).
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$PWD"
ctx=(--context colima-h3)
snapshot="$(ls -d "$HOME"/H3-Snapshots/h3-* 2>/dev/null | sort | tail -n 1)"
out="$HOME/H3-Snapshots"; base_mode=auto; volume=''; keep=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --snapshot) snapshot="$2"; shift 2 ;; --out) out="$2"; shift 2 ;; --base) base_mode="$2"; shift 2 ;;
    --volume) volume="$2"; shift 2 ;; --keep) keep=1; shift ;;
    *) sed -n '2,19p' "$0" >&2; exit 2 ;;
  esac
done
[[ -f "$snapshot/software.tar.zst" && -f "$snapshot/weights.tsv" && -f "$snapshot/SNAPSHOT.txt" ]] \
  || { echo "Нет снимка: $snapshot" >&2; exit 2; }
# shellcheck source=h3_snapshot_spec.sh
. infra/h3_snapshot_spec.sh
started=$(date +%s)
step() { printf '\n=== %s (%d с)\n' "$1" "$(( $(date +%s) - started ))"; }
dk() { docker "${ctx[@]}" "$@"; }

step "0/7 Проверки: коммит, Colima, место"
if [[ -n "$(git status --porcelain)" ]]; then
  git status --short >&2; echo "Стоп: незакоммиченные правки — в образ идёт только закоммиченное" >&2; exit 2
fi
sha="$(git rev-parse --short HEAD)"
colima status h3 >/dev/null 2>&1 || { echo "Colima h3 не запущена: bash infra/h3_local.sh up" >&2; exit 2; }
dk image inspect h3-local:base >/dev/null || dk build -q -t h3-local:base infra/local >/dev/null
vm_free="$(colima ssh -p h3 -- df -B1 --output=avail /var/lib/docker | tail -1 | tr -d ' ')"
old_unpacked=60000000000   # распакованный старый снимок (~41 ГБ) + новый слой (~15 ГБ) + запас
if [[ "$base_mode" == auto ]]; then
  if dk volume inspect h3root >/dev/null 2>&1 && ((vm_free < old_unpacked)); then base_mode=overlay; else base_mode=fresh; fi
fi
need=$([[ "$base_mode" == fresh ]] && echo "$old_unpacked" || echo 20000000000)
echo "  коммит $sha, база: $base_mode, в VM свободно $((vm_free / 1000000000)) ГБ, нужно ~$((need / 1000000000)) ГБ"
if [[ -z "$volume" ]] && ((vm_free < need)); then
  echo "Стоп: в VM не хватает $(((need - vm_free) / 1000000000 + 1)) ГБ (диск VM: colima stop h3 && colima start h3 --disk N)" >&2; exit 4
fi
mkdir -p "$out"
out_free=$(( $(df -k "$out" | tail -1 | awk '{print $4}') * 1024 ))
((out_free > 30000000000)) || { echo "Стоп: в $out меньше 30 ГБ свободно для архива" >&2; exit 4; }

stamp="$(date +%Y%m%d-%H%M)"
volume="${volume:-h3img-$stamp}"
box="h3img-$stamp"
dir="$out/h3-$stamp-$sha"
base_dir=$([[ "$base_mode" == fresh ]] && echo /img/base || echo /lower)
lower_mount=(); [[ "$base_mode" == fresh ]] || lower_mount=(-v h3root:/lower:ro)
work="$(mktemp -d -t h3img.XXXXXX)"
cleanup() {
  dk rm -f "$box-build" "$box-verify" "$box-pack" >/dev/null 2>&1 || true
  rm -rf "$work"
  if [[ "$keep" == 0 && "${done_ok:-0}" == 1 ]]; then dk volume rm "$volume" >/dev/null 2>&1 || true
  else echo "Том сохранён: $volume (продолжить: --volume $volume, удалить: docker --context colima-h3 volume rm $volume)"; fi
}
trap cleanup EXIT
dk volume create "$volume" >/dev/null
# Throwaway container: the image tree at server paths (infra/local/h3img_box.sh), network for pip/git.
start_box() {  # $1 mode, extra docker args...
  local mode="$1"; shift
  dk rm -f "$box-$mode" >/dev/null 2>&1 || true
  dk run -d --platform linux/amd64 --privileged --name "$box-$mode" "$@" \
    "${lower_mount[@]}" -v "$volume":/img -v "$repo/infra/local/h3img_box.sh":/h3img_box.sh:ro \
    h3-local:base bash /h3img_box.sh "$mode" "$base_dir" >/dev/null
  for _ in $(seq 1 30); do dk logs "$box-$mode" 2>&1 | grep -q "h3img $mode ready" && return; sleep 1; done
  dk logs "$box-$mode" >&2; return 1
}
in_box() { dk exec -i "$box-$1" bash -c "$2"; }
done_mark() { in_box "$1" "test -f /img/.done-$2"; }

step "1/7 Старый снимок $(basename "$snapshot") → нижний слой ($base_mode)"
start_box build
if done_mark build base; then
  echo "  уже готово в томе $volume"
elif [[ "$base_mode" == fresh ]]; then
  in_box build 'rm -rf /img/base && mkdir -p /img/base'
  zstd -dc -T0 "$snapshot/software.tar.zst" | in_box build 'tar -x -C /img/base --numeric-owner'
  in_box build 'touch /img/.base-stamp /img/.done-base'
else
  # The stand volume h3root was unpacked from this archive but has been used since (A/B fetches,
  # ComfyUI temp files, weight placeholders). Compare it with the archive member by member and make the
  # overlay view equal to the archive: differing members re-extracted into the upper layer, extra
  # files hidden (whiteouts). h3root itself is mounted read-only and never changes.
  in_box build 'touch /img/.base-stamp'
  echo "  сверка с архивом (tar --diff по всем файлам, ~10 мин)…"
  q='--numeric-owner --quoting-style=literal'
  zstd -dc -T0 "$snapshot/software.tar.zst" | in_box build "cd /h3img && tee >(tar -t $q -f - > /img/old-list.txt) \
    | tar -d $q -f - > /img/old-diff.txt 2>&1 || true; sleep 3"
  in_box build 'cd /h3img
    sed -n -e "s/^tar: \(.*\): Warning: Cannot [a-z]*: No such file or directory$/\1/p" \
           -e "/^tar: /d" -e "s/^\(.*\): [A-Z][a-z ]* differs\{0,1\}$/\1/p" -e "s/^\(.*\): Not linked to .*$/\1/p" /img/old-diff.txt \
      | sort -u > /img/old-fix.txt
    echo "  отличаются от архива: $(wc -l < /img/old-fix.txt) записей (из $(wc -l < /img/old-list.txt))"; head -5 /img/old-fix.txt | sed "s/^/    /"'
  # Extra files: in the old tree view, packable (same rules as H3_TAR_ARGS), absent from the archive.
  in_box build 'cd /h3img && python3 - <<'"'"'PY'"'"'
import os, shutil
members = {line.rstrip("\n").rstrip("/") for line in open("/img/old-list.txt", encoding="utf-8", errors="surrogateescape")}
skip = ("workspace/h3/ComfyUI/models", "workspace/h3/hf-cache", "workspace/h3/audio/ace-checkpoints",
        "workspace/h3/audio/CosyVoice/pretrained_models", "workspace/h3/LivePortrait/pretrained_weights",
        "workspace/h3/input", "workspace/h3/output", "workspace/h3/api-data", "workspace/h3/model-staging", ".uv/cache")
def skipped(path, name):
    return path in skip or path.startswith(tuple(p + "/" for p in skip)) or name == "__pycache__" or name.endswith(".part")
extra = []
for top in ("workspace/h3", ".uv"):
    for dirpath, dirs, files in os.walk(top):
        for name in sorted(dirs) + sorted(files):
            path = os.path.join(dirpath, name)
            if skipped(path, name) or path not in members:
                if not skipped(path, name):
                    extra.append(path)
                if name in dirs:
                    dirs.remove(name)
for top in ("etc/supervisor/conf.d", "opt/supervisor-scripts"):
    for name in os.listdir(top):
        if name.startswith("h3-") and os.path.join(top, name) not in members:
            extra.append(os.path.join(top, name))
for path in extra:
    shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
print(f"  лишнее (нет в архиве) скрыто: {len(extra)}")
for path in extra[:8]:
    print("    -", path)
PY
    { cat /img/old-fix.txt; grep "/$" /img/old-list.txt; } | sed "s#/\$##" | sort -u > /img/old-fix2.txt'
  # Differing members plus every directory entry (hiding extras touched directory mtimes).
  zstd -dc -T0 "$snapshot/software.tar.zst" | in_box build "cd /h3img && tar -x $q --no-recursion --no-unquote --overwrite -f - -T /img/old-fix2.txt"
  zstd -dc -T0 "$snapshot/software.tar.zst" | in_box build "cd /h3img && tar -d $q -f - > /img/old-diff2.txt 2>&1" \
    && echo "  повторная сверка: вид нижнего слоя = архив $(basename "$snapshot")" \
    || { echo "Стоп: нижний слой всё ещё отличается от архива:" >&2; in_box build 'head -20 /img/old-diff2.txt' >&2; exit 5; }
  in_box build 'touch /img/.done-base'
fi

step "2/7 Установщики сервера поверх (H3_IMAGE_BUILD=1): база ComfyUI → overlay → актёрская игра → режим Qwen"
if done_mark build install; then
  echo "  уже готово в томе $volume"
else
  # The two bundles exactly as they ship to a server: the base like deploy_remote_h3_local_studio.sh,
  # the overlay via scripts/h3_overlay_bundle.sh (shared with deploy_h3_new_server.sh).
  COPYFILE_DISABLE=1 tar --exclude='._*' --exclude='*/._*' \
    --exclude='__pycache__' --exclude='*/__pycache__' --exclude='*.pyc' \
    -czf "$work/base.tar.gz" -C vendor/minimax-h3-local-studio .
  bash scripts/h3_overlay_bundle.sh "$work/overlay.tar.gz"
  in_box build 'mkdir -p /tmp/minimax-h3-local-studio-deploy && tar -xzf - -C /tmp/minimax-h3-local-studio-deploy && chmod +x /tmp/minimax-h3-local-studio-deploy/scripts/*.sh' < "$work/base.tar.gz"
  in_box build 'mkdir -p /tmp/h3-h200-overlay && tar -xzf - -C /tmp/h3-h200-overlay' < "$work/overlay.tar.gz"
  COPYFILE_DISABLE=1 tar -cf - scripts/install_h3_acting.sh scripts/enable_acting_remote.py | in_box build 'mkdir -p /tmp/h3-acting && tar -xf - -C /tmp/h3-acting'
  envs="H3_IMAGE_BUILD=1 H3_IMAGE_WEIGHTS=/img/expected-weights.tsv PIP_NO_CACHE_DIR=1 UV_NO_CACHE=1 TMPDIR=/img/tmp"
  for flag in H3_SEEDVR2_7B H3_CAMERA_EFFECTS H3_SCENARIST_27B; do
    [[ -z "${!flag:-}" ]] || envs+=" $flag=${!flag}"
  done
  in_box build "rm -f /img/expected-weights.tsv /img/.installers-ok; rm -rf /img/tmp; mkdir -p /img/tmp
    # The archive never contains ComfyUI/models (weights dir): restore the files git tracks there
    # (put_*_here, configs) as a server does after its weights are in place, so the base installer's
    # clean-checkout check sees the pinned ComfyUI.
    git -c safe.directory='*' -C /workspace/h3/ComfyUI checkout -- models
    set -euo pipefail
    export $envs
    echo '--- deploy_h3_local_studio.sh --apply --with-models --parallel-models --ref-only'
    /tmp/minimax-h3-local-studio-deploy/scripts/deploy_h3_local_studio.sh --apply --with-models --parallel-models --ref-only
    echo '--- install_h3_h200_overlay.sh --apply'
    bash /tmp/h3-h200-overlay/scripts/install_h3_h200_overlay.sh --apply
    echo '--- install_h3_acting.sh'
    install -m 0755 /tmp/h3-acting/scripts/install_h3_acting.sh /tmp/h3-acting/scripts/enable_acting_remote.py /workspace/h3/scripts/
    bash /workspace/h3/scripts/install_h3_acting.sh
    echo '--- режим Qwen: sleep (как h3_new_server.sh --qwen sleep, без supervisorctl)'
    sed -i 's/H3_QWEN_AUTOMANAGE=\"1\"/H3_QWEN_AUTOMANAGE=\"0\"/' /etc/supervisor/conf.d/h3-api.conf
    sed -i 's/^autostart=true/autostart=false/' /etc/supervisor/conf.d/h3-qwen-vram-guard.conf
    rm -rf /img/tmp; touch /img/.installers-ok" 2>&1 | tee "$work/install.log" | grep -vE '^(Requirement already satisfied|  Using cached|Collecting|Downloading|   ━)' || true
  in_box build 'test -f /img/.installers-ok && test -s /img/expected-weights.tsv' \
    || { echo "Стоп: установщики не дошли до конца (лог выше)" >&2; exit 5; }
  # Files the running stand added to h3root while we built (ComfyUI temp etc.) must not leak in.
  if [[ "$base_mode" == overlay ]]; then
    in_box build 'cd /lower && find workspace/h3 -newer /img/.base-stamp -type f ! -path "*/__pycache__/*" \
      ! -path "workspace/h3/ComfyUI/models/*" ! -path "workspace/h3/input/*" ! -path "workspace/h3/output/*" ! -path "workspace/h3/api-data/*" \
      | while IFS= read -r f; do [ -e "/img/build/$f" ] || rm -f "/h3img/$f"; done'
  fi
  in_box build 'touch /img/.done-install'
fi
dk cp "$box-build":/img/expected-weights.tsv "$work/expected-weights.tsv"
dk rm -f "$box-build" >/dev/null

step "3/7 Опись весов (что сервер должен скачать)"
python3 infra/h3_image_weights.py --old "$snapshot/weights.tsv" --recorded "$work/expected-weights.tsv" --out "$work/weights.tsv"

step "4/7 Проверка собранного дерева (ComfyUI --cpu, GPU API, pip check, app/, удалённое)"
start_box verify
# Sparse placeholders with the exact sizes (no disk used): ComfyUI lists the models, the lock sees sizes.
cut -f1,2 "$work/weights.tsv" | in_box verify 'cd /workspace/h3 && while IFS=$(printf "\t") read -r p s; do
    case "$p" in ComfyUI/custom_nodes/*) continue ;; esac
    mkdir -p "$(dirname "$p")"; [ -s "$p" ] || truncate -s "$s" "$p"; done; mkdir -p input output api-data'
COPYFILE_DISABLE=1 tar -cf - app tests/golden scripts/h3_graph_validate.py | in_box verify 'mkdir -p /tmp/repo && tar -xf - -C /tmp/repo'
in_box verify 'printf "H3_COMFY_EXTRA_ARGS=\"--cpu\"\n" > /workspace/h3/comfy.env
  cat > /tmp/sv.conf <<EOF
[unix_http_server]
file=/tmp/sv.sock
[supervisord]
logfile=/tmp/supervisord.log
pidfile=/tmp/supervisord.pid
[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix:///tmp/sv.sock
[include]
files = /etc/supervisor/conf.d/h3-comfyui.conf /etc/supervisor/conf.d/h3-api.conf
EOF
  setsid nohup supervisord -n -c /tmp/sv.conf > /tmp/sv.out 2>&1 < /dev/null &
  sleep 3'
in_box verify 'for i in $(seq 1 120); do curl -fs -o /dev/null http://127.0.0.1:18188/system_stats && curl -fs -o /dev/null http://127.0.0.1:18189/api/live && exit 0; sleep 5; done
  supervisorctl -c /tmp/sv.conf status; tail -40 /tmp/sv.out; exit 1' \
  || { echo "Стоп: ComfyUI/GPU API из собранного дерева не поднялись" >&2; exit 6; }
fail=0
check() { if in_box verify "$2" > "$work/check.txt" 2>&1; then echo "  OK   $1"; sed 's/^/       /' "$work/check.txt" | head -n "${3:-0}"
          else echo "  FAIL $1"; sed 's/^/       /' "$work/check.txt" | head -20; fail=1; fi; }
check "supervisor: h3-comfyui и h3-api из собранных конфигов RUNNING" 'supervisorctl -c /tmp/sv.conf status | grep -c RUNNING | grep -qx 2 && supervisorctl -c /tmp/sv.conf status' 2
check "узлы ComfyUI загрузились (нет IMPORT FAILED)" 'log=$(cat /tmp/sv.out)
  ! grep -E "IMPORT FAILED|Cannot import" <<<"$log" || exit 1
  for n in h3_native_helpers ComfyUI-MiniMaxH3-FirstBlockCache h3-3D-camera-guide h3-color-grade h3-character-refine \
           ComfyUI-KJNodes ComfyUI-VideoHelperSuite ComfyUI-Frame-Interpolation websocket_image_save; do
    grep -q "custom_nodes/$n" <<<"$log" || { echo "нет $n"; exit 1; }; done
  grep -A15 "Import times for custom nodes" <<<"$log" | grep seconds | sed "s/.*custom_nodes\///"' 12
check "обязательные узлы overlay в /object_info" 'python3 - <<"PY"
import json, re, urllib.request
nodes = json.load(urllib.request.urlopen("http://127.0.0.1:18188/object_info", timeout=120))
required = {"MiniMaxH3ReferenceToVideo", "LoraLoaderModelOnly", "ApplyMiniMaxH3FirstBlockCache",
    "H3CameraGuide", "H3OperatorCamera", "H3CameraPrompt", "MiniMaxH3AddGuide", "H3ColorGrade", "H3FilmLook",
    "H3CharacterTrack", "H3CharacterCrop", "H3CharacterComposite", "ImageFromBatch", "TrimAudioDuration", "LoadVideo",
    "GetVideoComponents", "ColorMatch", "VHS_LoadVideo", "VHS_VideoCombine", "VHS_SelectImages", "RIFE VFI",
    "TextEncodeQwenImageEditPlus", "FluxKontextMultiReferenceLatentMethod", "ModelSamplingAuraFlow", "CFGNorm",
    "TextEncodeQwenImage21", "QwenImage21Cache", "ImageScaleToTotalPixels", "SaveImage"}
missing = sorted(required - nodes.keys())
print(f"{len(nodes)} классов, обязательных {len(required)}, нет: {missing}")
raise SystemExit(1 if missing else 0)
PY' 1
check "графы tests/golden против /object_info" 'cd /tmp/repo && python3 scripts/h3_graph_validate.py --object-info http://127.0.0.1:18188/object_info tests/golden/*_graph.json tests/golden/*_gpu_*.json' 10
check "GPU API: /api/live, /api/capabilities (continuation_v1, scenarist_v1)" 'curl -fsS http://127.0.0.1:18189/api/live >/dev/null
  curl -fsS http://127.0.0.1:18189/api/capabilities | python3 -c "import json,sys; c=json.load(sys.stdin); keys=[\"continuation_v1\",\"scenarist_v1\"]; print({k: c.get(k) for k in keys}); sys.exit(0 if all(k in c for k in keys) else 1)"' 1
# Audio venvs come from scripts/install_h3_audio_*.sh, which the image build does not run: reported, not gating.
check "pip check в venv установщиков (venv, api, qwen, qwen-next, liveportrait)" 'cd /workspace/h3; rc=0; for v in venv api-venv qwen-venv qwen-next-venv liveportrait-venv; do
    [ -x "$v/bin/python" ] || { echo "$v: нет"; continue; }
    # uv-made venvs have no pip: the api-venv pip checks them (pip --python)
    if "$v/bin/python" -m pip --version >/dev/null 2>&1; then r=$("$v/bin/python" -m pip check 2>&1) || rc=1
    else r=$(api-venv/bin/python -m pip --python "$v/bin/python" check 2>&1) || rc=1; fi
    echo "$v: $(tail -1 <<<"$r")"; done; exit $rc' 8
in_box verify 'cd /workspace/h3; for v in audio/cosy-venv audio/ACE-Step-1.5/.venv; do [ -x "$v/bin/python" ] || continue
    r=$("$v/bin/python" -m pip check 2>&1 || api-venv/bin/python -m pip --python "$v/bin/python" check 2>&1); echo "  INFO pip check $v (из старого снимка, сборка не трогает): $(tail -1 <<<"$r")"; done' || true
check "/workspace/h3/api/app = app/ репозитория (как h3_server_drift.sh)" 'diff -r -x __pycache__ -x "*.pyc" /workspace/h3/api/app /tmp/repo/app && echo "идентично ($(find /tmp/repo/app -type f ! -path "*/__pycache__/*" | wc -l) файлов)"' 1
check "удалённое удалено, режимы как на сервере" 'cd /workspace/h3
  ! grep -rn -- "--enable-cors-header" /opt/supervisor-scripts/h3-comfyui.sh /etc/supervisor/conf.d/ || exit 1
  ! ls /etc/supervisor/conf.d/h3-git-studio.conf /opt/supervisor-scripts/h3-git-studio.sh 2>/dev/null || exit 1
  grep -q "^autostart=false" /etc/supervisor/conf.d/h3-qwen-vram-guard.conf
  grep -q "H3_QWEN_AUTOMANAGE=\"0\"" /etc/supervisor/conf.d/h3-api.conf
  grep -qx "include-system-site-packages = false" venv/pyvenv.cfg
  ! ls ComfyUI/user/default/workflows/*.app.json 2>/dev/null || exit 1
  [ -x qwen-next-venv/bin/vllm ] && [ -f LivePortrait/.h3-acting-smoke-ok ]
  ls /etc/supervisor/conf.d/ /opt/supervisor-scripts/ | tr "\n" " "; echo' 1
check "ComfyUI checkout: закреплённый коммит, чужих правок нет" 'cd /workspace/h3/ComfyUI && git -c safe.directory="*" rev-parse HEAD && git -c safe.directory="*" status --short | grep -v "^ M server.py$" | grep -v " models/" | (! grep .)' 1
((fail == 0)) || { echo "Стоп: проверка собранного дерева не прошла" >&2; exit 6; }

step "5/7 Эталон lock/ (логика scripts/h3_server_lock.sh, против контейнера)"
mkdir -p "$work/lock"
bash scripts/h3_server_lock.sh --docker "$box-verify" --context colima-h3 --out "$work/lock" | sed 's/^/ /'
in_box verify 'supervisorctl -c /tmp/sv.conf shutdown >/dev/null 2>&1 || true'
dk rm -f "$box-verify" >/dev/null
dk run --rm --platform linux/amd64 -v "$volume":/img h3-local:base sh -c 'rm -rf /img/verify /img/work-verify'

step "6/7 Упаковка (тот же состав, что h3_snapshot_pull.sh) → $dir"
mkdir -p "$dir"
start_box pack
leaks="$(in_box pack "$(h3_leak_scan_cmd)")"
[[ -z "$leaks" ]] || { echo "Стоп: в софте найдены токены или ключи:" >&2; echo "$leaks" >&2; exit 3; }
echo "  токенов и ключей нет"
args="$(tr '\n' ' ' <<<"$H3_TAR_ARGS")"
# Stream straight to the output disk (no copy on the internal disk); renamed only when complete.
in_box pack "cd /h3img && tar --create $args workspace/h3 .uv \$(ls -d etc/supervisor/conf.d/h3-*.conf opt/supervisor-scripts/h3-*.sh) 2>/dev/null" \
  | zstd -T0 -3 -q -o "$dir/software.tar.zst.part"
in_box pack 'ls -d /etc/supervisor/conf.d/h3-*.conf /opt/supervisor-scripts/h3-*.sh' > "$work/packed-sv.txt"
dk rm -f "$box-pack" >/dev/null
zstd -t -q "$dir/software.tar.zst.part"
mv "$dir/software.tar.zst.part" "$dir/software.tar.zst"
echo "$(shasum -a 256 "$dir/software.tar.zst" | cut -d' ' -f1)  software.tar.zst" > "$dir/software.sha256"
(cd "$dir" && shasum -a 256 -c --quiet software.sha256) && echo "  архив цел: zstd -t OK, SHA-256 $(cut -c1-16 "$dir/software.sha256")…, $(du -h "$dir/software.tar.zst" | cut -f1)"
cp "$work/weights.tsv" "$dir/weights.tsv"
mkdir -p "$dir/lock" && cp "$work"/lock/* "$dir/lock/"

step "7/7 Паспорт SNAPSHOT.txt"
{
  echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "build: built offline on stand, no GPU (infra/h3_image_build.sh, H3_IMAGE_BUILD=1)"
  echo "server: none"
  echo "repo_commit: $(git rev-parse HEAD)"
  echo "base_snapshot: $(basename "$snapshot") (repo_commit $(sed -n 's/^repo_commit: //p' "$snapshot/SNAPSHOT.txt"))"
  echo "base_layer: $base_mode"
  echo "stand_image: h3-local:base $(dk image inspect -f '{{.Id}}' h3-local:base) (ubuntu:24.04 + infra/local/Dockerfile)"
  grep -E '^(base_image[a-z0-9_]*|os|cuda_toolkit|python):' "$snapshot/SNAPSHOT.txt"
  echo "weights: $(wc -l < "$dir/weights.tsv" | tr -d ' ') files, $(awk -F'\t' '{s+=$2} END {printf "%.1f", s/1e9}' "$dir/weights.tsv") GB (weights.tsv: path, size, sha256, source, revision, source_path)"
  echo "optional_weights: H3_SEEDVR2_7B=${H3_SEEDVR2_7B:-0} H3_CAMERA_EFFECTS=${H3_CAMERA_EFFECTS:-1} H3_SCENARIST_27B=${H3_SCENARIST_27B:-auto}"
  echo "gpu_unverified: torch.cuda / vLLM serve / XPose kernels / render quality — checked by verify_h3_server.sh on the GPU"
  echo "supervisor_programs:"
  sed 's/^/  /' "$work/packed-sv.txt"
} > "$dir/SNAPSHOT.txt"
done_ok=1
cat "$dir/SNAPSHOT.txt"
echo
echo "ГОТОВО за $(( ($(date +%s) - started) / 60 )) мин: $dir"
