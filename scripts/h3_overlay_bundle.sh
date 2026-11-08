#!/usr/bin/env bash
# The GPU overlay bundle (app/, installers, configs) exactly as it ships to a server. Shared by
# scripts/deploy_h3_new_server.sh (scp to the server) and infra/h3_image_build.sh (offline image).
#   bash scripts/h3_overlay_bundle.sh OUT.tar.gz
set -euo pipefail
archive="${1:?usage: h3_overlay_bundle.sh OUT.tar.gz}"
workspace="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COPYFILE_DISABLE=1 tar -czf "$archive" -C "$workspace" \
  --exclude='__pycache__' --exclude='*.pyc' \
  app requirements.txt \
  scripts/install_h3_h200_overlay.sh scripts/install_cache_metrics.py scripts/h3_install_render_assets.py scripts/install_h3_qwen_director.sh \
  scripts/install_h3_audio_cosyvoice.sh scripts/install_h3_audio_acestep.sh scripts/install_h3_server_queue.sh \
  scripts/run_h3_qwen_director.sh scripts/h3_runtime_watchdog.py \
  scripts/install_h3_studio_extensions.py scripts/h3_attest_weights.py \
  scripts/install_h3_scenarist_next.sh scripts/h3_scenarist_switch.sh scripts/h3_scenarist_conf.sh \
  deploy/scenarist-models \
  deploy/h3-qwen-director.conf deploy/h3-qwen-vram-guard.conf \
  deploy/h3-watchdog.conf deploy/h3-prewarm.conf scripts/h3_prewarm.sh \
  vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh \
  vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt \
  vendor/minimax-h3-local-studio/assets/comfy-extra-nodes-requirements.txt
for required in \
  app/render_assets.json scripts/h3_install_render_assets.py scripts/install_cache_metrics.py scripts/install_h3_server_queue.sh \
  scripts/install_h3_studio_extensions.py scripts/h3_attest_weights.py \
  deploy/h3-qwen-director.conf deploy/h3-watchdog.conf \
  scripts/install_h3_qwen_director.sh scripts/h3_runtime_watchdog.py \
  scripts/install_h3_scenarist_next.sh scripts/h3_scenarist_switch.sh scripts/h3_scenarist_conf.sh \
  vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh \
  vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt \
  vendor/minimax-h3-local-studio/assets/comfy-extra-nodes-requirements.txt; do
  tar -tzf "$archive" "$required" >/dev/null || {
    echo "GPU overlay bundle is missing $required" >&2
    exit 2
  }
done
