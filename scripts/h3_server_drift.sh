#!/usr/bin/env bash
# Сверка живого GPU-сервера с репозиторием: всё, что правили прямо на сервере, должно быть здесь,
# иначе новый сервер из этого пакета это потеряет. НИЧЕГО не меняет ни на сервере, ни в репозитории.
#   bash scripts/h3_server_drift.sh --host root@HOST --port PORT [--identity ~/.ssh/key]
# Выход 0 — на сервере нет ничего, чего нет в репозитории. Выход 1 — есть (список выше).
# Обратное (в репозитории новее, чем на сервере) только сообщается: это ещё не выложенные правки.
set -uo pipefail
host=''; port=''; identity="$HOME/.ssh/id_ed25519"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;; --port) port="$2"; shift 2 ;; --identity) identity="$2"; shift 2 ;;
    *) echo "Usage: $0 --host root@HOST --port PORT [--identity PATH]"; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { echo "Usage: $0 --host root@HOST --port PORT"; exit 2; }
cd "$(dirname "$0")/.."
live="$(mktemp -d -t h3-live.XXXXXX)"
trap 'rm -rf "$live"' EXIT

ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  'cd /workspace/h3 && tar -czf - --exclude=__pycache__ --exclude="*.pyc" --exclude="*.bak*" --exclude="*.pre-*" --exclude="*.orig*" --exclude="*.before-*" \
     api/app scripts ComfyUI/custom_nodes/h3-color-grade ComfyUI/custom_nodes/h3-character-refine \
     ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache/nodes.py \
     ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache/_studio_metrics.py \
     ComfyUI/custom_nodes/h3-3D-camera-guide/{camera,camera_prompt,nodes,renderer}.py \
     ComfyUI/custom_nodes/h3_native_helpers/__init__.py \
     /etc/supervisor/conf.d/h3-*.conf /opt/supervisor-scripts/h3-*.sh $(ls comfy.env 2>/dev/null) 2>/dev/null; true' 2>/dev/null \
  | tar -xzf - -C "$live" 2>/dev/null
[[ -d "$live/api/app" ]] || { echo "Сервер недоступен или нет /workspace/h3/api/app"; exit 2; }

server_only=0
note() { printf '  %-12s %s\n' "$1" "$2"; }

# Код: сервер → репозиторий. Строки, которые есть на сервере и отсутствуют в репозитории, — потеря при переезде.
compare() {  # $1 server file, $2 repo file
  local s="$1" r="$2" name="$2"
  if [[ ! -f "$r" ]]; then note "ТОЛЬКО-СЕРВЕР" "$name"; server_only=1; return; fi
  cmp -s "$s" "$r" && return
  # Exact: the server file equals some committed version of this path -> only not yet deployed.
  # A changed constant (threshold=0.08 -> 0.12) used to pass the old similarity heuristic as "rewritten".
  local blob
  blob="$(git hash-object "$s")"
  if git log --all --format= --raw --no-abbrev -- "$r" | awk '{print $4}' | grep -qx "$blob"; then
    note "НЕ-ВЫЛОЖЕНО" "$name: на сервере старая версия из истории git"
    return
  fi
  local delta lost added code_lost
  # diff целиком в переменную: `diff | grep -q` с pipefail ловит SIGPIPE и молча скрывает потерю.
  delta="$(diff "$s" "$r")"
  lost="$(awk '/^</ {n++} END {print n+0}' <<<"$delta")"
  added="$(awk '/^>/ {n++} END {print n+0}' <<<"$delta")"
  # Потеря — строка кода сервера без близкого аналога в репозитории (переписанная строка — не потеря).
  # Комментарии и пустые строки не считаются.
  local lost_lines
  lost_lines="$(python3 - "$r" "$s" <<'PY'
import difflib, sys
repo = [l.strip() for l in open(sys.argv[1], errors="replace")]
have = set(repo)
for line in open(sys.argv[2], errors="replace"):
    line = line.strip()
    if len(line) < 12 or line.startswith("#") or line in have:
        continue
    if not difflib.get_close_matches(line, repo, n=1, cutoff=0.6):
        print(line[:110])
PY
)"
  code_lost="$(awk 'NF {n++} END {print n+0}' <<<"$lost_lines")"
  # Not in history: this exact file exists only on the server.
  note "ПОТЕРЯ" "$name: такой версии нет в истории git ($code_lost строк без аналога, +$added новых в репо)"
  if (( code_lost > 0 )); then sed 's/^/               | /' <<<"$lost_lines" | head -n 6
  else diff "$r" "$s" | grep '^>' | head -n 6 | sed 's/^/               | /'; fi
  server_only=1
}

