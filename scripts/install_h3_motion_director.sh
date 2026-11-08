#!/usr/bin/env bash
set -euo pipefail

# Optional GPL-3.0 ComfyUI node. Kept outside the Studio source and default overlay.
revision=0e6b5913bbce44932a82fa9eed79a26c66c578d8
repo=https://github.com/j955229/ComfyUI-MiniMax-H3-Motion-Director.git
comfy="${H3_ROOT:-/workspace/h3}/ComfyUI"
target="$comfy/custom_nodes/ComfyUI-MiniMax-H3-Motion-Director"
patch="$(cd "$(dirname "$0")" && pwd)/patches/motion-director-kwargs.patch"
venv="$comfy/venv"
[[ -x "$venv/bin/python" ]] || venv="$comfy/.venv"
[[ -x "$venv/bin/python" ]] || venv="${H3_ROOT:-/workspace/h3}/venv"
# The GPU ComfyUI is reached through the Mac tunnel; the native Mac stand sets its own port.
comfy_url="${H3_COMFY_URL:-http://127.0.0.1:18188}"
# The Mac stand's venv is made by uv and has no pip module.
pip() { if "$venv/bin/python" -m pip --version >/dev/null 2>&1; then "$venv/bin/python" -m pip install "$@"; else uv pip install --python "$venv/bin/python" "$@"; fi; }

case "${1:---check}" in
  --check)
    [[ -d "$target/.git" ]] || { echo "Motion Director is not installed at $target"; exit 1; }
    actual="$(git -C "$target" rev-parse HEAD)"
    [[ "$actual" == "$revision" ]] || { echo "Expected $revision, found $actual"; exit 1; }
    # Our ComfyUI's MiniMax H3 nodes take a different positional order; the patch passes keywords.
    git -C "$target" apply --reverse --check "$patch" 2>/dev/null || { echo "Keyword-call patch is not applied"; exit 1; }
    [[ "$(git -C "$target" status --porcelain)" == " M nodes/conditioning.py" ]] || { echo "Unexpected local changes"; exit 1; }
    "$venv/bin/python" -c 'import torchaudio' || { echo "torchaudio is missing from the ComfyUI venv"; exit 1; }
    python3 "$(dirname "$0")/h3_motion_director_probe.py" "$comfy_url/object_info"
    ;;
  --apply)
    [[ -d "$comfy" && -x "$venv/bin/python" ]] || { echo "ComfyUI or its venv is missing" >&2; exit 2; }
    queue="$(curl -fsS --max-time 10 "$comfy_url/queue")"
    python3 -c 'import json,sys; q=json.loads(sys.argv[1]); sys.exit(0 if isinstance(q,dict) and isinstance(q.get("queue_running"),list) and isinstance(q.get("queue_pending"),list) and not q["queue_running"] and not q["queue_pending"] else 1)' "$queue" || {
      echo "ComfyUI queue is busy or its status is invalid; installation deferred" >&2; exit 3;
    }
    if [[ -e "$target" ]]; then
      [[ -d "$target/.git" && "$(git -C "$target" rev-parse HEAD)" == "$revision" ]] || {
        echo "Existing Motion Director checkout differs; inspect it before changing" >&2; exit 4;
      }
      if ! git -C "$target" apply --reverse --check "$patch" 2>/dev/null; then
        [[ -z "$(git -C "$target" status --porcelain)" ]] || {
          echo "Existing Motion Director checkout has local changes; inspect before continuing" >&2; exit 4;
        }
        git -C "$target" apply "$patch"
      fi
    else
      git clone --no-checkout "$repo" "$target"
      git -C "$target" fetch --depth 1 origin "$revision"
      git -C "$target" checkout --detach "$revision"
      git -C "$target" apply "$patch"
    fi
    # scenedetect depends on opencv-python, which would overwrite the venv's own cv2 build in place.
    "$venv/bin/python" -c 'import cv2' 2>/dev/null || pip 'opencv-python-headless>=4.8,<5'
    pip 'imageio-ffmpeg>=0.4,<1' 'click>=8,<9,!=8.3.0' platformdirs tqdm
    pip --no-deps 'scenedetect>=0.6.4,<0.8'
    # Audio continuity resamples with torchaudio; it must be the build of the venv's own torch.
    if ! "$venv/bin/python" -c 'import torchaudio' 2>/dev/null; then
      torch_build="$("$venv/bin/python" -c 'import torch; print(torch.__version__)')"
      if [[ "$torch_build" == *+* ]]; then
        pip --no-deps "torchaudio==$torch_build" \
          --index-url "https://download.pytorch.org/whl/${torch_build##*+}"
      else
        pip --no-deps "torchaudio==$torch_build"
      fi
    fi
    echo "Pinned node installed. Restart ComfyUI only when idle, then run --check. No render was submitted."
    ;;
  *) echo "Usage: $0 --check|--apply" >&2; exit 2 ;;
esac
