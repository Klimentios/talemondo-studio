#!/usr/bin/env bash
# Локальный стенд H3 на Mac: софт сервера из снимка, без видеокарты и без весов.
# Для отладки конфигов, узлов и сборки задания; рендер и Qwen проверяются только на GPU-сервере.
#
#   bash infra/h3_local.sh up      [--snapshot DIR]  VM Colima «h3» (x86 через Rosetta), образ, том со снимком, заглушки весов
#   bash infra/h3_local.sh start                     ComfyUI (CPU) :28188 и GPU API :28189 с кодом app/ из репозитория
#   bash infra/h3_local.sh check                     узлы загрузились + графы (tests/golden) совпадают со схемой узлов
#   bash infra/h3_local.sh stop | down               остановить контейнер | остановить VM (том со снимком остаётся)
#
# Ничего не меняет на сервере и в снимке. Код app/ монтируется из репозитория только для чтения.
set -euo pipefail
cd "$(dirname "$0")/.."
ctx=(--context colima-h3)
# shellcheck source=h3_snapshot_spec.sh
. infra/h3_snapshot_spec.sh
# Only `up` needs a snapshot; start/check/stop work on the unpacked volume after it is deleted.
snapshot="$({ ls -d "$HOME"/H3-Snapshots/h3-* 2>/dev/null || true; } | sort | tail -n 1)"
cmd="${1:-}"; shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in --snapshot) snapshot="$2"; shift 2 ;; *) sed -n '2,10p' "$0"; exit 2 ;; esac
done
in_box() { docker "${ctx[@]}" exec h3-local sh -c "$1"; }

case "$cmd" in
  up)
    [[ -f "$snapshot/software.tar.zst" ]] || { echo "Нет снимка: $snapshot" >&2; exit 2; }
    colima status h3 >/dev/null 2>&1 || colima start h3 --arch aarch64 --vm-type vz --vz-rosetta \
      --cpu 6 --memory 10 --disk 70 --runtime docker
    docker "${ctx[@]}" build -q -t h3-local:base infra/local >/dev/null
    docker "${ctx[@]}" volume create h3root >/dev/null
    stamp="$(docker "${ctx[@]}" run --platform linux/amd64 --rm -v h3root:/h3root h3-local:base cat /h3root/SNAPSHOT 2>/dev/null || true)"
    if [[ "$stamp" != "$(basename "$snapshot")" ]]; then
      echo "Распаковка $(basename "$snapshot") в том h3root (~6 мин)…"
      docker "${ctx[@]}" run --platform linux/amd64 --rm -v h3root:/h3root h3-local:base sh -c 'rm -rf /h3root/* /h3root/.uv'
      zstd -dc -T0 "$snapshot/software.tar.zst" \
        | docker "${ctx[@]}" run --platform linux/amd64 -i --rm -v h3root:/h3root h3-local:base tar -x -C /h3root --numeric-owner
      # Weights are not part of the snapshot: empty files with the same names let ComfyUI list them.
      { grep -viE "$H3_BANNED_WEIGHTS_RE" "$snapshot/weights.tsv" || true; } | cut -f1 | docker "${ctx[@]}" run --platform linux/amd64 -i --rm -v h3root:/h3root h3-local:base sh -c \
        'cd /h3root/workspace/h3 && while IFS= read -r p; do mkdir -p "$(dirname "$p")"; [ -e "$p" ] || : > "$p"; done; mkdir -p input output api-data'
      basename "$snapshot" | docker "${ctx[@]}" run --platform linux/amd64 -i --rm -v h3root:/h3root h3-local:base sh -c 'cat > /h3root/SNAPSHOT'
    fi
    echo "Стенд готов: $(basename "$snapshot"). Дальше: bash infra/h3_local.sh start"
    ;;
  start)
    docker "${ctx[@]}" rm -f h3-local >/dev/null 2>&1 || true
    docker "${ctx[@]}" run --platform linux/amd64 -d --name h3-local -p 127.0.0.1:28188:18188 -p 127.0.0.1:28189:18189 \
      -v h3root:/h3root -v "$PWD/app":/repo-app:ro h3-local:base sleep infinity >/dev/null
    in_box 'ln -sfn /h3root/workspace /workspace && ln -sfn /h3root/.uv /.uv
      cd /workspace/h3/ComfyUI && nohup /workspace/h3/venv/bin/python main.py --cpu --listen 0.0.0.0 --port 18188 \
        --disable-auto-launch --input-directory /workspace/h3/input --output-directory /workspace/h3/output > /tmp/comfy.log 2>&1 &
      mkdir -p /tmp/api && ln -sfn /repo-app /tmp/api/app
      cd /tmp/api && H3_JOB_REGISTRY_PATH=/workspace/h3/api-data/job-registry.sqlite3 H3_ENABLED_STYLE_ADAPTERS=style3d \
        nohup /workspace/h3/api-venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18189 > /tmp/api.log 2>&1 &'
    for _ in $(seq 1 60); do curl -fs -o /dev/null http://127.0.0.1:28188/system_stats && break; sleep 5; done
    curl -fs -o /dev/null http://127.0.0.1:28188/system_stats || { in_box 'tail -30 /tmp/comfy.log'; exit 1; }
    echo "ComfyUI: http://127.0.0.1:28188   GPU API: http://127.0.0.1:28189 (код app/ из репозитория)"
    ;;
  check)
    echo "== Узлы ComfyUI"
    if in_box "grep -E 'IMPORT FAILED|Cannot import' /tmp/comfy.log"; then echo "FAIL: узлы не загрузились" >&2; exit 1; fi
    in_box "grep -A12 'Import times for custom nodes' /tmp/comfy.log | grep seconds | sed 's/.*custom_nodes\//  OK /'"
    echo "== Графы против схемы узлов"
    python3 scripts/h3_graph_validate.py --object-info http://127.0.0.1:28188/object_info tests/golden/*_graph.json tests/golden/*_gpu_*.json
    echo "== GPU API"
    curl -fsS --max-time 10 http://127.0.0.1:28189/api/health && echo || { echo "GPU API не отвечает:"; in_box 'tail -20 /tmp/api.log'; }
    ;;
  stop) docker "${ctx[@]}" rm -f h3-local >/dev/null && echo "Контейнер остановлен" ;;
  down) docker "${ctx[@]}" rm -f h3-local >/dev/null 2>&1 || true; colima stop h3 ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