echo "== Код GPU API (/workspace/h3/api/app ↔ app/)"
while IFS= read -r f; do compare "$live/api/app/$f" "app/$f"; done \
  < <(cd "$live/api/app" && find . -type f ! -name '*.pyc' | sed 's|^\./||' | sort)
while IFS= read -r f; do
  [[ -f "$live/api/app/$f" ]] || note "НЕ-ВЫЛОЖЕНО" "app/$f (нет на сервере)"
done < <(cd app && find . -type f -name '*.py' ! -path '*/__pycache__/*' ! -path './comfy_nodes/*' | sed 's|^\./||' | sort)

echo "== Узлы ComfyUI"
for pair in h3-color-grade:h3_color_grade h3-character-refine:h3_character_refine; do
  srv="${pair%%:*}"; rep="${pair##*:}"
  [[ -d "$live/ComfyUI/custom_nodes/$srv" ]] || continue
  for f in "$live/ComfyUI/custom_nodes/$srv"/*.py; do compare "$f" "app/comfy_nodes/$rep/$(basename "$f")"; done
done

cam="$live/ComfyUI/custom_nodes/h3-3D-camera-guide"
for f in camera camera_prompt nodes renderer; do
  [[ -f "$cam/$f.py" ]] && compare "$cam/$f.py" "app/camera_guide_vendor/$f.py"
done
[[ -f "$live/ComfyUI/custom_nodes/h3_native_helpers/__init__.py" ]] && \
  compare "$live/ComfyUI/custom_nodes/h3_native_helpers/__init__.py" "vendor/minimax-h3-local-studio/assets/h3_native_helpers.py"

echo "== Скрипты сервера (/workspace/h3/scripts ↔ scripts/)"
for f in "$live/scripts"/*; do [[ -f "$f" ]] && compare "$f" "scripts/$(basename "$f")"; done

echo "== Веса на сервере, которые не скачивает ни один установщик"
weights="$(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  'cd /workspace/h3 && find ComfyUI/models LivePortrait/pretrained_weights -type f -size +1M ! -name "*.part" -printf "%s %p\n"' 2>/dev/null)"
installers="$(cat scripts/*.sh scripts/*.py vendor/minimax-h3-local-studio/scripts/*.sh 2>/dev/null)"
while read -r size path; do
  [[ -n "$path" ]] || continue
  name="$(basename "$path")"
  case "$path" in LivePortrait/*) continue ;; esac  # install_h3_acting.sh берёт весь набор из манифеста HF
  grep -qF -- "$name" <<<"$installers" || { note "ТОЛЬКО-СЕРВЕР" "вес $path ($((size / 1048576)) МиБ): ни один установщик его не скачивает"; server_only=1; }
done <<<"$weights"

echo "== Закреплённые git-checkout'ы: коммит и локальные правки"
pin() { grep -hoE "^[[:space:]]*$1=['\"]?[0-9a-f]{40}" "${@:2}" | grep -oE '[0-9a-f]{40}' | head -n 1; }
# "dir=commit" pairs (macOS ships bash 3.2: no associative arrays).
pins="ComfyUI=$(pin comfy_commit vendor/minimax-h3-local-studio/scripts/deploy_h3_local_studio.sh)
ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache=$(pin cache_commit scripts/install_h3_h200_overlay.sh)
ComfyUI/custom_nodes/h3-3D-camera-guide=$(pin camera_commit scripts/install_h3_h200_overlay.sh)
vendor/sam2=$(pin sam2_commit scripts/install_h3_h200_overlay.sh)
ComfyUI/custom_nodes/ComfyUI-KJNodes=$(pin kjnodes_commit scripts/install_h3_h200_overlay.sh)
ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite=$(pin vhs_commit scripts/install_h3_h200_overlay.sh)
ComfyUI/custom_nodes/ComfyUI-Frame-Interpolation=$(pin vfi_commit scripts/install_h3_h200_overlay.sh)
LivePortrait=$(pin commit scripts/install_h3_acting.sh)"
dirs="$(cut -d= -f1 <<<"$pins" | tr '\n' ' ')"
# Files the installers patch on purpose (deploy_h3_local_studio.sh / install_h3_h200_overlay.sh).
expected_dirty='^(ComfyUI (server\.py|server\.py\.pre-minimax-comfy-user-[0-9]+)|ComfyUI/custom_nodes/h3-3D-camera-guide (camera|camera_prompt|nodes|renderer|__init__)\.py)$'
# Telemetry is an intentional source patch, accepted only when the whole audited patch and
# helper match the current installer. A filename allowlist would hide unrelated cache changes.
cache_metrics_matches=0
if python3 - "$live/ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache" <<'PY' 2>/dev/null
from pathlib import Path
import sys
from scripts.install_cache_metrics import patched
cache = Path(sys.argv[1])
source = (cache / 'nodes.py').read_text()
assert source == patched(source)
assert (cache / '_studio_metrics.py').read_bytes() == Path('app/comfy_nodes/h3_cache_metrics/runtime.py').read_bytes()
PY
then cache_metrics_matches=1; note "OK" "FirstBlockCache: точный патч метрик и runtime из репозитория"; fi
git_state="$(ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" "bash -s -- $dirs" 2>/dev/null <<'REMOTE'
cd /workspace/h3 || exit 0
for d in "$@"; do
  [ -d "$d/.git" ] || { echo "$d MISSING"; continue; }
  echo "$d HEAD $(git -C "$d" rev-parse HEAD)"
  # "<dir> DIRTY <XY> <path>", status spaces shown as '.'
  git -C "$d" status --porcelain | while IFS= read -r l; do
    xy="${l:0:2}"; echo "$d DIRTY ${xy// /.} ${l:3}"
  done
done
REMOTE
)"
while IFS='=' read -r d want; do
  [[ -n "$d" ]] || continue
  if grep -q "^$d MISSING" <<<"$git_state"; then note "НЕТ" "$d: checkout отсутствует"; continue; fi
  head="$(awk -v d="$d" '$1==d && $2=="HEAD" {print $3}' <<<"$git_state")"
  if [[ -n "$want" && "$head" != "$want" ]]; then note "ПОТЕРЯ" "$d: HEAD ${head:0:12} ≠ закреплённый ${want:0:12}"; server_only=1
  else note "OK" "$d @ ${head:0:12}"; fi
done <<<"$pins"
while read -r d _ xy file; do
  [[ -n "$file" ]] || continue
  if [[ "$cache_metrics_matches" == 1 && "$d" == ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache \
      && ( "$file" == nodes.py || "$file" == _studio_metrics.py ) ]]; then
    continue
  fi
  # Restore/build artifacts deliberately produced on the target GPU. XPose's
  # extension is architecture-specific; the omitted weights placeholder is not source.
  if [[ "$d" == LivePortrait && ( \
      "$file" == "pretrained_weights/.gitkeep" || \
      "$file" == ".h3-acting-smoke-ok" || \
      "$file" == "src/utils/dependencies/XPose/models/UniPose/ops/.h3-xpose-arch" || \
      "$file" == src/utils/dependencies/XPose/models/UniPose/ops/MultiScaleDeformableAttention*.so ) ]]; then
    continue
  fi
  grep -Eq "$expected_dirty" <<<"$d $file" && continue
  # Untracked directories the installers create inside the ComfyUI checkout (nodes, user data).
  [[ "$xy" == "??" && "$d" == ComfyUI && "$file" =~ ^(custom_nodes/[^/]+/|user/|models/|input/|output/)$ ]] && continue
  note "ПОТЕРЯ" "$d/$file: локальная правка в закреплённом checkout"; server_only=1
done < <(awk '$2=="DIRTY"' <<<"$git_state")

echo "== Скрипты supervisor (/opt/supervisor-scripts)"
# Launch lines from the installer template, with its variables resolved to the server layout.
expected_execs="$(sed -nE 's/^[[:space:]]*(exec .*)/\1/p' vendor/minimax-h3-local-studio/scripts/deploy_h3_local_studio.sh \
  | sed -e 's|\$h3_root|/workspace/h3|g' -e 's|\\\$|$|g')"
for f in "$live"/opt/supervisor-scripts/h3-*.sh; do
  [[ -f "$f" ]] || continue
  b="$(basename "$f")"
  line="$(sed -nE 's/^[[:space:]]*(exec .*)/\1/p' "$f" | head -n 1)"
  if grep -qxF -- "$line" <<<"$expected_execs"; then note "OK" "$b"
  else note "ОТЛИЧИЕ" "$b: команда запуска не совпадает с шаблоном установщика"; sed 's/^/               | /' <<<"$line"; server_only=1; fi
done

echo "== Конфиги supervisor против эталона"
# Comments never count; the VRAM guard's autostart is switched by h3_new_server.sh --qwen.
conf_body() { grep -v '^;' "$1" | { [[ "$2" == h3-qwen-vram-guard.conf ]] && grep -v '^autostart=' || cat; }; }
for f in "$live/etc/supervisor/conf.d"/h3-*.conf; do
  b="$(basename "$f")"
  [[ -f "deploy/$b" ]] || continue
  if diff -q <(conf_body "deploy/$b" "$b") <(conf_body "$f" "$b") >/dev/null; then note "OK" "$b"; continue; fi
  # A non-default scenarist profile: compare with what h3_scenarist_switch.sh writes for it.
  sp="$(sed -n 's/.*H3_SCENARIST_PROFILE="\([^"]*\)".*/\1/p' "$f" | head -n1)"
  if [[ "$b" == h3-qwen-director.conf && -n "$sp" ]]; then
    su="$(sed -n 's/.*H3_QWEN_GPU_UTIL="\([^"]*\)".*/\1/p' "$f" | head -n1)"; sl="$(sed -n 's/.*H3_QWEN_SLEEP_LEVEL="\([^"]*\)".*/\1/p' "$f" | head -n1)"
    if diff -q <(bash scripts/h3_scenarist_conf.sh "$sp" /workspace/h3 "deploy/$b" "$su" "$sl" 2>/dev/null | grep -v '^;') \
               <(conf_body "$f" "$b") >/dev/null; then note "OK" "$b (профиль сценариста $sp, util $su, sleep $sl)"; continue; fi
  fi
  # The server runs a committed older deploy/$b: only not yet deployed, nothing to lose.
  historic=0
  for rev in $(git log --format=%H -- "deploy/$b"); do
    if diff -q <(git show "$rev:deploy/$b" 2>/dev/null | grep -v '^;' | { [[ "$b" == h3-qwen-vram-guard.conf ]] && grep -v '^autostart=' || cat; }) \
               <(conf_body "$f" "$b") >/dev/null; then historic=1; break; fi
  done
  if [[ $historic == 1 ]]; then note "НЕ-ВЫЛОЖЕНО" "$b: на сервере версия ${rev:0:8} из истории git"; continue; fi
  note "ОТЛИЧИЕ" "$b не совпадает с deploy/$b:"; diff <(conf_body "deploy/$b" "$b") <(conf_body "$f" "$b") | grep '^[<>]' | head -n 6 | sed 's/^/               | /'; server_only=1
