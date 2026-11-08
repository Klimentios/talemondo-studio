#!/usr/bin/env bash
# Полная проверка связки STUDIO Studio (Mac) ↔ GPU-сервер: узлы, связи, конфиги, модули.
# НИЧЕГО не рендерит и не меняет. Запуск с Mac из корня проекта:
#   bash scripts/verify_h3_server.sh --host root@HOST --port PORT [--identity ~/.ssh/key]
# Итог: таблица OK/FAIL и код выхода 0 только если всё OK.
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
# Stop before comparing weights, nodes and packages when the server is absent.
# Otherwise empty SSH responses are reported as hundreds of misleading missing files.
if ! ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 -o ConnectionAttempts=1 \
  "$host" 'test -d /workspace/h3/api/app' >/dev/null 2>&1; then
  echo "ИТОГ: GPU-сервер недоступен или /workspace/h3/api/app отсутствует; узлы, веса и конфигурация не проверены."
  exit 2
fi
fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fail=1; }
local_schema_sha="$(shasum -a 256 app/director_schema.json 2>/dev/null || sha256sum app/director_schema.json)"; local_schema_sha="${local_schema_sha%% *}"
local_graph_sha="$(shasum -a 256 app/graph.py 2>/dev/null || sha256sum app/graph.py)"; local_graph_sha="${local_graph_sha%% *}"
local_metrics_sha="$(shasum -a 256 app/comfy_nodes/h3_cache_metrics/runtime.py 2>/dev/null || sha256sum app/comfy_nodes/h3_cache_metrics/runtime.py)"; local_metrics_sha="${local_metrics_sha%% *}"
# Весь код GPU API одним отпечатком: правка «только на сервере» или невыложенная правка видна сразу.
app_digest() { find app -name '*.py' ! -path '*/__pycache__/*' ! -path 'app/comfy_nodes/*' | LC_ALL=C sort \
  | while read -r f; do printf '%s  %s\n' "$( (shasum -a 256 "$f" 2>/dev/null || sha256sum "$f") | cut -d' ' -f1)" "$f"; done; }
local_app_sha="$(app_digest | (shasum -a 256 2>/dev/null || sha256sum) | cut -d' ' -f1)"
# app_digest skips app/comfy_nodes (they live under ComfyUI/custom_nodes): 06.11 a deploy from an old bundle
# left out the probe nodes and the prewarm service and every check still said OK. Every node class our packages
# declare must be loaded by the live ComfyUI.
local_nodes="$(python3 - <<'PY'
import ast, pathlib
names = set()
for p in pathlib.Path("app/comfy_nodes").rglob("*.py"):
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
                and any(getattr(t, "id", "") == "NODE_CLASS_MAPPINGS" for t in n.targets)):
            names |= {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
print(",".join(sorted(names)))
PY
)"

echo "== 1. GPU-сервер ($host:$port)"
ssh -i "$identity" -p "$port" -o BatchMode=yes -o ConnectTimeout=10 "$host" \
  "LOCAL_SCHEMA_SHA=$local_schema_sha LOCAL_GRAPH_SHA=$local_graph_sha LOCAL_APP_SHA=$local_app_sha LOCAL_METRICS_SHA=$local_metrics_sha LOCAL_NODES=$local_nodes bash -s" <<'REMOTE' || fail=1
set -uo pipefail
root=/workspace/h3; fail=0
ok()  { printf '  OK    %s\n' "$1"; }
bad() { printf '  FAIL  %s\n' "$1"; fail=1; }
gpu="$(nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader 2>/dev/null)" && ok "GPU: $gpu" || bad "nvidia-smi недоступен"
df -h "$root" | awk 'NR==2 {print "  INFO  диск: свободно " $4 " из " $2}'
for svc in h3-comfyui h3-api h3-server-queue; do
  st="$(supervisorctl status "$svc" 2>/dev/null | awk '{print $2}')"
  [[ "$st" == RUNNING ]] && ok "supervisor $svc RUNNING" || bad "supervisor $svc: ${st:-нет}"
