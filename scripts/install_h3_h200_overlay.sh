#!/usr/bin/env bash
set -euo pipefail
# Repos under /workspace are often owned by another uid (Vast image, copied volume). As root, git then
# refuses them as "dubious ownership" (exit 128). Trust them for this script only, no global config.
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'

# Run inside a freshly deployed, pinned H3 server. This script never submits a render.
if [[ "${1:-}" != "--apply" ]]; then
  echo "Usage: install_h3_h200_overlay.sh --apply"
echo "Installs the GPU API, verified 3D style LoRA, camera/color nodes and cache node."
  exit 0
fi

root="${H3_ROOT:-/workspace/h3}"
comfy="$root/ComfyUI"
bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh
. "$bundle/vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$root/backup/h200-overlay-$stamp"
command -v supervisorctl >/dev/null || { echo "supervisorctl is required" >&2; exit 2; }
[[ -x "$root/venv/bin/python" && -d "$comfy/models" ]] || {
  echo "Deploy the pinned base H3 stack first." >&2; exit 2;
}
"$root/venv/bin/python" -c 'import scipy.ndimage' || {
  echo "ComfyUI Python needs scipy.ndimage for the optical camera guide" >&2; exit 3;
}
[[ -f /etc/vast-agents-guide.md ]] && cat /etc/vast-agents-guide.md
if ! command -v ffmpeg >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg
fi
if [[ ! -f "$bundle/deploy/h3-qwen-director.conf" || ! -f "$bundle/deploy/h3-watchdog.conf" || ! -f "$bundle/scripts/install_h3_qwen_director.sh" ]]; then
  echo "Bundled Qwen Director installer or Supervisor config is missing." >&2
  exit 2
fi
available="$(df -PB1 "$root" | awk 'NR==2 {print $4}')"
# Structural control (ControlNet, SDPose, RT-DETR, Depth-Anything-3) was removed; only overlay weights below.
need=0
# X2 Detail VAE (5.25 GB) and all four style adapter modes plus both face-realism A/B adapters.
for model_spec in \
  'vae/MiniMax-H3-X2-Detail-v1.safetensors:5246877148' \
  'loras/3dsrx_minimax_h3_rank16-step00001250.safetensors:298260424' \
  'loras/minimax-h3-rough-2d-cartoon-illustration-1200.safetensors:74570640' \
  'loras/wushu_action_h3_lora_v5_3000_pruned.safetensors:155109672' \
  'loras/wushu_spatial_physics_clean_3000_pruned.safetensors:155109672' \
  'loras/camera_motion_h3_lora_v1_3000_pruned.safetensors:155109680' \
  'loras/h3-realism-people-t2v-i2v-r2v.safetensors:131229656' \
  'loras/minimax-h3-facial-realism-closeup-cp2000.safetensors:74570624'; do
  model_path="${model_spec%%:*}"; model_bytes="${model_spec##*:}"
  [[ -s "$comfy/models/$model_path" ]] || need=$((need + model_bytes))
done
# Image build (H3_IMAGE_BUILD=1, see h3_gpu_env.sh): weights are only recorded, not downloaded.
if h3_image_build; then need=0; fi
if ((available < need)); then
  echo "Need $need bytes free for enabled overlay weights and adapters; available: $available" >&2
  exit 2
fi
# Second scenarist model (installed, not activated): H3_SCENARIST_27B=1 required, 0 skipped,
# unset = installed when the disk has room for it after the overlay weights (~44 GB more).
scenarist_27b="${H3_SCENARIST_27B:-auto}"
if [[ "$scenarist_27b" == auto ]]; then
  # Image build: no 27B weights are downloaded, the venv alone fits (its own disk check stays).
  if h3_image_build || [[ -x "$root/qwen-next-venv/bin/vllm" ]] || ((available >= need + 44000000000)); then scenarist_27b=1
  else scenarist_27b=0; echo "WARNING: not enough disk for the optional 27B scenarist; skipping it (H3_SCENARIST_27B=1 to insist)" >&2; fi
