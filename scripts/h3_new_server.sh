#!/usr/bin/env bash
# Новый GPU-сервер H3 одной командой: проверка пакета → (сверка со старым сервером) → установка →
# актёрская игра → режим Qwen → проверка → Studio на Mac сразу работает с новым сервером. Запуск с Mac из корня репозитория:
#
#   bash scripts/h3_new_server.sh --host root@HOST --port PORT [--from root@OLD:PORT] [--qwen sleep|guard]
#        [--keep-paused]
#
#   --from     старый рабочий сервер: перед установкой сверяет его с репозиторием (scripts/h3_server_drift.sh)
#              и останавливается, если там есть правки, которых нет в пакете. Без --from сверка пропускается.
#   --qwen     sleep: Qwen спит в памяти (~1,3 ГБ), просыпается за 0,3 с, модели ComfyUI
#              между заданиями не выгружаются. guard: сторож останавливает Qwen на время рендера
#              (+~1 мин 40 с к каждому заданию: холодный старт Qwen и перезагрузка весов ComfyUI).
#              По умолчанию disabled: Qwen Director 8B отключён по решению 2026-10-22.
#              H3 encoder Qwen3-VL-32B остаётся обязательным.
#   --identity SSH-ключ (по умолчанию ~/.ssh/id_ed25519)
#   --skip-base сервер уже с ComfyUI и весами (повторная установка поверх)
#   Qwen-Image-Edit и LTX не устанавливаются: кадры готовит Codex на Mac.
#   --check    перед установкой прогнать тесты пакета (scripts/check.sh) — для разработчиков.
#   --keep-paused не снимать паузу очереди Studio. По умолчанию после «ИТОГ: всё OK» очередь
#              запускается: Studio сразу создаёт на новом сервере.
#
# Генерацию не запускает. Очередь, история, референсы и настройки Studio на Mac не трогает.
set -euo pipefail
host=''; port=''; from=''; qwen='disabled'; identity="$HOME/.ssh/id_ed25519"; skip_base=(); run_check=0
resume_queue=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:?}"; shift 2 ;;
    --port) port="${2:?}"; shift 2 ;;
    --from) from="${2:?}"; shift 2 ;;
    --qwen) qwen="${2:?}"; shift 2 ;;
    --identity) identity="${2:?}"; shift 2 ;;
    --skip-base) skip_base=(--skip-base); shift ;;
    --no-ltx-edit|--ltx-edit|--keyframe-qwen) echo "$1 больше не поддерживается в этом установщике" >&2; exit 2 ;;
    --keep-paused) resume_queue=0; shift ;;
    --check) run_check=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) sed -n '2,22p' "$0" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && -n "$port" ]] || { sed -n '2,22p' "$0" >&2; exit 2; }
[[ "$qwen" == sleep || "$qwen" == guard || "$qwen" == disabled ]] || { echo "--qwen: sleep, guard или disabled" >&2; exit 2; }
cd "$(dirname "$0")/.."
step() { printf '\n=== %s\n' "$1"; }

# Документы и результаты аудита не входят в пакет сервера. Останавливаемся только если грязны
# каталоги, которые реально отправляются на GPU/Mac; заметка в docs/ не должна прерывать restore.
deploy_paths=(app local_studio preview/h3-director scripts infra vendor deploy tests)
if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && [[ -n "$(git status --porcelain -- "${deploy_paths[@]}")" ]]; then
  git status --short -- "${deploy_paths[@]}" >&2
  echo "Стоп: есть незакоммиченные правки в коде/скриптах развёртывания (см. выше); сначала проверь и зафиксируй их." >&2
  exit 2
fi
echo "Версия пакета: $(git log -1 --format='%h %ci %s' 2>/dev/null || echo 'архив без git')"

if [[ "$run_check" == 1 ]]; then
  step "1/6 Пакет: тесты и синтаксис"
  bash scripts/check.sh
else
  step "1/6 Тесты пакета пропущены (включаются флагом --check)"
fi

if [[ -n "$from" ]]; then
  step "2/6 Сверка старого сервера с пакетом ($from)"
  old_host="${from%:*}"; old_port="${from##*:}"
  bash scripts/h3_server_drift.sh --host "$old_host" --port "$old_port" --identity "$identity" || {
    echo "Стоп: на старом сервере есть правки, которых нет в пакете (см. ПОТЕРЯ выше)." >&2
    echo "Перенесите их в репозиторий и запустите снова — иначе новый сервер их не получит." >&2
    exit 3
  }
  # Эталон «один в один» снимается со старого сервера в момент переезда; шаг 6 сравнивает с ним новый.
  export H3_LOCK_DIR="$(mktemp -d -t h3-lock.XXXXXX)"
  bash scripts/h3_server_lock.sh --host "$old_host" --port "$old_port" --identity "$identity" --out "$H3_LOCK_DIR"