done
queue_caps="$(curl -fsS --max-time 10 http://127.0.0.1:18191/api/server-queue/capabilities)" || queue_caps='{}'
echo "$queue_caps" | grep -q '"server_queue_v1": *true' && ok "постоянная очередь сервера готова" \
  || bad "серверная очередь недоступна на 18191"
st="$(supervisorctl status h3-qwen-director 2>/dev/null | awk '{print $2}')"; echo "  INFO  supervisor h3-qwen-director: ${st:-нет} (спит между заданиями — это нормально)"
command -v ffmpeg >/dev/null && ok "ffmpeg" || bad "ffmpeg не установлен"
models="$root/ComfyUI/models"
for f in diffusion_models/minimax_h3_ref2va_bf16.safetensors text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors \
         vae/minimax_h3_video_vae_fp16.safetensors vae/minimax_h3_audio_vae_fp32.safetensors \
         vae/MiniMax-H3-X2-Detail-v1.safetensors \
         loras/3dsrx_minimax_h3_rank16-step00001250.safetensors \
         loras/minimax-h3-rough-2d-cartoon-illustration-1200.safetensors \
         loras/wushu_action_h3_lora_v5_3000_pruned.safetensors \
         loras/wushu_spatial_physics_clean_3000_pruned.safetensors \
         loras/camera_motion_h3_lora_v1_3000_pruned.safetensors \
         loras/h3-realism-people-t2v-i2v-r2v.safetensors \
         loras/minimax-h3-facial-realism-closeup-cp2000.safetensors; do
  p="$(ls "$models"/$f 2>/dev/null || find "$models" -name "$(basename "$f")" -size +1M 2>/dev/null | head -1)"
  [[ -n "$p" && -s "$p" ]] && ok "вес $(basename "$f") ($(du -hL "$p" | cut -f1))" || bad "нет веса $(basename "$f")"
done
node_repo="$root/ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-LatentUpscaler"
if [[ -d "$node_repo/.git" ]]; then
  node_commit="$(git -C "$node_repo" rev-parse HEAD 2>/dev/null)"
  [[ "$node_commit" == "2e568dfe3e4f5e81da178bc845e05dcfbb64d55b" ]] && \
    ok "X2 decoder node pinned commit $node_commit" || bad "X2 decoder node commit отличается: ${node_commit:-нет}"
else
  bad "нет X2 decoder node checkout"
fi
md_repo="$root/ComfyUI/custom_nodes/ComfyUI-MiniMax-H3-Motion-Director"
if [[ -d "$md_repo/.git" ]]; then
  [[ "$(git -C "$md_repo" rev-parse HEAD 2>/dev/null)" == "0e6b5913bbce44932a82fa9eed79a26c66c578d8" ]] \
    && ok "Motion Director v1.2.0 pinned" || bad "Motion Director: коммит отличается от v1.2.0"
  grep -q "ref_image_size=ref_image_size," "$md_repo/nodes/conditioning.py" \
    && ok "Motion Director: патч вызова по именам" || bad "Motion Director: нет patches/motion-director-kwargs.patch (Ref2VA упадёт: str // int)"
  "$root/venv/bin/python" -c 'import torchaudio' 2>/dev/null \
    && ok "Motion Director: torchaudio в venv ComfyUI" || bad "Motion Director: нет torchaudio (аудио-непрерывность упадёт)"
else
  bad "нет Motion Director — scripts/install_h3_motion_director.sh --apply"