fi
# The active profile survives a reinstall (install_h3_qwen_director.sh writes the default conf).
active_scenarist="${H3_SCENARIST_PROFILE:-}"
if [[ -z "$active_scenarist" && -f /etc/supervisor/conf.d/h3-qwen-director.conf ]]; then
  active_scenarist="$(sed -n 's/.*H3_SCENARIST_PROFILE="\([^"]*\)".*/\1/p' /etc/supervisor/conf.d/h3-qwen-director.conf | head -n1)"
fi
active_scenarist="${active_scenarist:-default}"

h3_image_build || python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:18188/queue", timeout=10) as response:
    queue = json.load(response)
if queue.get("queue_running") or queue.get("queue_pending"):
    raise SystemExit("ComfyUI queue is not empty; refusing a service restart")
PY

mkdir -p "$backup" "$root/api" "$root/api-data" "$comfy/models/loras" \
  "$comfy/models/diffusion_models"
[[ ! -d "$root/api/app" ]] || cp -a "$root/api/app" "$backup/app"
[[ ! -f /etc/supervisor/conf.d/h3-api.conf ]] || \
  cp -a /etc/supervisor/conf.d/h3-api.conf "$backup/h3-api.conf"
[[ ! -f /etc/supervisor/conf.d/h3-watchdog.conf ]] || \
  cp -a /etc/supervisor/conf.d/h3-watchdog.conf "$backup/h3-watchdog.conf"
# Replace, not merge: a module deleted from the repo must not linger on the server (it was backed
# up above). Found by the offline image build: app/replacement_motion.py stayed after its removal.
rm -rf "$root/api/app"
cp -a "$bundle/app" "$root/api/"
cp "$bundle/requirements.txt" "$root/api/requirements.txt"
mkdir -p "$root/scripts"
install -m 0755 "$bundle/scripts/h3_install_render_assets.py" "$root/scripts/h3_install_render_assets.py"
find "$root/api/app" -type d -name __pycache__ -prune -exec rm -rf {} +

# PEP 668: never pip-install into the system Python. Every package goes into a venv.
# Ubuntu images often lack python3-venv: `python3 -m venv` then leaves a half-made
# venv without pip, and the next run fails with "externally-managed-environment".
ensure_venv() {
  local dir="$1"; shift
  if ! python3 -c 'import ensurepip, venv' >/dev/null 2>&1; then
    local pyver; pyver="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "python${pyver}-venv" python3-venv python3-pip || \
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip
  fi
  if [[ -x "$dir/bin/python" ]] && ! "$dir/bin/python" -m pip --version >/dev/null 2>&1; then
    echo "Recreating broken venv without pip: $dir" >&2
    rm -rf "$dir"
  fi
  [[ -x "$dir/bin/python" ]] || python3 -m venv "$@" "$dir"
}
ensure_venv "$root/api-venv"
"$root/api-venv/bin/python" -m pip install -r "$root/api/requirements.txt"
"$root/api-venv/bin/python" -m compileall -q "$root/api/app"

fetch_checked() {
  local repo="$1" revision="$2" path="$3" dest="$4" digest="$5"
  local url="https://huggingface.co/$repo/resolve/$revision/$path"
  if h3_image_build; then
    h3_image_record_weight "$dest" - "$digest" "$repo" "$revision" "$path"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]]; then
    if echo "$digest  $dest" | sha256sum -c --status; then
      echo "Verified existing: $dest"
      return
    fi
    echo "Refusing wrong-hash model: $dest" >&2
    exit 3
  fi
  local curl_args=(-fL --retry 8 --retry-delay 5 --continue-at -)
  if [[ -s /root/.cache/h3-hf-curl.conf ]]; then
    curl_args+=(--config /root/.cache/h3-hf-curl.conf)
  fi
  curl "${curl_args[@]}" -o "$dest.part" "$url"
  echo "$digest  $dest.part" | sha256sum -c --status || {
    echo "SHA-256 mismatch: $dest.part" >&2; exit 3;
  }
  mv "$dest.part" "$dest"
}

fetch_checked prithivMLmods/MiniMax-H3-Rough-2D-Cartoon-Illustration \
  fc40d010a2b44fd4caa4b750b6f503eca5d61577 \
  minimax-h3-rough-2d-cartoon-illustration-1200.safetensors \
  "$comfy/models/loras/minimax-h3-rough-2d-cartoon-illustration-1200.safetensors" \
  8d49fe5abad6195f321fc7d741cc024e006ff891a40c67844834866bcdcf9e59

# H3 style, combat, and face-detail adapters. Keep all four API adapter modes available on a
# fresh install; combat requires both the action LoRA and its spatial-physics companion.
fetch_checked SOLRICKS/3D-Animation-Style-MiniMax-H3 \
  c06b8d8d44cb6ba0e5224549b3d243d1728f7019 \
  3dsrx_minimax_h3_rank16-step00001250.safetensors \
  "$comfy/models/loras/3dsrx_minimax_h3_rank16-step00001250.safetensors" \
  28b56d2240990436fba8d9a1fa06310c971a7ef95d3046059118445e0c0a4ef0