done

echo "== Политика перезапуска h3-api / h3-comfyui (генерируются установщиками)"
# autorestart/startsecs/stopwaitsecs/... of the generated programs against the installer heredocs.
# A value some committed installer produced is "not yet deployed"; anything else exists only on the server.
for spec in h3-api:scripts/install_h3_h200_overlay.sh h3-comfyui:vendor/minimax-h3-local-studio/scripts/deploy_h3_local_studio.sh; do
  prog="${spec%%:*}"; src="${spec#*:}"; conf="$live/etc/supervisor/conf.d/$prog.conf"
  [[ -f "$conf" ]] || continue
  verdict="$(python3 - "$prog" "$src" "$conf" <<'PY'
import re, subprocess, sys
prog, src, conf = sys.argv[1:]
KEYS = ("autorestart", "exitcodes", "startsecs", "startretries", "stopsignal", "stopwaitsecs",
        "stopasgroup", "killasgroup")
def policy(text):
    return {k: v for k, v in re.findall(r"^(\w+)=(.*)$", text, re.M) if k in KEYS}
def block(text):
    match = re.search(r"^\[program:%s\]\n(.*?)^EOF$" % re.escape(prog), text, re.M | re.S)
    return policy(match.group(1)) if match else None
have = policy(open(conf).read())
want = block(open(src).read())
fmt = lambda p: " ".join(f"{k}={p[k]}" for k in KEYS if k in p) or "—"
if want == have:
    print("OK " + fmt(have)); sys.exit()
