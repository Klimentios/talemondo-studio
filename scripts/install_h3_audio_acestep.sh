#!/usr/bin/env bash
set -euo pipefail

# Execute on the idle GPU host. Default invocation only prints the intended work.
root="${H3_ROOT:-/workspace/h3}"
if [[ "${1:-}" != "--apply" && "${1:-}" != "--prepare-only" ]]; then
  echo "Dry run: isolated ACE-Step 1.5 SFT music runtime, pinned code and weights."
  echo "Run after API deployment: bash scripts/install_h3_audio_acestep.sh --apply"
  exit 0
fi

prepare_only=false
if [[ "${1:-}" == "--prepare-only" ]]; then prepare_only=true; fi
if [[ "$prepare_only" == false ]]; then
  [[ -f "$root/api/app/audio_acestep_music.py" ]] || { echo "Deploy current GPU API first" >&2; exit 2; }
fi
[[ -f /root/.cache/h3-hf-curl.conf && "$(stat -c %a /root/.cache/h3-hf-curl.conf)" == 600 ]] || {
  echo "Private mode-600 HF token config is required" >&2; exit 2;
}
command -v uv >/dev/null || { echo "uv is required for isolated ACE-Step dependencies" >&2; exit 2; }
command -v nvidia-smi >/dev/null || { echo "NVIDIA GPU not found" >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "ffprobe not found" >&2; exit 2; }
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:18188/queue', timeout=10) as response:
    queue = json.load(response)
if queue.get('queue_running') or queue.get('queue_pending'):
    raise SystemExit('ComfyUI queue is active; refusing music install')
PY
available="$(df -PB1 "$root" | awk 'NR==2 {print $4}')"
(( available >= 35000000000 )) || { echo "Need at least 35 GB free; available $available" >&2; exit 2; }

music="$root/audio/ACE-Step-1.5"
checkpoints="$root/audio/ace-checkpoints"
code_rev=ca1e85fe9430179831e6bc6be790c332190a3866
main_rev=19671f406d603126926c1b7e2adc169acbcade22
sft_rev=c410d249e71ea9385a7b586865e65b1473e1098d
mkdir -p "$root/audio" "$checkpoints"
if [[ ! -d "$music/.git" ]]; then
  git clone https://github.com/ace-step/ACE-Step-1.5.git "$music"
fi
[[ -z "$(git -C "$music" status --porcelain)" ]] || { echo "ACE-Step checkout is dirty" >&2; exit 3; }
git -C "$music" fetch origin "$code_rev"
git -C "$music" checkout --detach "$code_rev"
(cd "$music" && uv sync --frozen)
# A venv restored from an image can look complete but miss its packages (2026-10-07: «No module
# named 'torch'» on every music job). Repair once, then refuse to continue if it still fails.
if ! "$music/.venv/bin/python" -c 'import torch' >/dev/null 2>&1; then
  echo "ACE-Step venv cannot import torch — reinstalling packages" >&2
  (cd "$music" && uv sync --frozen --reinstall)
  "$music/.venv/bin/python" -c 'import torch' || { echo "ACE-Step venv still cannot import torch" >&2; exit 4; }
fi

# Never place the token in shell arguments or logs.
"$music/.venv/bin/python" - "$checkpoints" "$main_rev" "$sft_rev" <<'PY'
import re
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
private = Path('/root/.cache/h3-hf-curl.conf')
match = re.search(r'Authorization:\s*Bearer\s+(hf_[A-Za-z0-9]+)', private.read_text())
if not match:
    raise SystemExit('HF token config does not contain a bearer token')
root, main_rev, sft_rev = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
snapshot_download('ACE-Step/Ace-Step1.5', revision=main_rev, local_dir=root,
                  allow_patterns=['vae/*', 'Qwen3-Embedding-0.6B/*', 'acestep-5Hz-lm-1.7B/*'],
                  token=match.group(1))
snapshot_download('ACE-Step/acestep-v15-sft', revision=sft_rev,
                  local_dir=root / 'acestep-v15-sft', token=match.group(1))
for name in ('acestep-v15-sft/model.safetensors', 'vae/diffusion_pytorch_model.safetensors',
             'Qwen3-Embedding-0.6B/model.safetensors', 'acestep-5Hz-lm-1.7B/model.safetensors'):
    path = root / name
    if not path.is_file() or path.stat().st_size < 100_000_000:
        raise SystemExit(f'Incomplete model file: {name}')
print('ACE-Step model files downloaded from pinned revisions')
PY

if [[ "$prepare_only" == true ]]; then
  echo 'ACE-Step runtime and weights prepared; smoke test and API activation pending.'
  exit 0
fi

"$music/.venv/bin/python" "$root/api/app/audio_acestep_music.py" \
  --repo "$music" --checkpoints "$checkpoints" --duration 10 \
  --prompt 'Instrumental cinematic ambient music, soft piano and strings, no vocals.' \
  --output "$root/audio/acestep-smoke.wav"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 \
  "$root/audio/acestep-smoke.wav" | grep -q .

python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:18188/queue', timeout=10) as response:
    queue = json.load(response)
if queue.get('queue_running') or queue.get('queue_pending'):
    raise SystemExit('Music installed; H3 is active, so API restart is deferred')
PY
config=/etc/supervisor/conf.d/h3-api.conf
[[ -f "$config" ]] || { echo "h3-api supervisor config not found" >&2; exit 3; }
if ! grep -q 'H3_ACESTEP_REPO=' "$config"; then
  cp -p "$config" "$config.before-acestep-$(date -u +%Y%m%dT%H%M%SZ)"
  python3 - "$config" "$root" <<'PY'
from pathlib import Path
import sys
path, root = Path(sys.argv[1]), sys.argv[2]
data = path.read_text()
lines = data.splitlines()
matches = [i for i, line in enumerate(lines) if line.startswith('environment=')]
if len(matches) != 1 or '[program:h3-api]' not in data:
    raise SystemExit('Unexpected h3-api supervisor configuration')
i = matches[0]
music = (f',H3_ACESTEP_REPO="{root}/audio/ACE-Step-1.5"'
         f',H3_ACESTEP_PYTHON="{root}/audio/ACE-Step-1.5/.venv/bin/python"'
         f',H3_ACESTEP_CHECKPOINTS="{root}/audio/ace-checkpoints"'
         f',H3_ACESTEP_SMOKE_WAV="{root}/audio/acestep-smoke.wav"')
lines[i] += music
path.write_text('\n'.join(lines) + '\n')
PY
  supervisorctl reread
  supervisorctl update h3-api
fi
supervisorctl restart h3-api
curl -fsS --max-time 15 http://127.0.0.1:18189/api/audio/capabilities