fetch_checked Jojocodex/minimax-h3-wushu-action-lora \
  2fcd16cf77d1af1db41ea4e8b782a493a6ea87c8 \
  old/wushu_action_h3_lora_v5_3000_fl2va_no-adaln.safetensors \
  "$comfy/models/loras/wushu_action_h3_lora_v5_3000_pruned.safetensors" \
  136c16924f7413f0edb1c439f29c1e4c9da13d2cf07117c4e46556691193aa16
fetch_checked Jojocodex/minimax-h3-spatial-physics-lora \
  2e1fcd55fe3c1ce1b02ede2c0e2279ee722fd632 \
  wushu_spatial_physics_clean_3000_pruned.safetensors \
  "$comfy/models/loras/wushu_spatial_physics_clean_3000_pruned.safetensors" \
  7d14f3701560068e7004159c8b2a7278bd2dbfc9e5e3b60d0bc9aef6c049919d
fetch_checked Jojocodex/minimax-h3-Camera-Motion-lora \
  fac88d6196ca84f3cee014ec79c71dc73572ab0a \
  camera_motion_h3_lora_v1_3000_pruned.safetensors \
  "$comfy/models/loras/camera_motion_h3_lora_v1_3000_pruned.safetensors" \
  9d7d98d7377f56efed3aa7d507f767936112d208906f49908ec9a8ae912be88b
fetch_checked prithivMLmods/MiniMax-H3-Facial-Realism-CloseUp \
  c89656bd0cb71873133dd2c7a925f306a21c633c \
  minimax-h3-facial-realism-closeup-cp2000.safetensors \
  "$comfy/models/loras/minimax-h3-facial-realism-closeup-cp2000.safetensors" \
  df2e588dee147f58d6344910ec758c50d2696761afe2294a0887235336a3d83a
fetch_checked fal/MiniMax-H3-Realism-People-LoRA \
  b6e6b9f683c80c38684dbb1adcd566fc69a711fb \
  h3-realism-people-t2v-i2v-r2v.safetensors \
  "$comfy/models/loras/h3-realism-people-t2v-i2v-r2v.safetensors" \
  acc529601d2da117fb81179e76c56e488a3beab1171659d305f04fa3655b787e

# The experimental adapter is advertised by capabilities; keep its exact artifact
# reproducible instead of relying on a one-off download on the reference server.
fetch_checked akatz-ai/MiniMax-H3-Character-Swap-LoRA \
  62407e0cc8089c363abd9ce4b0b27662abb237af \
  h3_character_swap_pro4500_1000.safetensors \
  "$comfy/models/loras/h3_character_swap_pro4500_1000.safetensors" \
  4b2a3f420ae804c0aa3422761ff84dbd1bf52eef6900ffab6d2e66df63cb4e79

# V1.5 (17.10.2026, upstream "recommended", strength 1.0): less drift back to the source look on
# the author's shots. Installed for A/B against V1; the Studio default stays V1 until a render wins.
fetch_checked akatz-ai/MiniMax-H3-Character-Swap-LoRA \
  96ea3407b230b6f7df71c58c70e31df96bd7e533 \
  h3_character_swap_v1.5.safetensors \
  "$comfy/models/loras/h3_character_swap_v1.5.safetensors" \
  8f2c3c3eba161df8d0c6099598b4730f641f5ce01e04cd16d2427cff8a35218f

# X2 Detail VAE for the optional A/B decoder (ab_vae=x2detail); base decode stays the default.
fetch_checked speach1sdef178/MiniMax-H3-X2-Detail-VAE \
  af8c92d267c6849fec5032c35a65d5766737b338 \
  MiniMax-H3-X2-Detail-v1.safetensors \
  "$comfy/models/vae/MiniMax-H3-X2-Detail-v1.safetensors" \
  2296840f4acedcaa976688e7d7b97f7bf570b136e400385d3f46224011897aac

# Required only for X2 VAE decoding; the separate optional reference-image detail enhancer is omitted.
x2_decoder_repo="$comfy/custom_nodes/ComfyUI-MiniMaxH3-LatentUpscaler"
x2_decoder_commit=2e568dfe3e4f5e81da178bc845e05dcfbb64d55b
pinned_clone() {
  if [[ ! -e "$2" ]]; then
    git clone "$1" "$2"
    git -C "$2" checkout --detach "$3"
  fi
  [[ "$(git -C "$2" rev-parse HEAD)" == "$3" ]] || {
    echo "Existing node pack differs from pinned commit: $2" >&2; exit 3;
  }
}
if ! h3_image_build; then
  pinned_clone https://github.com/TripleHeadedMonkey/ComfyUI-MiniMaxH3_LatentUpscaler.git \
    "$x2_decoder_repo" "$x2_decoder_commit"
  [[ -z "$(git -C "$x2_decoder_repo" status --porcelain)" ]] || {
    echo "X2 decoder node checkout is dirty: $x2_decoder_repo" >&2; exit 3;
  }
