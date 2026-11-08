# shellcheck shell=bash
# Shared, host-agnostic helpers for H3 GPU deployment scripts.
# Source this file; it defines functions only and changes nothing by itself.
#
# Known-good build (verified 2026-09-30 on H100 NVL, driver 570 / CUDA 12.8):
#   ComfyUI venv: torch 2.11.0+cu128, torchvision 0.26.0+cu128
#   Qwen Director venv: vllm 0.15.0 (torch backend cu128)
# CUDA 12.8 wheels run on any driver that supports CUDA >= 12.8 (forward compatible).

H3_TORCH_INDEX="${H3_TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
# shellcheck disable=SC2034  # used by scripts that source this file
H3_TORCH_PINS=("torch==2.11.0+cu128" "torchvision==0.26.0+cu128")
H3_VLLM_PIN="${H3_VLLM_PIN:-vllm==0.15.0}"
H3_VLLM_TORCH_BACKEND="${H3_VLLM_TORCH_BACKEND:-cu128}"
H3_MIN_DRIVER_CUDA="${H3_MIN_DRIVER_CUDA:-12.8}"

# Offline image build (infra/h3_image_build.sh): H3_IMAGE_BUILD=1 runs the regular installers on a
# machine without GPU and without weights, to produce the software image. In this mode they skip
# GPU/driver checks (the torch version/CUDA-build assertions stay), download no weights but record
# what they would fetch (h3_image_record_weight), and neither start nor restart supervisor programs.
# Unset (the default on every server) changes nothing.
h3_image_build() {
  [[ "${H3_IMAGE_BUILD:-0}" == 1 ]]
}

# Records one weight the installer would download, as a weights.tsv line (infra/h3_snapshot_spec.sh):
#   h3_image_record_weight DEST SIZE SHA256 SOURCE REVISION SOURCE_PATH
# DEST is absolute (under $H3_ROOT) or relative to it; unknown SIZE/SHA256 = "-" (filled by
# infra/h3_image_weights.py from the Hugging Face API). SOURCE = HF repo id or a download URL.
h3_image_record_weight() {
  local dest="$1" root="${H3_ROOT:-/workspace/h3}"
  dest="${dest#"$root"/}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$dest" "$2" "$3" "$4" "$5" "$6" >> "${H3_IMAGE_WEIGHTS:-/tmp/h3-image-weights.tsv}"
  printf 'Image build: would fetch %s (%s@%s:%s), not downloaded\n' "$dest" "$4" "$5" "$6"
}

# Prints the highest CUDA version supported by the installed NVIDIA driver, e.g. 12.8.
h3_driver_cuda() {
  local version=''
  if command -v nvidia-smi >/dev/null 2>&1; then
    version="$(nvidia-smi 2>/dev/null | sed -n 's/.*CUDA Version: *\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n1)"
  fi
  if [[ -z "$version" ]] && command -v vast-capabilities >/dev/null 2>&1; then
    version="$(vast-capabilities 2>/dev/null | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["hardware"]["gpu"]["cuda"].get("driver_max_cuda", ""))' \
      2>/dev/null || true)"
  fi
  printf '%s\n' "$version"
}

# Numeric comparison of dotted versions: h3_version_ge 12.9 12.8 -> success.
h3_version_ge() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# Stops early with a clear message instead of failing later with PTX/libcudart errors.
h3_require_driver_cuda() {
  local have
  if h3_image_build; then
    printf 'Image build: no GPU here, driver check skipped (server needs CUDA >= %s)\n' "$H3_MIN_DRIVER_CUDA"
    return 0
  fi
  have="$(h3_driver_cuda)"
  if [[ -z "$have" ]]; then
    printf 'Cannot detect the NVIDIA driver CUDA version (nvidia-smi missing?).\n' >&2
    return 2
  fi
  if ! h3_version_ge "$have" "$H3_MIN_DRIVER_CUDA"; then
    printf 'NVIDIA driver supports CUDA %s; the pinned H3 build needs >= %s. Choose another host.\n' \
      "$have" "$H3_MIN_DRIVER_CUDA" >&2
    return 2
  fi
  printf 'Driver CUDA %s (>= %s): using pinned cu128 wheels\n' "$have" "$H3_MIN_DRIVER_CUDA"
}

# Vast images ship supervisord plus /opt/supervisor-scripts. Elsewhere install
# supervisor and keep our launch scripts under $H3_ROOT/bin.
h3_ensure_supervisor() {
  if ! command -v supervisorctl >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update -qq
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq supervisor
    else
      printf 'supervisor is required and apt-get is unavailable\n' >&2
      return 2
    fi
  fi
  mkdir -p /etc/supervisor/conf.d
  # Image build: only the config files go into the image; supervisord starts on the server.
  if h3_image_build; then return 0; fi
  # supervisorctl exits 3 when some programs are not RUNNING: supervisord still answers.
  local code=0
  supervisorctl status >/dev/null 2>&1 || code=$?
  if [[ "$code" != 0 && "$code" != 3 ]]; then
    if command -v service >/dev/null 2>&1 && service supervisor start >/dev/null 2>&1; then
      :
    elif [[ -f /etc/supervisor/supervisord.conf ]]; then
      supervisord -c /etc/supervisor/supervisord.conf
    else
      printf 'Cannot start supervisord on this host\n' >&2
      return 2
    fi
    sleep 2
  fi
}

