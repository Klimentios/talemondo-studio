#!/usr/bin/env bash
set -euo pipefail

# Run on the GPU host after the current API overlay is deployed. Dry run by default.
root="${H3_ROOT:-/workspace/h3}"
if [[ "${1:-}" != "--apply" && "${1:-}" != "--prepare-only" ]]; then
  echo "Dry run: CosyVoice3 0.5B, isolated Python 3.10 venv, authenticated pinned HF weights."
  echo "Run on the verified idle GPU host: bash scripts/install_h3_audio_cosyvoice.sh --apply"
  exit 0
fi

prepare_only=false
if [[ "${1:-}" == "--prepare-only" ]]; then prepare_only=true; fi
if [[ "$prepare_only" == false ]]; then
  [[ -f "$root/api/app/audio_cosyvoice_line.py" ]] || { echo "Deploy current GPU API first" >&2; exit 2; }
fi
[[ -f /root/.cache/h3-hf-curl.conf && "$(stat -c %a /root/.cache/h3-hf-curl.conf)" == 600 ]] || {
  echo "Private mode-600 HF token config is required" >&2; exit 2;
}
if command -v python3.10 >/dev/null; then
  python310="$(command -v python3.10)"
else
  command -v uv >/dev/null || { echo "CosyVoice requires Python 3.10 or uv-managed Python" >&2; exit 2; }
  uv python install 3.10
  python310="$(uv python find 3.10)"
fi
command -v nvidia-smi >/dev/null || { echo "NVIDIA GPU not found" >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "ffprobe not found" >&2; exit 2; }
[[ -f /etc/vast-agents-guide.md ]] && cat /etc/vast-agents-guide.md
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:18188/queue', timeout=10) as response:
    queue = json.load(response)
if queue.get('queue_running') or queue.get('queue_pending'):
    raise SystemExit('ComfyUI queue is active; refusing audio install')
PY
available="$(df -PB1 "$root" | awk 'NR==2 {print $4}')"
(( available >= 25000000000 )) || { echo "Need at least 25 GB free for isolated runtime and model; available $available" >&2; exit 2; }

audio="$root/audio"
repo="$audio/CosyVoice"
venv="$audio/cosy-venv"
model="$repo/pretrained_models/Fun-CosyVoice3-0.5B"
mkdir -p "$audio"
if [[ ! -d "$repo/.git" ]]; then
  git clone --recursive https://github.com/QwenAudio/CosyVoice.git "$repo"
fi
[[ -z "$(git -C "$repo" status --porcelain)" ]] || { echo "CosyVoice checkout is dirty" >&2; exit 3; }
git -C "$repo" fetch origin 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C "$repo" checkout --detach 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc
git -C "$repo" submodule update --init --recursive
[[ -x "$venv/bin/python" ]] || "$python310" -m venv "$venv"
"$venv/bin/python" -m pip install --upgrade pip
"$venv/bin/python" -m pip install 'setuptools==80.9.0' wheel
# The pinned Whisper source imports pkg_resources during build. New isolated
# setuptools releases omit it, so build this dependency in the pinned venv.
"$venv/bin/python" -m pip install --no-build-isolation --no-deps 'openai-whisper==20231117'
compute_cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n 1)"
if [[ "$compute_cap" == 12.* ]]; then
  # CosyVoice pins torch 2.3.1/CUDA 12.1, which cannot execute on Blackwell.
  # Install the official CUDA 12.8 pair before resolving the other requirements.
  "$venv/bin/python" -m pip install --no-cache-dir 'torch==2.7.1' 'torchaudio==2.7.1' \
    --index-url https://download.pytorch.org/whl/cu128
  audio_requirements="$(mktemp)"
  trap 'rm -f "$audio_requirements"' EXIT
  # CosyVoice's conformer/deepspeed dependencies name torch without a CUDA
  # constraint, so pip otherwise selects an incompatible cu121 wheel. The
  # isolated venv is fully provisioned before this stage; apply its pinned
  # direct requirements without resolving transitive dependencies again.
  grep -v -E '^(torch|torchaudio)==' "$repo/requirements.txt" > "$audio_requirements"
  "$venv/bin/python" -m pip install --no-cache-dir --no-deps -r "$audio_requirements" 'huggingface_hub==0.35.3'
else
  "$venv/bin/python" -m pip install --no-cache-dir -r "$repo/requirements.txt" 'huggingface_hub==0.35.3'
fi

# The token stays inside the Python process; never print it or place it in argv.
"$venv/bin/python" - "$model" <<'PY'
import re
import sys
from pathlib import Path
from huggingface_hub import snapshot_download
private = Path('/root/.cache/h3-hf-curl.conf')
match = re.search(r'Authorization:\s*Bearer\s+(hf_[A-Za-z0-9]+)', private.read_text())
if not match:
    raise SystemExit('HF token config does not contain a bearer token')
snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512',
                  revision='29e01c4e8d000f4bcd70751be16fa94bf3d85a18',
                  local_dir=sys.argv[1], token=match.group(1))
PY

"$venv/bin/python" - "$model" <<'PY'
import hashlib
import sys
from pathlib import Path
root = Path(sys.argv[1])
files = {
 'CosyVoice-BlankEN/model.safetensors': (988097824, '130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96'),
 'campplus.onnx': (28303423, 'a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73'),
 'flow.decoder.estimator.fp32.onnx': (1326216933, '9b51b9533a55937762b262bf2cf9c6220ce40760f76d6532cb16a6a6d84059a8'),
 'flow.pt': (1329116148, 'a6fab32a7825e5b0bc855ddd948f8db9370b0a786fbc249caa4595e95b608e4b'),
 'hift.pt': (83202622, 'b279d7641eb97ae55b3b540cfba4f953c26492a2df758328a89a4d007ab87a65'),
 'llm.pt': (2024669519, '69f43bd545131c30e98947fb360ea8b4dc9916d8e83dded7757c7ea4f5a24970'),
 'speech_tokenizer_v3.batch.onnx': (969451579, 'b156b8a7bbff436585e153f4637b9a368009005ac66efa108a6c8bfb34e5ee43'),
 'speech_tokenizer_v3.onnx': (969451503, '23236a74175dbdda47afc66dbadd5bcb41303c467a57c261cb8539ad9db9208d'),
}
for name, (size, expected) in files.items():
    path = root / name
    if not path.is_file() or path.stat().st_size != size:
        raise SystemExit(f'Incomplete model file: {name}')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise SystemExit(f'Wrong model hash: {name}')
print('CosyVoice3 model hashes verified')
PY

if [[ "$prepare_only" == true ]]; then
  echo 'CosyVoice3 runtime and weights prepared; smoke test and API activation pending.'
  exit 0
fi

"$venv/bin/python" "$root/api/app/audio_cosyvoice_line.py" \
  --repo "$repo" --model "$model" --reference "$repo/asset/zero_shot_prompt.wav" \
  --text 'Проверка русской речи.' --direction 'Speak naturally in Russian.' \
  --output "$audio/cosyvoice-smoke.wav"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$audio/cosyvoice-smoke.wav" | grep -q .

# An H3 render could start while weights download. Verify the queue again before
# updating only the API service; ComfyUI and its models are not restarted.
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:18188/queue', timeout=10) as response:
    queue = json.load(response)
if queue.get('queue_running') or queue.get('queue_pending'):
    raise SystemExit('CosyVoice installed; H3 is active, so API restart is deferred')
PY
supervisorctl restart h3-api
curl -fsS --max-time 15 http://127.0.0.1:18189/api/audio/capabilities