else
  h3_image_record_weight "$x2_decoder_repo" - "$x2_decoder_commit" \
    https://github.com/TripleHeadedMonkey/ComfyUI-MiniMaxH3_LatentUpscaler.git - -
fi

# Official MiniMax H3 prompt embeddings (job setting camera_effect, app/camera_effects.py), ~10.7 MB in
# total; approved for the image on 2026-10-05. Set H3_CAMERA_EFFECTS=0 to omit them.
if [[ "${H3_CAMERA_EFFECTS:-1}" == 1 ]]; then
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_art_is_explosion.safetensors \
    "$comfy/models/embeddings/minimaxh3_art_is_explosion.safetensors" \
    00e8679c77ce1fc3fd3accb67bc4db56d626b6b27609797504ab00e2b997145f
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_blooming_flowers.safetensors \
    "$comfy/models/embeddings/minimaxh3_blooming_flowers.safetensors" \
    934d1d2c50bf27cd0b3785b0f44b0b62119a90b2901f9193782d86ff7191cbe9
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_bullet_time.safetensors \
    "$comfy/models/embeddings/minimaxh3_bullet_time.safetensors" \
    e318b200d28c22fcf41d9b25ce481dd5a766f0ac50561663e33401975ea1bcae
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_dark_magic.safetensors \
    "$comfy/models/embeddings/minimaxh3_dark_magic.safetensors" \
    71df9fe76f9cbb34a7508fc49d46bf35ee51744aaa17789c6406e1bec49f5866
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_fire_breath.safetensors \
    "$comfy/models/embeddings/minimaxh3_fire_breath.safetensors" \
    ed048cf52802774a819e916bbf6ad6eaa047027abc6cd0decc63daccbb93c9f1
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_four_seasons.safetensors \
    "$comfy/models/embeddings/minimaxh3_four_seasons.safetensors" \
    9a432e89afc942d1ea27055372a6fb81c4dc0f4c0498a49b9f5eff1a015f1034
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_kiss_camera.safetensors \
    "$comfy/models/embeddings/minimaxh3_kiss_camera.safetensors" \
    9da82f56c26f2a81b80803a31486d31036c122112203b62d4991759a90ddf4b8
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_spiral_ascent.safetensors \
    "$comfy/models/embeddings/minimaxh3_spiral_ascent.safetensors" \
    767b6bdccee4c41805802a07bf3c04ba9582fbb8e7bc4bd416ff8e000e3e29b9
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_storm_magic.safetensors \
    "$comfy/models/embeddings/minimaxh3_storm_magic.safetensors" \
    5047cd4499207ef20d62872c7153a476e216e7edabd12b595802f567666d7425
  fetch_checked Comfy-Org/MiniMax-H3 \
    e5eb578a89295337b8ff433a035929ce0279e0b6 \
    embeddings/minimaxh3_truman_show.safetensors \
    "$comfy/models/embeddings/minimaxh3_truman_show.safetensors" \
    3830e6ab9d7b95965f5497a6691bd92e33f8bf7a62bafb7b2331b268ff98aa00
fi

# Keep the multimodal prompt director local and serialize it with H3 by sleeping
# vLLM between requests. Its install smoke check leaves Qwen weights asleep.
if [[ "${H3_SKIP_QWEN_DIRECTOR:-1}" != 1 ]]; then
  H3_ROOT="$root" bash "$bundle/scripts/install_h3_qwen_director.sh"
else
  echo "Qwen Director 8B отключён: кэш не скачивается"
  sed -i "s/^autostart=true/autostart=false/" /etc/supervisor/conf.d/h3-qwen-director.conf
  supervisorctl stop h3-qwen-director >/dev/null 2>&1 || true
fi
# Scenarist profiles: tools for switching without reinstall, then the optional 27B model
# (own venv with vLLM 0.19.1 + pinned snapshot). Default stays the 8B above until switched.
mkdir -p "$root/scripts" "$root/deploy/scenarist-models" "$root/vendor/minimax-h3-local-studio/scripts"
install -m 0755 "$bundle/scripts/h3_scenarist_switch.sh" "$bundle/scripts/h3_scenarist_conf.sh" \
  "$bundle/scripts/install_h3_scenarist_next.sh" "$root/scripts/"
