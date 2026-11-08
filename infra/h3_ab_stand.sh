#!/usr/bin/env bash
# Проверка A/B-харнесса (scripts/h3_ab.py) на локальном стенде без GPU, во временном контейнере.
#   bash infra/h3_ab_stand.sh
# Контейнер h3-ab-stand из образа h3-local:base: том h3root только для чтения, app/ этой копии репозитория,
# порты 38188/38189 (стенд h3-local на 28188/28189 не трогается). Три прогона scripts/h3_ab.py --golden --sparse:
#   1. настоящий ComfyUI --cpu, без файла realism LoRA → realism-* отклонены 422, остальные графы
#      приняты валидацией ComfyUI и падают на пустых весах;
#   2. то же с пустой заглушкой LoRA в /tmp контейнера → все шесть графов приняты ComfyUI;
#   3. mock ComfyUI (infra/ab_mock_comfyui.py, object_info из п. 1) → MP4, тайминги, VRAM, отчёт;
#      затем повтор baseline с --merge (процедура сравнения двух сборок ComfyUI).
# Контейнер удаляется в конце. Результаты: logs/ab-*-stand-*.
set -euo pipefail
cd "$(dirname "$0")/.."
ctx=(--context colima-h3)
name=h3-ab-stand
api=http://127.0.0.1:38189
box() { docker "${ctx[@]}" exec -i "$name" sh; }
docker "${ctx[@]}" rm -f "$name" >/dev/null 2>&1 || true
trap 'docker "${ctx[@]}" rm -f "$name" >/dev/null 2>&1 || true' EXIT
docker "${ctx[@]}" run --platform linux/amd64 -d --name "$name" -p 127.0.0.1:38188:18188 -p 127.0.0.1:38189:18189 \
  -v h3root:/h3root:ro -v "$PWD/app":/repo-app:ro -v "$PWD/scripts":/repo-scripts:ro -v "$PWD/infra":/repo-infra:ro \
  h3-local:base sleep infinity >/dev/null

start_api() {
  box <<'SH'
for p in $(pgrep -f '^/workspace/h3/api-venv/bin/python'); do kill "$p"; done; sleep 1
mkdir -p /tmp/api && ln -sfn /repo-app /tmp/api/app
cd /tmp/api && H3_JOB_REGISTRY_PATH=/tmp/registry.sqlite3 H3_ENABLED_STYLE_ADAPTERS=style3d \
  nohup /workspace/h3/api-venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18189 > /tmp/api.log 2>&1 &
SH
  for _ in $(seq 1 30); do curl -fs -o /dev/null "$api/api/live" && return; sleep 1; done
  box <<<'tail -20 /tmp/api.log'; exit 1
}
ab() { python3 scripts/h3_ab.py run --golden --gpu-url "$api" --poll 1 --timeout 900 "$@"; }

echo "== Стенд: ComfyUI --cpu (только чтение h3root)"
box <<'SH'
ln -sfn /h3root/workspace /workspace && ln -sfn /h3root/.uv /.uv
mkdir -p /tmp/in /tmp/out /tmp/user /tmp/tmp /tmp/models/loras
printf 'ab:\n  base_path: /tmp/models\n  loras: loras\n' > /tmp/extra.yaml
cd /workspace/h3/ComfyUI && nohup /workspace/h3/venv/bin/python main.py --cpu --listen 0.0.0.0 --port 18188 \
  --disable-auto-launch --input-directory /tmp/in --output-directory /tmp/out --user-directory /tmp/user \
  --temp-directory /tmp/tmp --extra-model-paths-config /tmp/extra.yaml > /tmp/comfy.log 2>&1 &
SH
for _ in $(seq 1 90); do curl -fs -o /dev/null http://127.0.0.1:38188/system_stats && break; sleep 5; done
curl -fs -o /dev/null http://127.0.0.1:38188/system_stats || { box <<<'tail -30 /tmp/comfy.log'; exit 1; }
start_api

echo "== 1. Без весов realism LoRA: ожидаем 422 для realism-*"
ab --sparse --tag stand-real-nolora
echo "== 2. С заглушкой LoRA: все графы приняты ComfyUI"
box <<<': > /tmp/models/loras/h3-realism-people-t2v-i2v-r2v.safetensors'
ab --sparse --tag stand-real

echo "== 3. Mock ComfyUI: MP4, тайминги, VRAM, отчёт"
box <<'SH'
curl -fs http://127.0.0.1:18188/object_info > /tmp/object_info.json
for p in $(pgrep -f 'main.py --cpu'); do kill "$p"; done; sleep 2
nohup /workspace/h3/venv/bin/python /repo-infra/ab_mock_comfyui.py /tmp/object_info.json \
  --lora h3-realism-people-t2v-i2v-r2v.safetensors > /tmp/mock.log 2>&1 &
SH
sleep 4
start_api
ab --sparse --tag stand-mock
mock_run="$(ls -d logs/ab-*-stand-mock | sort | tail -n 1)"
ab --variants baseline --tag stand-comfy-new --merge "$mock_run"
echo "OK: $(ls -d logs/ab-*-stand-comfy-new | sort | tail -n 1)/index.html"
