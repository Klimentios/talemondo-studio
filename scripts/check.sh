#!/usr/bin/env bash
# Полная проверка перед релизом: JS-тесты Studio, синтаксис модулей, Python-тесты Mac Studio и GPU API.
# Запуск из корня проекта:  bash scripts/check.sh
set -euo pipefail
shopt -s nullglob
cd "$(dirname "$0")/.."

echo "== Studio: синтаксис модулей"
for f in preview/h3-director/*.mjs preview/h3-director/editor/*.mjs preview/h3-director/assets/palettes/*.mjs; do node --check "$f"; done

echo "== Studio: JS-тесты"
node --test preview/h3-director/*.test.mjs preview/h3-director/editor/*.test.mjs 2>&1 | tail -n 8

echo "== Схема Director: валидный JSON + копия для Studio"
python3 -m json.tool app/director_schema.json >/dev/null
cp app/director_schema.json preview/h3-director/director-schema.json
cp app/motion_reference_policy.json preview/h3-director/motion-reference-policy.json

echo "== Python: Mac Studio + GPU API"
# Тесты — в своём venv (системный Python без pytest; PEP 668). Создаётся один раз.
venv="${H3_CHECK_VENV:-$HOME/Library/Caches/h3-check-venv}"
if ! "$venv/bin/python" -c 'import pytest, fastapi, numpy, PIL' 2>/dev/null; then
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install -q -r requirements.txt pytest numpy pillow
fi
"$venv/bin/python" -m pytest -q tests
"$venv/bin/python" -m py_compile scripts/h3_install_render_assets.py scripts/install_h3_server_queue_tunnel.py scripts/backup_mac_studio.py scripts/h3_ab.py scripts/h3_scenarist_ab.py infra/ab_mock_comfyui.py infra/h3_image_weights.py infra/h3_restore_symlinks.py
bash -n scripts/h3_ab.sh infra/h3_ab_stand.sh infra/local/h3img_box.sh scripts/h3_overlay_bundle.sh

echo "== Установщик GPU: синтаксис"
bash -n scripts/install_h3_server_queue.sh infra/h3_server_queue_tunnel.sh scripts/run_h3_qwen_director.sh scripts/install_h3_scenarist_next.sh scripts/h3_scenarist_switch.sh scripts/h3_scenarist_conf.sh scripts/install_h3_h200_overlay.sh scripts/deploy_h3_new_server.sh scripts/h3_new_server.sh scripts/h3_server_drift.sh scripts/install_h3_acting.sh scripts/install_h3_audio_cosyvoice.sh scripts/install_h3_audio_acestep.sh scripts/h3_server_lock.sh scripts/verify_h3_server.sh infra/*.sh

echo "OK — всё зелёное."