install -m 0644 "$bundle/deploy/h3-qwen-director.conf" "$root/deploy/h3-qwen-director.conf"
install -m 0644 "$bundle"/deploy/scenarist-models/*.sha256 "$root/deploy/scenarist-models/"
install -m 0644 "$bundle/vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh" \
  "$root/vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh"
if [[ "$scenarist_27b" == 1 ]]; then
  H3_ROOT="$root" bash "$bundle/scripts/install_h3_scenarist_next.sh"
fi

cache_repo="$comfy/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache"
cache_commit=f7a27128e73e1859f2295e64698164203799029d
if [[ ! -e "$cache_repo" ]]; then
  git clone https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache.git "$cache_repo"
  git -C "$cache_repo" checkout --detach "$cache_commit"
else
  [[ "$(git -C "$cache_repo" rev-parse HEAD)" == "$cache_commit" ]] || {
    echo "Existing cache node differs from pinned commit: $cache_repo" >&2; exit 3;
  }
fi

python3 "$bundle/scripts/install_cache_metrics.py" "$cache_repo"

camera_repo="$comfy/custom_nodes/h3-3D-camera-guide"
camera_commit=84609999d2a8d5d9caa064515c188aaeef5aca48
if [[ ! -e "$camera_repo" ]]; then
  git clone https://github.com/sepiablue-ai/h3-3D-camera-guide.git "$camera_repo"
  git -C "$camera_repo" checkout --detach "$camera_commit"
else
  [[ "$(git -C "$camera_repo" rev-parse HEAD)" == "$camera_commit" ]] || {
    echo "Existing camera node differs from pinned commit: $camera_repo" >&2; exit 3;
  }
fi
# The pinned upstream camera node supplies trajectory rendering. The bundled,
# tested extension adds version-2 lens/aperture/focus fields, the optical
# H3OperatorCamera node, and the deterministic H3 prompt compiler. The pinned
# upstream __init__.py imports NODE_CLASS_MAPPINGS from nodes.py, so replacing
# nodes.py registers H3OperatorCamera without editing upstream registration.
mkdir -p "$backup/camera-guide"
for camera_file in camera.py camera_prompt.py nodes.py renderer.py; do
  [[ ! -f "$camera_repo/$camera_file" ]] || \
    cp -a "$camera_repo/$camera_file" "$backup/camera-guide/$camera_file"
done
install -m 0644 "$bundle/app/camera_guide_vendor/camera.py" "$camera_repo/camera.py"
install -m 0644 "$bundle/app/camera_guide_vendor/camera_prompt.py" "$camera_repo/camera_prompt.py"
install -m 0644 "$bundle/app/camera_guide_vendor/nodes.py" "$camera_repo/nodes.py"
install -m 0644 "$bundle/app/camera_guide_vendor/renderer.py" "$camera_repo/renderer.py"
"$root/venv/bin/python" -m compileall -q "$camera_repo/camera.py" "$camera_repo/camera_prompt.py" \
  "$camera_repo/nodes.py" "$camera_repo/renderer.py"

# Ready-made node packs, pinned like the cache/camera nodes (pip deps: comfy-extra-nodes-requirements.txt,
# installed below after the venv is isolated). KJNodes: ColorMatch (+ MiniMax H3 nodes for later A/B);
# VideoHelperSuite: video IO and frame select; Frame-Interpolation: RIFE 4.x for optional 24→48/60 fps.
kjnodes_repo="$comfy/custom_nodes/ComfyUI-KJNodes"
kjnodes_commit=d3cfe21625e5170126ce06fbfcfe1d88108688c3
pinned_clone https://github.com/kijai/ComfyUI-KJNodes.git "$kjnodes_repo" "$kjnodes_commit"
vhs_repo="$comfy/custom_nodes/ComfyUI-VideoHelperSuite"
vhs_commit=4d907bee61e92c2e65af3bd6383a4e4d356126d1
pinned_clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git "$vhs_repo" "$vhs_commit"
vfi_repo="$comfy/custom_nodes/ComfyUI-Frame-Interpolation"
vfi_commit=26545cc2dd95bc3d27f056016300673bdeee78f5
pinned_clone https://github.com/Fannovel16/ComfyUI-Frame-Interpolation.git "$vfi_repo" "$vfi_commit"
# RIFE 4.17, pre-fetched so a render never downloads from a mirror. Not rife426.pth: the release asset
# carries extra teacher.*/caltime.* tensors and fails the node's strict load_state_dict at this commit
# (checked on the local stand); 4.17 loads strictly and runs. Path = the node's own cache (config.yaml
# ckpts_path, gitignored). Source: the pack's GitHub release "models" (asset 380422504); the digest
# matches GitHub's published asset digest.
rife_ckpt="$vfi_repo/ckpts/rife/rife417.pth"
rife_sha256=f6b1561354dda3b31606190cba1d1009ea001558c1b9c9cef2d03939bb381ee5
mkdir -p "$(dirname "$rife_ckpt")"
if h3_image_build; then
  h3_image_record_weight "$rife_ckpt" - "$rife_sha256" \
    https://github.com/Fannovel16/ComfyUI-Frame-Interpolation/releases/download/models/rife417.pth - -
