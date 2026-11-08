#!/usr/bin/env bash
set -euo pipefail

# Qwen Director is a local, serialized planning service. Its sleep endpoints
# release VRAM between plan compilation and the MiniMax H3 render.
model="${H3_DIRECTOR_MODEL:-Qwen/Qwen3-VL-8B-Instruct}"
host="${H3_DIRECTOR_BIND_HOST:-127.0.0.1}"
port="${H3_DIRECTOR_PORT:-8000}"
if [[ "$host" != "127.0.0.1" && "$host" != "::1" && "$host" != "localhost" ]]; then
  echo "Refusing to expose vLLM dev sleep endpoints on a non-loopback interface" >&2
  exit 2
fi
# H3_SCENARIST_PROFILE picks a stronger planner (app/scenarist_profile.py has the same table).
# Unset or "default" = the exact 2026-09-30 command below, from the vLLM 0.15.0 qwen-venv.
profile="${H3_SCENARIST_PROFILE:-default}"
if [[ "$profile" != default ]]; then
  case "$profile" in
    # Apache-2.0, Qwen3_5ForConditionalGeneration (Gated DeltaNet + gated attention, vision tower).
    qwen3.8-27b-fp8) pinned_model=Qwen/Qwen3.8-27B-FP8; revision=017b9c7af6b5689d5dd426a76e0bc077eb5ca20a ;;
    qwen3.6-27b-fp8) pinned_model=Qwen/Qwen3.6-27B-FP8; revision=e89b16ebf1988b3d6befa7de50abc2d76f26eb09 ;;
    *) echo "Unknown H3_SCENARIST_PROFILE=$profile (default, qwen3.8-27b-fp8, qwen3.6-27b-fp8)" >&2; exit 2 ;;
  esac
  model="${H3_DIRECTOR_MODEL:-$pinned_model}"
  # Separate venv with vLLM 0.19.1 (scripts/install_h3_scenarist_next.sh); qwen-venv stays 0.15.0.
  venv="${H3_QWEN_NEXT_VENV:-${H3_ROOT:-/workspace/h3}/qwen-next-venv}"
  [[ -x "$venv/bin/vllm" ]] || {
    echo "Profile $profile needs $venv: H3_SCENARIST_PROFILE=$profile bash scripts/install_h3_scenarist_next.sh" >&2
    exit 2
  }
  export VLLM_SERVER_DEV_MODE=1
  # The installer pre-fetched the pinned snapshot into HF_HOME: never download at service start.
  export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
  args=(serve "$model")
  [[ "$model" != "$pinned_model" ]] || args+=(--revision "$revision")
  # 0.45 of a 96 GB card ≈ 43 GB awake: ~31 GB FP8 weights + vision + KV/GDN state for 32k.
  # It wakes only with an idle ComfyUI queue and H3 unloaded (app/director.py exclusive_gpu).
  args+=(--dtype bfloat16 --host "$host" --port "$port" --enable-sleep-mode
    --gpu-memory-utilization "${H3_QWEN_GPU_UTIL:-0.45}" --max-model-len "${H3_QWEN_MAX_MODEL_LEN:-32768}"
    # One request at a time (planning_lock): few sequences = small GDN state cache and CUDA graphs.
    --max-num-seqs "${H3_QWEN_MAX_NUM_SEQS:-4}"
    # Director: 9 pictures + 3 videos x 3 frames = 18 images; frames only, never video input.
    --limit-mm-per-prompt '{"image":24,"video":0}'
    # Thinking off on the server as well; the API also sends it per request (the qwen3
    # parser reads the request kwargs). The parser strips a stray </think> from content.
    --reasoning-parser qwen3 --default-chat-template-kwargs '{"enable_thinking":false}')
  # Recipe options, off by default until checked on the GPU together with sleep/wake:
  # FP8 KV cache (vLLM issue #48310 SW3: wake resets KV scales; #37618 Qwen3.5 FP8-KV accuracy),
  # MTP drafting, and eager mode (smaller asleep footprint: CUDA graphs stay resident, #47700).
  [[ "${H3_QWEN_KV_FP8:-0}" != 1 ]] || args+=(--kv-cache-dtype fp8)
  [[ "${H3_QWEN_MTP:-0}" != 1 ]] || \
    args+=(--speculative-config "{\"method\":\"mtp\",\"num_speculative_tokens\":${H3_QWEN_MTP_TOKENS:-3}}")
  [[ "${H3_QWEN_ENFORCE_EAGER:-0}" != 1 ]] || args+=(--enforce-eager)
  args+=(--structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true,"disable_fallback":true}')
  exec "$venv/bin/vllm" "${args[@]}"
fi
command -v vllm >/dev/null || { echo "Install vLLM in an isolated Qwen environment first" >&2; exit 2; }
export VLLM_SERVER_DEV_MODE=1
args=(serve "$model" --dtype bfloat16 --host "$host" --port "$port" --enable-sleep-mode)
# Optional, for tuning on smaller GPUs; unset = identical to the 2026-09-30 command.
[[ -z "${H3_QWEN_GPU_UTIL:-}" ]] || args+=(--gpu-memory-utilization "$H3_QWEN_GPU_UTIL")
[[ -z "${H3_QWEN_MAX_MODEL_LEN:-}" ]] || args+=(--max-model-len "$H3_QWEN_MAX_MODEL_LEN")
# Explicit backend and compact JSON prevent unbounded formatting tokens.
args+=(--structured-outputs-config '{"backend":"xgrammar","disable_any_whitespace":true,"disable_fallback":true}')
exec vllm "${args[@]}"
