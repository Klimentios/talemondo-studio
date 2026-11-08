#!/usr/bin/env bash
# Prints the h3-qwen-director Supervisor program for a scenarist profile (stdout, changes nothing).
#   bash scripts/h3_scenarist_conf.sh PROFILE ROOT TEMPLATE [GPU_UTIL] [SLEEP_LEVEL]
# default = the template exactly as install_h3_qwen_director.sh writes it (qwen-venv, vLLM 0.15.0).
# A 27B profile differs only in PATH (qwen-next-venv), the VRAM budget and three variables.
# Used by scripts/h3_scenarist_switch.sh (server) and scripts/h3_server_drift.sh (expected conf).
set -euo pipefail
profile="${1:?profile}"; root="${2:?root}"; template="${3:?template}"
util="${4:-0.45}"; level="${5:-1}"
case "$profile" in
  default) sed "s|/workspace/h3|$root|g" "$template"; exit 0 ;;
  qwen3.8-27b-fp8|qwen3.6-27b-fp8) ;;
  *) echo "Unknown scenarist profile: $profile" >&2; exit 2 ;;
esac
[[ "$util" =~ ^0\.[0-9]+$ ]] || { echo "GPU util must look like 0.45: $util" >&2; exit 2; }
[[ "$level" =~ ^[12]$ ]] || { echo "Sleep level must be 1 or 2: $level" >&2; exit 2; }
sed -e "s|/workspace/h3|$root|g" -e "s|$root/qwen-venv/bin|$root/qwen-next-venv/bin|" \
    -e "s|H3_QWEN_GPU_UTIL=\"0.30\"|H3_QWEN_GPU_UTIL=\"$util\"|" \
    -e "s|^environment=\(.*\)$|environment=\1,H3_ROOT=\"$root\",H3_SCENARIST_PROFILE=\"$profile\",H3_QWEN_SLEEP_LEVEL=\"$level\"|" \
    "$template"