else
if [[ ! -f "$rife_ckpt" ]]; then
  curl -fL --retry 8 --retry-delay 5 -o "$rife_ckpt.part" \
    https://github.com/Fannovel16/ComfyUI-Frame-Interpolation/releases/download/models/rife417.pth
  mv "$rife_ckpt.part" "$rife_ckpt"
fi
echo "$rife_sha256  $rife_ckpt" | sha256sum -c --status || {
  echo "SHA-256 mismatch: $rife_ckpt" >&2; rm -f "$rife_ckpt"; exit 3;
}
fi

# H3ColorGrade: real color control for Studio «Цвет» presets with a reference photo.
color_node="$comfy/custom_nodes/h3-color-grade"
if [[ -d "$color_node" ]]; then
  mkdir -p "$backup/h3-color-grade" && cp -a "$color_node/." "$backup/h3-color-grade/"
fi
mkdir -p "$color_node"
install -m 0644 "$bundle/app/comfy_nodes/h3_color_grade/__init__.py" "$color_node/__init__.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_color_grade/grade.py" "$color_node/grade.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_color_grade/film.py" "$color_node/film.py"
"$root/venv/bin/python" -m compileall -q "$color_node"

# Same Studio integration files on the CPU stand and GPU: no models or service restarts here.
"$root/api-venv/bin/python" "$bundle/scripts/install_h3_studio_extensions.py" \
  --source "$bundle" --comfy "$comfy" --backup "$backup/studio-extensions" --apply

# «Продолжение» A/B anchor: the previous clip's tail copied into the latent under the denoise mask.
anchor_node="$comfy/custom_nodes/h3-continuation-anchor"
if [[ -d "$anchor_node" ]]; then
  mkdir -p "$backup/h3-continuation-anchor" && cp -a "$anchor_node/." "$backup/h3-continuation-anchor/"
fi
mkdir -p "$anchor_node"
install -m 0644 "$bundle/app/comfy_nodes/h3_continuation_anchor/__init__.py" "$anchor_node/__init__.py"

# Masked hero swap: only the tracked source performers are redrawn (app/swap_mask.py; A/B route).
swap_mask_node="$comfy/custom_nodes/h3-swap-mask"
if [[ -d "$swap_mask_node" ]]; then
  mkdir -p "$backup/h3-swap-mask" && cp -a "$swap_mask_node/." "$backup/h3-swap-mask/"
fi
mkdir -p "$swap_mask_node"
install -m 0644 "$bundle/app/comfy_nodes/h3_swap_mask/__init__.py" "$swap_mask_node/__init__.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_swap_mask/latent_mask.py" "$swap_mask_node/latent_mask.py"
"$root/venv/bin/python" -m compileall -q "$anchor_node"

# Research probes (docs/reference/H3_ENCODER_RESEARCH.md): read-only, used only by graphs that add them.
probe_node="$comfy/custom_nodes/h3-probe"
mkdir -p "$probe_node"
install -m 0644 "$bundle/app/comfy_nodes/h3_probe/__init__.py" "$probe_node/__init__.py"
"$root/venv/bin/python" -m compileall -q "$probe_node"

# Whole-character tracking/refinement; full BF16 H3 remains the generator.
refine_node="$comfy/custom_nodes/h3-character-refine"
if [[ -d "$refine_node" ]]; then
  mkdir -p "$backup/h3-character-refine" && cp -a "$refine_node/." "$backup/h3-character-refine/"
fi
mkdir -p "$refine_node"
install -m 0644 "$bundle/app/comfy_nodes/h3_character_refine/__init__.py" "$refine_node/__init__.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_character_refine/tracking.py" "$refine_node/tracking.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_character_refine/sam_tracking.py" "$refine_node/sam_tracking.py"
install -m 0644 "$bundle/app/comfy_nodes/h3_character_refine/masks.py" "$refine_node/masks.py"
# Avoid co-installing competing cv2 wheels or silently changing NumPy/Torch.
"$root/venv/bin/python" - <<'PYREF'
import importlib.metadata as m
conflicts = [n for n in ("opencv-python", "opencv-python-headless", "opencv-contrib-python") if next(m.distributions(name=n), None)]
if conflicts:
    raise SystemExit("Conflicting cv2 wheels: " + ", ".join(conflicts) + "; resolve before installing refinement")