else
  step "2/6 Сверка со старым сервером пропущена (нет --from)"
fi

step "2b/6 Hugging Face на новом сервере (токен не обязателен: модели открытые)"
# Источник токена по порядку: $HF_TOKEN → связка ключей macOS (service h3-hf-token) → старый сервер (--from).
# Токен идёт только через stdin; в аргументы команд, логи и вывод он не попадает.
hf_line=''
if [[ -n "${HF_TOKEN:-}" ]]; then
  hf_line="$HF_TOKEN"
elif hf_line="$(security find-generic-password -s h3-hf-token -w 2>/dev/null)"; then
  :
elif [[ -n "$from" ]]; then
  hf_line="$(ssh -i "$identity" -p "${from##*:}" -o BatchMode=yes -o ConnectTimeout=10 "${from%:*}" \
    'grep -o "hf_[A-Za-z0-9]*" /root/.cache/h3-hf-curl.conf' 2>/dev/null || true)"
fi
if [[ "$hf_line" =~ ^hf_[A-Za-z0-9]+$ ]]; then
  printf '%s\n' "$hf_line" | ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 \
    -o StrictHostKeyChecking=accept-new "$host" 'umask 077; mkdir -p /root/.cache; read -r t;
    printf "header = \"Authorization: Bearer %s\"\n" "$t" > /root/.cache/h3-hf-curl.conf;
    printf "header=Authorization: Bearer %s\n" "$t" > /root/.cache/h3-hf-aria2.conf; echo "токен записан (0600)"'
else
  # Every model Studio installs is public on Hugging Face (MiniMax H3, LoRAs, LivePortrait, SAM2): no token needed.
  # The download configs still exist (0600) so the installers find them; they only set a user agent.
  ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$host" \
    'umask 077; mkdir -p /root/.cache;
    [ -s /root/.cache/h3-hf-curl.conf ] || printf "user-agent = \"h3-studio\"\n" > /root/.cache/h3-hf-curl.conf;
    [ -s /root/.cache/h3-hf-aria2.conf ] || printf "user-agent=h3-studio\n" > /root/.cache/h3-hf-aria2.conf;
    echo "Hugging Face без токена: открытые модели"'
fi
unset hf_line

step "3/6 Установка на $host:$port (ComfyUI, веса BF16, GPU API, Qwen, узлы H3) и перевод туннеля Mac"
H3_SKIP_QWEN_DIRECTOR="$([[ "$qwen" == disabled ]] && echo 1 || echo 0)" bash scripts/deploy_h3_new_server.sh --host "$host" --port "$port" --identity "$identity" --apply \
  ${skip_base[@]+"${skip_base[@]}"}

step "4/6 Актёрская игра: LivePortrait human + animals (веса ~1,6 ГБ, venv, XPose)"
scp -i "$identity" -P "$port" -o BatchMode=yes -q scripts/install_h3_acting.sh scripts/enable_acting_remote.py "$host:/workspace/h3/scripts/"
ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" '
  if [[ -x /usr/local/cuda-12.8/bin/nvcc ]]; then
    export CUDA_HOME=/usr/local/cuda-12.8
    export PATH="$CUDA_HOME/bin:$PATH"
  fi
  bash /workspace/h3/scripts/install_h3_acting.sh'

step "4b/6 Motion Director (непрерывные сцены): закреплённый v1.2.0 + патч + torchaudio"
ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" 'mkdir -p /workspace/h3/scripts/patches'
scp -i "$identity" -P "$port" -o BatchMode=yes -q scripts/install_h3_motion_director.sh scripts/h3_motion_director_probe.py "$host:/workspace/h3/scripts/"
scp -i "$identity" -P "$port" -o BatchMode=yes -q scripts/patches/motion-director-kwargs.patch "$host:/workspace/h3/scripts/patches/"
ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" 'set -e
bash /workspace/h3/scripts/install_h3_motion_director.sh --apply
supervisorctl restart h3-comfyui >/dev/null
for _ in $(seq 1 90); do curl -fsS --max-time 5 http://127.0.0.1:18188/object_info/MiniMaxH3MotionDirector 2>/dev/null | grep -q MiniMaxH3MotionDirector && break; sleep 5; done
bash /workspace/h3/scripts/install_h3_motion_director.sh --check >/dev/null && echo "Motion Director установлен и загружен"'