# Directory for generated launch scripts and the log target for supervisor programs.
h3_launcher_dir() {
  if [[ -d /opt/supervisor-scripts ]]; then
    printf '%s\n' /opt/supervisor-scripts
  else
    printf '%s\n' "${H3_ROOT:-/workspace/h3}/bin"
  fi
}

h3_supervisor_log_lines() {
  local name="$1"
  # /dev/stdout only inside a container (Vast). A restored snapshot brings /opt/supervisor-scripts
  # onto plain VMs too (Nebius), where systemd's supervisord fails on /dev/stdout with ENXIO.
  if [[ -d /opt/supervisor-scripts && -f /.dockerenv ]]; then
    printf 'stdout_logfile=/dev/stdout\nredirect_stderr=true\nstdout_logfile_maxbytes=0\n'
  else
    printf 'stdout_logfile=/var/log/%s.log\nredirect_stderr=true\nstdout_logfile_maxbytes=20971520\nstdout_logfile_backups=3\n' "$name"
  fi
}

# Restart only when ComfyUI has nothing running or pending.
h3_comfy_queue_empty() {
  local url="${1:-http://127.0.0.1:18188}"
  python3 - "$url" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + "/queue", timeout=10) as response:
        queue = json.load(response)
except Exception:
    sys.exit(0)  # ComfyUI is not running: nothing can be interrupted
sys.exit(1 if queue.get("queue_running") or queue.get("queue_pending") else 0)
PY
}

# The ComfyUI venv must not see the base image's system Python (vastai/base-image is a floating tag:
# a newer one would silently change numpy/pillow/requests/... under a restored snapshot).
# Installs the pinned former system packages, turns include-system-site-packages off in an existing
# venv (servers deployed before 2026-10-04 have it on), then proves the venv still imports what
# ComfyUI and our custom nodes need. On failure the flag is restored and the caller stops (exit 3):
# the server keeps working exactly as before.
#   h3_comfy_venv_isolate VENV PINS_FILE [extra modules to import...]
h3_comfy_venv_isolate() {
  local venv="$1" pins="$2"; shift 2
  local cfg="$venv/pyvenv.cfg" flipped=0
  [[ -s "$pins" ]] || { printf 'Missing ComfyUI venv pins: %s\n' "$pins" >&2; return 2; }
  # Switch the flag off BEFORE installing: otherwise pip sees the system copies as "already
  # satisfied" and installs nothing into the venv. A running ComfyUI keeps its loaded modules.
  if grep -Eq '^include-system-site-packages *= *true' "$cfg"; then
    cp -p "$cfg" "$cfg.system-site-packages.bak"
    sed -i -E 's/^include-system-site-packages *= *true/include-system-site-packages = false/' "$cfg"
    flipped=1
    printf 'ComfyUI venv: system site-packages switched off (backup %s)\n' "$cfg.system-site-packages.bak"
  fi
  "$venv/bin/python" -m pip install --disable-pip-version-check -r "$pins" || {
    [[ "$flipped" == 0 ]] || cp -p "$cfg.system-site-packages.bak" "$cfg"
    return 3
  }
  local modules="numpy PIL yaml tqdm psutil requests jinja2 torch torchvision torchsde transformers huggingface_hub safetensors einops aiohttp sqlalchemy alembic scipy.ndimage av kornia spandrel comfy_kitchen $*"
  # shellcheck disable=SC2086  # module list is intentionally word-split
  if ! "$venv/bin/python" -c 'import subprocess,sys
r=subprocess.run([sys.executable,"-m","pip","check"],capture_output=True,text=True)
known="scenedetect 0.7.2 requires opencv-python, which is not installed."
problems=[s for s in r.stdout.splitlines() if s and s != known and s != "No broken requirements found."]
if problems or r.stderr.strip():
 print("\n".join(problems),file=sys.stderr); print(r.stderr,file=sys.stderr); sys.exit(r.returncode or 1)
if known in r.stdout:
 import cv2; assert hasattr(cv2,"dilate")
 print("scenedetect cv2 dependency supplied by pinned contrib-headless wheel")' || ! (cd / && "$venv/bin/python" -c "import importlib, sys
for name in sys.argv[1:]: importlib.import_module(name)
print('ComfyUI venv self-contained: imports OK')" $modules); then
    if [[ "$flipped" == 1 ]]; then
      cp -p "$cfg.system-site-packages.bak" "$cfg"
      printf 'ComfyUI venv still needs system packages; flag restored. Add the missing pins to %s\n' "$pins" >&2
    fi
    return 3
  fi
}