PYREF
"$root/venv/bin/python" -m pip install --no-deps 'opencv-contrib-python-headless==4.13.0.92'
"$root/venv/bin/python" -c 'import cv2; cv2.dilate; cv2.GaussianBlur'
sam2_repo="$root/vendor/sam2"
sam2_commit="2b90b9f5ceec907a1c18123530e92e794ad901a4"
mkdir -p "$root/vendor"
if [[ ! -d "$sam2_repo" ]]; then
  git clone https://github.com/facebookresearch/sam2.git "$sam2_repo"
  git -C "$sam2_repo" checkout --detach "$sam2_commit"
fi
[[ "$(git -C "$sam2_repo" rev-parse HEAD)" == "$sam2_commit" ]] || {
  echo "Existing SAM2 checkout differs from pinned revision; refusing overwrite" >&2; exit 3;
}
[[ -z "$(git -C "$sam2_repo" status --porcelain)" ]] || {
  echo "SAM2 checkout is dirty; refusing install" >&2; exit 3;
}
SAM2_BUILD_CUDA=0 "$root/venv/bin/python" -m pip install --no-deps --no-build-isolation "$sam2_repo"
comfy_pins="$bundle/vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt"
"$root/venv/bin/python" -m pip install 'hydra-core==1.3.2' 'iopath==0.1.10' -c "$comfy_pins"
# The ComfyUI venv no longer sees the base image's system Python (see h3_gpu_env.sh).
h3_comfy_venv_isolate "$root/venv" "$comfy_pins" cv2 sam2 hydra iopath || exit 3
# Node-pack deps only after isolation (otherwise pip would accept the system copies), --no-deps with a
# complete pinned list: torch/numpy/cv2 stay exactly as pinned above.
"$root/venv/bin/python" -m pip install --disable-pip-version-check --no-deps \
  -r "$bundle/vendor/minimax-h3-local-studio/assets/comfy-extra-nodes-requirements.txt"
# scenedetect declares opencv-python by distribution name; the pinned contrib-headless
# wheel supplies the same cv2 API without installing competing OpenCV wheels.
"$root/venv/bin/python" - <<'PIPCHECK'
import subprocess, sys
result = subprocess.run([sys.executable, '-m', 'pip', 'check'], capture_output=True, text=True)
known = 'scenedetect 0.7.2 requires opencv-python, which is not installed.'
problems = [line for line in result.stdout.splitlines() if line and line != known and line != 'No broken requirements found.']
if problems or result.stderr.strip():
    print('\n'.join(problems), file=sys.stderr)
    print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode or 1)
if known in result.stdout:
    print('scenedetect: cv2 supplied by pinned opencv-contrib-python-headless')
PIPCHECK
(cd / && "$root/venv/bin/python" -c 'import color_matcher, matplotlib, imageio, cv2, einops, kornia, psutil')
# Fail closed: downloads use the operator's private HF configuration, never anonymous.
h3_image_build || [[ -f /root/.cache/h3-hf-curl.conf && "$(stat -c %a /root/.cache/h3-hf-curl.conf)" == 600 ]] || {
  echo "SAM2 download requires private mode-600 HF curl config" >&2; exit 3;
}
fetch_checked facebook/sam2.1-hiera-large main sam2.1_hiera_large.pt \
  "$comfy/models/sam2/sam2.1_hiera_large.pt" \
  2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318
h3_image_build || [[ "$(stat -c %s "$comfy/models/sam2/sam2.1_hiera_large.pt")" == 898083611 ]] || exit 3
"$root/venv/bin/python" -m compileall -q "$refine_node"

cat > /etc/supervisor/conf.d/h3-api.conf <<EOF
[program:h3-api]
directory=$root/api
command=$root/api-venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18189
environment=H3_JOB_REGISTRY_PATH="$root/api-data/job-registry.sqlite3",H3_CONTEXT_REGISTRY_PATH="$root/api-data/context-ir.sqlite3",H3_AUDIO_JOBS_DIR="$root/api-data/audio",H3_COSYVOICE_REPO="$root/audio/CosyVoice",H3_COSYVOICE_MODEL="$root/audio/CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B",H3_COSYVOICE_PYTHON="$root/audio/cosy-venv/bin/python",H3_COSYVOICE_DEFAULT_VOICE="$root/audio/CosyVoice/asset/zero_shot_prompt.wav",H3_COSYVOICE_SMOKE_WAV="$root/audio/cosyvoice-smoke.wav",H3_ACESTEP_REPO="$root/audio/ACE-Step-1.5",H3_ACESTEP_PYTHON="$root/audio/ACE-Step-1.5/.venv/bin/python",H3_ACESTEP_CHECKPOINTS="$root/audio/ace-checkpoints",H3_ACESTEP_SMOKE_WAV="$root/audio/acestep-smoke.wav",H3_CACHE_MODE="safe",H3_ENABLED_STYLE_ADAPTERS="style3d,cartoon2d,combat,realism_people,character_swap,character_swap_v15",H3_QWEN_AUTOMANAGE="${H3_QWEN_AUTOMANAGE:-0}"
autostart=true
; A web server never exits on purpose: restart on any exit (also a clean exit 0 after a stray
; SIGTERM). Job state lives in ComfyUI + the registry, so a restart loses no render.
autorestart=true
startsecs=5
startretries=10
; uvicorn finishes in-flight requests (uploads, Director plan replies) before SIGKILL.
stopwaitsecs=30
stopasgroup=true
killasgroup=true
$(h3_supervisor_log_lines h3-api)
EOF