step "5/6 Режим Qwen: $qwen"
if [[ "$qwen" == disabled ]]; then
  ssh -i "$identity" -p "$port" -o BatchMode=yes "$host" 'bash -s' <<'DISABLE_QWEN'
set -euo pipefail
supervisorctl stop h3-qwen-director h3-qwen-vram-guard >/dev/null 2>&1 || true
sed -i 's/^autostart=true/autostart=false/' /etc/supervisor/conf.d/h3-qwen-director.conf
sed -i 's/H3_QWEN_AUTOMANAGE="1"/H3_QWEN_AUTOMANAGE="0"/' /etc/supervisor/conf.d/h3-api.conf
supervisorctl reread >/dev/null && supervisorctl update >/dev/null
supervisorctl restart h3-api >/dev/null
supervisorctl status h3-qwen-director || true
DISABLE_QWEN
else
ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" "QWEN_MODE=$qwen bash -s" <<'REMOTE'
set -euo pipefail
queue="$(curl -fsS --max-time 10 http://127.0.0.1:18188/queue)"
python3 -c 'import json,sys; q=json.loads(sys.argv[1]); sys.exit(1 if q["queue_running"] or q["queue_pending"] else 0)' "$queue" \
  || { echo "ComfyUI занят — режим Qwen не меняю" >&2; exit 1; }
api=/etc/supervisor/conf.d/h3-api.conf; guard=/etc/supervisor/conf.d/h3-qwen-vram-guard.conf
if [[ "$QWEN_MODE" == sleep ]]; then
  sed -i 's/H3_QWEN_AUTOMANAGE="1"/H3_QWEN_AUTOMANAGE="0"/' "$api"
  [[ ! -f "$guard" ]] || sed -i 's/^autostart=true/autostart=false/' "$guard"
  supervisorctl stop h3-qwen-vram-guard >/dev/null 2>&1 || true
else
  sed -i 's/H3_QWEN_AUTOMANAGE="0"/H3_QWEN_AUTOMANAGE="1"/' "$api"
  [[ ! -f "$guard" ]] || sed -i 's/^autostart=false/autostart=true/' "$guard"
fi
supervisorctl reread >/dev/null && supervisorctl update >/dev/null
supervisorctl restart h3-api >/dev/null
if [[ "$QWEN_MODE" == sleep ]]; then
  supervisorctl start h3-qwen-director >/dev/null 2>&1 || true
  for _ in $(seq 1 90); do
    curl -fsS --max-time 3 http://127.0.0.1:8000/is_sleeping >/dev/null 2>&1 && break; sleep 2
  done
  curl -fsS -X POST --max-time 30 'http://127.0.0.1:8000/sleep?level=1' >/dev/null
fi
for _ in $(seq 1 30); do curl -fsS --max-time 3 http://127.0.0.1:18190/api/health >/dev/null && break; sleep 2; done
grep -o 'H3_QWEN_AUTOMANAGE="[01]"' "$api"
supervisorctl status h3-api h3-qwen-director h3-qwen-vram-guard 2>/dev/null || true
REMOTE

fi

# Nodes and models the user added in Studio «Узлы и модели» (pinned commits, SHA-256 weights).
studio_data="${H3_MAC_DATA_DIR:-$HOME/Library/Application Support/H3 Studio}"
if [[ -f "$studio_data/comfy/manifest.json" ]]; then
  echo "== Свои узлы и модели из Studio"
  bash scripts/h3_comfy_manifest_apply.sh --host "$host" --port "$port" --identity "$identity" \
    --manifest "$studio_data/comfy/manifest.json" --apply
fi

step "6/6 Проверка сервера и Studio (ничего не рендерит)"
bash scripts/verify_h3_server.sh --host "$host" --port "$port" --identity "$identity"

if [[ "$resume_queue" == 1 ]]; then
  curl -fsS --max-time 10 -X PUT -H 'Content-Type: application/json' -d '{"paused":false}' \
    http://127.0.0.1:18187/api/queue >/dev/null && echo "Очередь Studio запущена" \
    || echo "Studio не ответила: сними паузу кнопкой «Возобновить очередь»" >&2
fi

echo
echo "ГОТОВО: Studio http://127.0.0.1:18187/ работает с $host:$port. Первый ролик — короткий (5 с)."