revs = subprocess.run(["git", "log", "--format=%H", "--", src], capture_output=True, text=True).stdout.split()
for rev in revs:
    old = subprocess.run(["git", "show", f"{rev}:{src}"], capture_output=True, text=True).stdout
    if block(old) == have:
        print(f"OLD {rev[:8]} сервер: {fmt(have)}; установщик: {fmt(want or {})}"); sys.exit()
print(f"DIFF сервер: {fmt(have)}; установщик: {fmt(want or {})}")
PY
)"
  case "$verdict" in
    OK*) note "OK" "$prog.conf ${verdict#OK }" ;;
    OLD*) note "НЕ-ВЫЛОЖЕНО" "$prog.conf: политика из ${verdict#OLD }" ;;
    *) note "ОТЛИЧИЕ" "$prog.conf: ${verdict#DIFF }"; server_only=1 ;;
  esac
done
if [[ -f "$live/etc/supervisor/conf.d/h3-api.conf" ]]; then
  # h3-api.conf is generated by install_h3_h200_overlay.sh; compare its environment keys/values.
  expected="$(grep -m1 '^environment=H3_JOB_REGISTRY_PATH' scripts/install_h3_h200_overlay.sh | sed 's/^environment=//; s/\$root/\/workspace\/h3/g' | tr ',' '\n')"
  actual="$(grep -m1 '^environment=' "$live/etc/supervisor/conf.d/h3-api.conf" | sed 's/^environment=//' | tr ',' '\n')"
  while IFS= read -r kv; do
    key="${kv%%=*}"; want="${kv#*=}"; have="$(grep -m1 "^$key=" <<<"$actual" | cut -d= -f2-)"
    [[ "$want" == *'$'* ]] && { note "h3-api.conf" "$key=$have (задаётся при установке)"; continue; }
    [[ "$have" == "$want" ]] || { note "ОТЛИЧИЕ" "h3-api.conf $key: сервер $have, установщик $want"; server_only=1; }
  done <<<"$expected"
  while IFS= read -r kv; do
    grep -q "^${kv%%=*}=" <<<"$expected" && continue
    # Scenarist profile keys: written by scripts/h3_scenarist_switch.sh, not by the overlay.
    [[ "${kv%%=*}" =~ ^(H3_SCENARIST_PROFILE|H3_QWEN_SLEEP_LEVEL|H3_QWEN_START_TIMEOUT)$ ]] && { note "ПРОФИЛЬ" "h3-api.conf $kv (h3_scenarist_switch.sh)"; continue; }
    # Retired key: the GPU API no longer reads it (ControlNet removed); reinstall drops it.
    [[ "${kv%%=*}" == H3_STRUCTURAL_CONTROL_ENABLED ]] && { note "УСТАРЕЛО" "h3-api.conf ${kv%%=*} (код не читает)"; continue; }
    note "ТОЛЬКО-СЕРВЕР" "h3-api.conf ${kv%%=*} (нет в установщике)"; server_only=1
  done <<<"$actual"
fi

echo "== Конфиги supervisor: переменные окружения"
for f in "$live/etc/supervisor/conf.d"/h3-*.conf; do
  b="$(basename "$f")"
  env_line="$(grep -h '^environment=' "$f" 2>/dev/null)"
  note "$b" "${env_line#environment=}"
  grep -h '^autostart=' "$f" | sed 's/^/                 /'
done
[[ -f "$live/comfy.env" ]] && note "comfy.env" "$(tr '\n' ' ' < "$live/comfy.env")" || note "comfy.env" "нет (ComfyUI без доп. флагов)"

echo
if [[ $server_only == 0 ]]; then
  echo "ИТОГ: всё, что работает на сервере, есть в репозитории — пакет переноса полный."
else
  echo "ИТОГ: на сервере есть правки, которых нет в репозитории (ПОТЕРЯ / ТОЛЬКО-СЕРВЕР). Перенесите их в репозиторий"
  echo "      до переезда, иначе новый сервер их не получит."
fi
exit $server_only