H3_ROOT="$root" H3_IMAGE_BUILD=1 bash "$bundle/scripts/install_h3_server_queue.sh"

sed "s|/workspace/h3|$root|g" "$bundle/deploy/h3-qwen-vram-guard.conf" > /etc/supervisor/conf.d/h3-qwen-vram-guard.conf

mkdir -p "$root/scripts"
install -m 0755 "$bundle/scripts/install_h3_audio_cosyvoice.sh" "$root/scripts/install_h3_audio_cosyvoice.sh"
install -m 0755 "$bundle/scripts/install_h3_audio_acestep.sh" "$root/scripts/install_h3_audio_acestep.sh"
install -m 0755 "$bundle/scripts/h3_runtime_watchdog.py" "$root/scripts/h3_runtime_watchdog.py"
install -m 0644 "$bundle/deploy/h3-watchdog.conf" /etc/supervisor/conf.d/h3-watchdog.conf
# Page-cache prewarm of the H3 weights after every boot (first job would stream them from disk).
install -m 0755 "$bundle/scripts/h3_prewarm.sh" "$root/scripts/h3_prewarm.sh"
install -m 0644 "$bundle/deploy/h3-prewarm.conf" /etc/supervisor/conf.d/h3-prewarm.conf

if h3_image_build; then
  # The required-node check (/object_info) runs on the built tree in infra/h3_image_build.sh.
  echo "Image build: configs written; no supervisor restart, no service checks (server does them)."
  exit 0
fi
# `update` restarts changed programs and the restart below kills any render in progress.
if [[ "${H3_FORCE:-0}" != 1 ]] && ! h3_comfy_queue_empty; then
  echo "ComfyUI queue is busy: configs are written, rerun when idle to apply (or H3_FORCE=1)" >&2
  exit 7
fi
supervisorctl reread
supervisorctl update
supervisorctl restart h3-comfyui
for _ in $(seq 1 120); do
  if curl -fsS --max-time 3 http://127.0.0.1:18188/system_stats >/dev/null &&
     curl -fsS --max-time 3 http://127.0.0.1:18189/api/health >/dev/null; then
    break
  fi
  sleep 2
done
python3 - <<'PY'
import json, urllib.request
base = "http://127.0.0.1:18188"
with urllib.request.urlopen(base + "/object_info", timeout=60) as response:
    nodes = json.load(response)
required = {
    "MiniMaxH3ReferenceToVideo", "LoraLoaderModelOnly", "ApplyMiniMaxH3FirstBlockCache",
    "H3CameraGuide", "H3OperatorCamera", "H3CameraPrompt", "MiniMaxH3AddGuide", "H3ColorGrade", "H3FilmLook", "H3CharacterTrack", "H3CharacterCrop", "H3CharacterComposite", "H3CharacterMask",
    # «Продолжение» (app/continuation.py), core ComfyUI nodes.
    "ImageFromBatch", "TrimAudioDuration", "LoadVideo", "GetVideoComponents",
    "ColorMatch", "VHS_LoadVideo", "VHS_VideoCombine", "VHS_SelectImages", "RIFE VFI",
}
missing = sorted(required - nodes.keys())
if missing:
    raise SystemExit("Missing ComfyUI nodes: " + ", ".join(missing))
print("GPU API, ComfyUI nodes and model downloads checked.")
PY
if [[ "$active_scenarist" != default ]]; then
  # A failed switch restores the default 8B by itself; the overlay result stays usable.
  H3_ROOT="$root" bash "$root/scripts/h3_scenarist_switch.sh" --profile "$active_scenarist" || \
    echo "WARNING: scenarist profile $active_scenarist did not come back; default 8B active" >&2
fi
echo "Rollback backup: $backup"
echo "Default: full BF16 H3; local Qwen Director (scenarist profile $active_scenarist); pinned H3 camera guide; FirstBlockCache safe. Visual quality requires a real reviewed render."