fi
curl -fsS --max-time 5 http://127.0.0.1:18188/system_stats >/dev/null && ok "ComfyUI 18188 отвечает" || bad "ComfyUI 18188 не отвечает"
curl -fsS --max-time 60 http://127.0.0.1:18188/object_info -o /tmp/h3-object-info.json && ok "object_info получен" || bad "object_info недоступен"
grep -q '"MiniMaxH3MotionDirector"' /tmp/h3-object-info.json 2>/dev/null && ok "узел MiniMaxH3MotionDirector загружен" || bad "ComfyUI не загрузил MiniMaxH3MotionDirector"
for node in ${LOCAL_NODES//,/ }; do
  grep -q "\"$node\"" /tmp/h3-object-info.json 2>/dev/null || bad "ComfyUI не загрузил наш узел $node — overlay выложен из старого бандла (install_h3_h200_overlay.sh из HEAD)"
done
[[ -n "$LOCAL_NODES" ]] && ok "наши узлы app/comfy_nodes загружены (${LOCAL_NODES//,/, })" \
  || bad "список наших узлов пуст — на Mac не прочитан app/comfy_nodes (python3), проверка узлов не выполнена"
# Research probes (app/probe_graph.py) wrap attention in every job while this flag exists: 06.11 it broke sparse
# attention («another attention override is already active»). A production server runs without it.
[[ -f $root/api-data/probe_all.json ]] && bad "включены исследовательские зонды ($root/api-data/probe_all.json) — удали файл до рабочих заданий" || ok "исследовательские зонды выключены"
[[ -f /etc/supervisor/conf.d/h3-prewarm.conf && -x $root/scripts/h3_prewarm.sh ]] && ok "прогрев весов h3-prewarm установлен" \
  || bad "нет h3-prewarm (deploy/h3-prewarm.conf + scripts/h3_prewarm.sh) — overlay выложен из старого бандла"
# Source and live route both matter: copying telemetry without restarting ComfyUI is insufficient.
metrics_dir="$root/ComfyUI/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache"
[[ "$(sha256sum "$metrics_dir/_studio_metrics.py" 2>/dev/null | cut -d' ' -f1)" == "$LOCAL_METRICS_SHA" ]] \
  && ok "метрики кэша: runtime совпадает с Mac" || bad "метрики кэша: runtime отсутствует или устарел"
grep -q '_record_cache_metrics(cache, preset, started, completed)' "$metrics_dir/nodes.py" 2>/dev/null \
  && ok "метрики кэша: запись перед reset установлена" || bad "метрики кэша: нода не инструментирована"
# An invalid UUID distinguishes the installed handler (400) from an absent route (404).
metrics_status="$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:18188/h3/cache-metrics/not-a-uuid)"
[[ "$metrics_status" == 400 ]] && ok "метрики кэша: маршрут ComfyUI активен" \
  || bad "метрики кэша: маршрут не активен (HTTP $metrics_status)"
# The same extension contract used by the CPU stand; files alone do not prove
# the current ComfyUI process loaded graph exchange and provenance handlers.
cd "$root/api" && "$root/api-venv/bin/python" - <<'PY' || fail=1
from pathlib import Path
import urllib.request
import urllib.error
from app.studio_extensions import extension_problems, runtime_problems
def status(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18188' + path, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
problems = extension_problems(Path.cwd(), Path('/workspace/h3/ComfyUI')) + runtime_problems(status)
for problem in problems:
    print('  FAIL  Studio/ComfyUI:', problem)
if not problems:
    print('  OK    Studio/ComfyUI: файлы интеграции совпадают, маршруты загружены')
raise SystemExit(int(bool(problems)))
PY
[[ ! -f /etc/supervisor/conf.d/h3-git-studio.conf ]] && ok "устаревшая gradio Studio удалена" \
  || bad "остался конфиг устаревшей h3-git-studio"
grep -qF 'H3_CONTEXT_REGISTRY_PATH="'"$root"'/api-data/context-ir.sqlite3"' /etc/supervisor/conf.d/h3-api.conf \
  && ok "Context-IR: реестр операций в api-data" || bad "Context-IR: путь реестра не закреплён в конфиге API"
health="$(curl -fsS --max-time 10 http://127.0.0.1:18189/api/health)" && ok "GPU API 18189: $health" || bad "GPU API 18189 не отвечает"
caps="$(curl -fsS --max-time 10 http://127.0.0.1:18189/api/capabilities)" || caps='{}'
for cap in color_grade_v1 film_look_v1 continuation_v1; do
  echo "$caps" | grep -q "\"$cap\": *true" && ok "возможность API $cap" || bad "API не объявляет $cap — app/ на сервере старый"
done
python3 - "$caps" <<'PY' || fail=1
import json
import sys
caps = json.loads(sys.argv[1])
available = set(caps.get("style_adapters", []))
required = {"style3d", "cartoon2d", "combat", "realism_people"}
optional = {"character_swap", "character_swap_v15"}  # swap A/B v1.5 (store.SWAP_LORAS)
if required <= available and available <= required | optional:
    extra = " + экспериментальный character_swap" if "character_swap" in available else ""
    print(f"  OK    все четыре штатных LoRA adapter modes доступны{extra}")
else:
    missing = sorted(required - available)
    extra = sorted(available - required - optional)
    print(f"  FAIL  LoRA modes: доступны={sorted(available)}, отсутствуют={missing}, лишние={extra}")
    raise SystemExit(1)
PY
# Версионированный контракт Mac↔GPU: Mac Studio откажет серверу со старым контрактом или другой схемой.
echo "$caps" | grep -q "\"api_contract\": *[3-9]" && ok "контракт API объявлен" || bad "API не объявляет api_contract — app/ на сервере старый"
echo "$caps" | grep -q "\"director_schema_sha256\": *\"$LOCAL_SCHEMA_SHA\"" && ok "API считает ту же схему Director" \
  || bad "API объявляет другую схему Director — задеплой app/ и перезапусти API"
sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
[[ "$(sha $root/api/app/director_schema.json)" == "$LOCAL_SCHEMA_SHA" ]] && ok "схема Director совпадает с Mac" || bad "схема Director на сервере отличается от Mac — задеплой app/"
[[ "$(sha $root/api/app/graph.py)" == "$LOCAL_GRAPH_SHA" ]] && ok "graph.py совпадает с Mac" || bad "graph.py на сервере отличается от Mac — задеплой app/"
for f in __init__.py grade.py film.py; do
  [[ -f "$root/ComfyUI/custom_nodes/h3-color-grade/$f" ]] && ok "узел-пакет h3-color-grade/$f" || bad "нет custom_nodes/h3-color-grade/$f"
done
# Уроки 2026-10-03: каждая строка ниже — ошибка, которая на живом сервере не давала явного сбоя.
st="$(supervisorctl status h3-watchdog 2>/dev/null | awk '{print $2}')"
[[ "$st" == RUNNING ]] && ok "supervisor h3-watchdog RUNNING" || bad "h3-watchdog: ${st:-нет} — scripts/h3_runtime_watchdog.py + deploy/h3-watchdog.conf"
qconf=/etc/supervisor/conf.d/h3-qwen-director.conf
grep -q 'H3_QWEN_GPU_UTIL="0\.[1-4][0-9]*"' "$qconf" 2>/dev/null && ok "Qwen: ограничен бюджет VRAM ($(grep -o 'H3_QWEN_GPU_UTIL="[^"]*"' "$qconf"))" \
  || bad "Qwen без H3_QWEN_GPU_UTIL≈0.30: с vLLM-дефолтом 0.90 не стартует рядом с прогретым ComfyUI (цикл падений)"
if [[ -x "$root/qwen-venv/bin/python" ]]; then
  vllm_ver="$("$root/qwen-venv/bin/python" -c 'import vllm; print(vllm.__version__)' 2>/dev/null)"
  [[ "$vllm_ver" == 0.15.* ]] && ok "vLLM $vllm_ver (проверенная версия)" || bad "vLLM ${vllm_ver:-нет}: проверена только 0.15.x cu128"
  worker="$("$root/qwen-venv/bin/python" -c 'import vllm.v1.worker.gpu_worker as w; print(w.__file__)' 2>/dev/null)"
  if [[ -n "$worker" ]] && grep -q ') and set_current_vllm_config' "$worker"; then
    bad "vLLM без патча сна: спящий Qwen держит ~18 ГБ весов — scripts/install_h3_qwen_director.sh"
  else ok "vLLM: веса Qwen выгружаются во сне (патч weights-pool)"; fi
else
  qwen_state="$(supervisorctl status h3-qwen-director 2>/dev/null | awk '{print $2}' || true)"
  [[ "$qwen_state" == STOPPED ]] \
    && ok "Qwen Director отключён и его venv не установлен" \
    || bad "Qwen Director включён без рабочего venv"
fi
# Сценарист: оба профиля установлены, активен один (scripts/h3_scenarist_switch.sh).
sprofile="$(sed -n 's/.*H3_SCENARIST_PROFILE="\([^"]*\)".*/\1/p' "$qconf" 2>/dev/null | head -n1)"; sprofile="${sprofile:-default}"
aprofile="$(sed -n 's/.*H3_SCENARIST_PROFILE="\([^"]*\)".*/\1/p' /etc/supervisor/conf.d/h3-api.conf 2>/dev/null | head -n1)"; aprofile="${aprofile:-default}"
echo "  INFO  сценарист: активный профиль $sprofile"
[[ "$sprofile" == "$aprofile" ]] && ok "профиль сценариста одинаков у h3-qwen-director и h3-api" \
  || bad "профиль сценариста: h3-qwen-director=$sprofile, h3-api=$aprofile — bash $root/scripts/h3_scenarist_switch.sh --profile $sprofile"
next_ver="$("$root/qwen-next-venv/bin/python" -c 'import vllm; print(vllm.__version__)' 2>/dev/null)"
next_weights="$root/hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a/model.safetensors.index.json"
if [[ -n "$next_ver" && ( "$sprofile" != default || -f "$next_weights" ) ]]; then
  [[ "$next_ver" == 0.19.1 ]] && ok "сценарист 27B: qwen-next-venv vLLM $next_ver" || bad "qwen-next-venv: vLLM $next_ver, закреплена 0.19.1"
  [[ -f "$next_weights" ]] \
    && ok "сценарист 27B: веса Qwen3.8-27B-FP8 @017b9c7a" || bad "нет весов Qwen3.8-27B-FP8 @017b9c7a — scripts/install_h3_scenarist_next.sh"
elif [[ "$sprofile" != default ]]; then bad "активен $sprofile, но qwen-next-venv без vLLM"
else echo "  INFO  сценарист 27B не установлен (H3_SCENARIST_27B=0 или мало диска)"; fi
if [[ "$sprofile" != default ]]; then
  grep -q "$root/qwen-next-venv/bin" "$qconf" && ok "h3-qwen-director запускается из qwen-next-venv" || bad "h3-qwen-director: профиль $sprofile без qwen-next-venv в PATH"
fi
# Спящий 27B держит больше: CUDA-графы vLLM не освобождаются во сне (vLLM #47700). Норма уточняется на GPU.
sleep_limit=3000; [[ "$sprofile" == default ]] || sleep_limit=8000
if curl -fsS --max-time 5 http://127.0.0.1:8000/is_sleeping 2>/dev/null | grep -q true; then
  qpid="$(pgrep -f 'VLLM::EngineCore' | head -1)"
  qmem="$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits | awk -F', ' -v p="$qpid" '$1==p {print $2}')"
  [[ -n "$qmem" && "$qmem" -lt "$sleep_limit" ]] && ok "спящий Qwen держит ${qmem} МиБ VRAM" || bad "спящий Qwen держит ${qmem:-?} МиБ VRAM (норма < $sleep_limit)"
else echo "  INFO  Qwen сейчас не спит — проверка памяти во сне пропущена"; fi
grep -q director_fallback "$root/api/app/main.py" && ok "GPU API: запасной путь без плана Qwen (director_fallback)" \
  || bad "GPU API без director_fallback: невалидный план Qwen роняет задание"
# Уроки 2026-10-04.
remote_app_sha="$(cd "$root/api" && find app -name '*.py' ! -path '*/__pycache__/*' ! -path 'app/comfy_nodes/*' | LC_ALL=C sort \
  | while read -r f; do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"; done | sha256sum | cut -d' ' -f1)"
[[ "$remote_app_sha" == "$LOCAL_APP_SHA" ]] && ok "весь код GPU API (app/*.py) совпадает с Mac" \
  || bad "код GPU API на сервере отличается от Mac — с Mac: bash scripts/h3_server_drift.sh (ПОТЕРЯ = правка только на сервере)"
if grep -q 'H3_QWEN_AUTOMANAGE="1"' /etc/supervisor/conf.d/h3-api.conf 2>/dev/null; then
  echo "  INFO  Qwen: режим guard — сторож останавливает Qwen на рендер (+~1 мин 40 с к заданию); быстрый: --qwen sleep"
else
  st="$(supervisorctl status h3-qwen-vram-guard 2>/dev/null | awk '{print $2}')"
  [[ "$st" != RUNNING ]] && ok "Qwen: режим sleep (спит в памяти, без перезагрузки весов ComfyUI)" \
    || bad "сторож Qwen запущен при H3_QWEN_AUTOMANAGE=0 — supervisorctl stop h3-qwen-vram-guard"
fi
comfy_pid="$(pgrep -f 'main.py --listen 127.0.0.1 --port 18188' | head -1)"
# --high-ram on the 1 GB-swap Vast host thrashed it (load 41, sshd hung, 488 s/step) and was reverted (eeb2c5a8, 02.11).
swap_gb="$(awk '/SwapTotal/ {print int($2/1048576)}' /proc/meminfo)"
if tr '\0' ' ' < "/proc/${comfy_pid:-0}/cmdline" 2>/dev/null | grep -q -- '--high-ram'; then
  [[ "${swap_gb:-0}" -ge 16 ]] && echo "  INFO  ComfyUI с --high-ram, своп ${swap_gb} ГБ — следить за load и временем шага" \
    || bad "ComfyUI с --high-ram при свопе ${swap_gb:-0} ГБ — хост повиснет (02.11): очистить $root/comfy.env, supervisorctl restart h3-comfyui при пустой очереди"
else
  ok "ComfyUI без --high-ram (на хостах с малым свопом он вешает сервер; перезагрузку весов снижает группировка очереди)"
fi
acting="$(curl -fsS --max-time 10 http://127.0.0.1:18189/api/acting/capabilities 2>/dev/null)"
[[ "$acting" == *'"human":true'* && "$acting" == *'"animal":true'* ]] && ok "актёрская игра LivePortrait: human + animal" \
  || bad "актёрская игра недоступна (${acting:-нет ответа}) — scripts/install_h3_acting.sh"
[[ -s "$root/ComfyUI/models/sam2/sam2.1_hiera_large.pt" ]] && ok "SAM2 для уточнения персонажа" \
  || bad "нет models/sam2/sam2.1_hiera_large.pt — уточнение персонажа не заработает"
grep -q 'reference_video_short_edge' "$root/api/app/main.py" && ok "видео «Движение» уменьшается до 512p (меньше токенов)" \
  || bad "видео с ролью «Движение» идёт в полном размере (~2× дольше шаг) — нет хука reference_video_short_edge в main.py"
grep -q 'NamedTemporaryFile\|TemporaryDirectory\|mkstemp' "$root/api/app/director.py" && ok "кадры видео-референса читаются из файла (iPhone MP4 с индексом в конце)" \
  || bad "director.py читает видео потоком: iPhone MP4 падает «Не удалось прочитать кадры видео»"
# Сухая сборка графа со ВСЕМИ функциями Studio и сверка с реальными определениями узлов.
cd "$root/api" && "$root/api-venv/bin/python" - <<'PY' || fail=1
import json
from app.contract import GenerationRequest
from app.graph import build_prompt
info = json.load(open("/tmp/h3-object-info.json"))
state = {"version": 2, "keys": [
  {"t": 0, "orbit": 0, "elevation": 0, "distance": 1.3, "target": [0, 1.6, 0], "fov": 27, "focal_length_mm": 50, "aperture_f": 2.8, "focus_distance_m": 1.3},
  {"t": 4, "orbit": 0, "elevation": 0, "distance": 1.3, "target": [0, 1.6, 0], "fov": 27, "focal_length_mm": 50, "aperture_f": 2.8, "focus_distance_m": 1.3}]}
optics = {"sensor_format": "super_35", "focal_length_mm": 50, "aperture_f": 2.8, "focus_mode": "subject_head", "focus_distance_m": 1.3, "shutter_angle_deg": 180, "iso": 800}
request = GenerationRequest("<Picture 1> walks. <Picture 2> defines lighting only.", 5, "16:9", "quick", steps=25)
graph = build_prompt(request, ["hero.png", "light.png"], camera_state=state, camera_optics=optics, style_adapter="cartoon2d",
                     color_grade={"target_mean": [55, 10, 25], "target_std": [18, 6, 9], "strength": 0.5},
                     film_look={"contrast": 0.1, "grain": 0.3, "grain_size": 1.3, "seed": 1})
problems = []
for node_id, node in graph.items():
    spec = info.get(node["class_type"])
    if spec is None:
        problems.append(f"узел {node['class_type']} (#{node_id}) не установлен"); continue
    allowed = set(spec.get("input", {}).get("required", {})) | set(spec.get("input", {}).get("optional", {}))
    for key in node["inputs"]:
        if "." not in key and allowed and key not in allowed:
            problems.append(f"{node['class_type']}.{key}: такого входа у узла нет")
for p in problems: print("  FAIL  граф:", p)
chain = [graph["130"]["inputs"]["images"][0], graph.get("341", {}).get("inputs", {}).get("images", [None])[0]]
print(f"  {'OK  ' if not problems else 'FAIL'}  граф Studio: {len(graph)} узлов, пост-цепочка VAEDecode→H3ColorGrade→H3FilmLook→MP4 {chain}")
# A locked camera must reach H3 as text only: a guide video doubled the per-step cost (65 vs 29 s/step).
if "330" in graph or any(k.startswith("ref_videos.") for k in graph["136"]["inputs"]):
    problems.append("неподвижная камера добавила видеогид")
    print("  FAIL  неподвижная камера идёт видеогидом — graph.py старый (шаг в ~2 раза медленнее)")
else:
    print("  OK    неподвижная камера идёт текстом, без видеогида")
from app.graph import camera_guide_size
gw, gh = camera_guide_size(768, 1344)
if gw * gh > 256 * 448:
    problems.append("видеогид камеры крупный")
    print(f"  FAIL  видеогид камеры {gw}x{gh}: каждый кадр латента +{gw*gh//256} токенов (норма 256x448 = 448)")
else:
    print(f"  OK    видеогид движения камеры {gw}x{gh} (+{gw*gh//256} токенов на кадр латента)")
raise SystemExit(1 if problems else 0)
PY
exit $fail
REMOTE

echo "== 2. Mac Studio (127.0.0.1:18187)"
h="$(curl -fsS --max-time 5 http://127.0.0.1:18187/api/health)" && ok "Studio отвечает: $h" || bad "Studio 18187 не отвечает"
echo "$h" | grep -q '"gpu_reachable": *true\|"gpu_reachable":true' && ok "Studio видит GPU" || bad "Studio не видит GPU — проверь туннель"
api="$(curl -fsS --max-time 5 http://127.0.0.1:18187/openapi.json)"
for route in /api/history /director-schema.json /api/context-ir/jobs /api/context-ir/capabilities; do
  echo "$api" | grep -q "\"$route\"" && ok "сервер Studio знает $route" || bad "сервер Studio старый (нет $route) — «Перезапустить Studio.command»"
done
local_gpu_api_url="${H3_GPU_API_URL:-http://127.0.0.1:18190}"
caps="$(curl -fsS --max-time 10 "$local_gpu_api_url/api/capabilities")" && ok "туннель Mac→GPU API: $caps" || bad "туннель $local_gpu_api_url не работает"
python3 scripts/verify_context_ir.py || bad "Context-IR Mac/GPU не готов — см. диагностику выше"
cmp -s app/director_schema.json preview/h3-director/director-schema.json && ok "копия схемы для Studio актуальна" || bad "preview/h3-director/director-schema.json устарела — bash scripts/check.sh"

echo "== 3. Один в один с эталоном рабочего сервера (${H3_LOCK_DIR:-deploy/live-lock})"
lock="${H3_LOCK_DIR:-deploy/live-lock}"
if [[ -f "$lock/components.txt" ]]; then
  now="$(mktemp -d -t h3-now.XXXXXX)"
  bash scripts/h3_server_lock.sh --host "$host" --port "$port" --identity "$identity" --out "$now" >/dev/null
  # Director 8B is optional when explicitly disabled; the H3 text encoder is a separate model.
  qwen_director_disabled="$(ssh -i "$identity" -p "$port" -o BatchMode=yes "$host" \
    'if grep -q "^autostart=false" /etc/supervisor/conf.d/h3-qwen-director.conf && \
        grep -q "H3_QWEN_AUTOMANAGE=\"0\"" /etc/supervisor/conf.d/h3-api.conf && \
        supervisorctl status h3-qwen-director | grep -q STOPPED; then echo yes; fi' 2>/dev/null)"
  # Веса и репозитории: каждый файл эталона с тем же размером, каждая ревизия та же.
  # Блобы hf-cache сверяются по размеру: их имена — хеши ревизии модели.
  while read -r kind a b; do
    if [[ "$kind" == repo ]]; then
      grep -qx "repo $a $b" "$now/components.txt" && ok "ревизия $a ${b:0:8}" \
        || bad "ревизия $a: нужна ${b:0:8}, на сервере $(awk -v n="$a" '$1=="repo" && $2==n {print substr($3,1,8)}' "$now/components.txt")"
    elif [[ "$kind" == file ]]; then
      if [[ "$qwen_director_disabled" == yes && "$b" == hf-cache/hub/models--Qwen--Qwen3-VL-8B-Instruct/* ]]; then
        continue
      fi
      # Optional 27B scenarist (H3_SCENARIST_27B=0 skips it on small disks): only when installed.
      if [[ "$b" == hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/* ]] && \
         ! grep -q ' hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/' "$now/components.txt"; then
        continue
      fi
      grep -qx "file $a $b" "$now/components.txt" && ok "вес $(basename "$b")" \
        || bad "нет веса или другой размер: $b ($a байт)"
    fi
  done < "$lock/components.txt"
  # Пакеты: отсутствующие и другие версии ключевых пакетов — FAIL, прочие расхождения версий — INFO.
  key='^(torch|torchvision|torchaudio|vllm|xgrammar|transformers|numpy|opencv-.*|onnxruntime-gpu|sam-2|comfyui-.*|safetensors|triton|av|fastapi|uvicorn)$'
  for f in "$lock"/*venv.txt; do
    v="$(basename "$f" .txt)"; [[ -s "$f" ]] || continue
    missing=0; keydiff=0; otherdiff=0
    while IFS='=' read -r name _ ver; do
      grep -qxF "$name==$ver" "$now/$v.txt" && continue  # пакет может стоять в двух версиях: сверяем строку целиком
      have="$(awk -F'==' -v n="$name" '$1==n {print $2}' "$now/$v.txt" | paste -sd, -)"
      if [[ -z "$have" ]]; then printf '  FAIL  %s: нет пакета %s==%s\n' "$v" "$name" "$ver"; missing=$((missing+1))
      elif [[ "$have" != "$ver" ]]; then
        if [[ "$name" =~ $key ]]; then printf '  FAIL  %s: %s %s (эталон %s)\n' "$v" "$name" "$have" "$ver"; keydiff=$((keydiff+1))
        else otherdiff=$((otherdiff+1)); fi
      fi
    done < "$f"
    (( missing + keydiff == 0 )) && ok "$v: все $(wc -l < "$f" | tr -d ' ') пакетов эталона на месте" || fail=1
    (( otherdiff == 0 )) || echo "  INFO  $v: у $otherdiff второстепенных пакетов другая версия"
  done
  rm -rf "$now"
else
  echo "  INFO  эталона нет — сравнение пропущено (scripts/h3_server_lock.sh)"
fi

echo
[[ $fail == 0 ]] && echo "ИТОГ: всё OK — можно делать тестовый рендер (5 сек, 0,90 Мп)." || echo "ИТОГ: есть FAIL — см. выше."
exit $fail
