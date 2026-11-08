#!/usr/bin/env bash
# «Актёрская игра» (LivePortrait human + animals) на GPU-сервере — точная копия рабочей установки 2026-10-03.
# Запускается НА СЕРВЕРЕ после установщика H3 (нужны /workspace/h3/api и /root/.cache/h3-hf-curl.conf).
# Идемпотентен: уже скачанные веса с верным размером не перекачиваются.
set -euo pipefail
root="${H3_ROOT:-/workspace/h3}"
lp="$root/LivePortrait"
venv="$root/liveportrait-venv"
commit=9b294b3d0536135442ea73cb01e6cb3ca7029dd3
hf_conf=/root/.cache/h3-hf-curl.conf
# H3_IMAGE_BUILD=1 (infra/h3_image_build.sh): offline image build without GPU and weights — weights are
# recorded in $H3_IMAGE_WEIGHTS instead of downloaded, the GPU assertion is skipped, XPose must
# already be built (from the snapshot). Unset on servers: nothing changes.
image_build="${H3_IMAGE_BUILD:-0}"
ops="$root/LivePortrait/src/utils/dependencies/XPose/models/UniPose/ops"

command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
if [[ "$image_build" == 1 ]]; then
  ls "$ops"/MultiScaleDeformableAttention*.so >/dev/null 2>&1 || { echo "Сборка образа: XPose не собран в снимке, без GPU его не собрать" >&2; exit 3; }
else
[[ -f "$hf_conf" && "$(stat -c %a "$hf_conf")" == 600 ]] || { echo "Нет приватного $hf_conf (0600)" >&2; exit 3; }
command -v nvcc >/dev/null || { echo "Нет nvcc: модуль XPose для животных собирается из исходников (CUDA toolkit 12.8)" >&2; exit 3; }
fi

if [[ ! -d "$lp/.git" ]]; then
  git clone https://github.com/KlingAIResearch/LivePortrait.git "$lp"
fi
git -C "$lp" fetch -q --depth 1 origin "$commit" 2>/dev/null || true
git -C "$lp" checkout -q --detach "$commit"

[[ -x "$venv/bin/python" ]] || uv venv --python 3.12 "$venv"
uv pip install --python "$venv/bin/python" torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python "$venv/bin/python" numpy==1.26.4 pyyaml==6.0.1 opencv-python-headless==4.10.0.84 \
  scipy==1.13.1 imageio==2.34.2 lmdb==1.4.1 tqdm==4.66.4 rich==13.7.1 ffmpeg-python==0.2.0 onnx==1.16.1 \
  scikit-image==0.24.0 albumentations==1.4.10 matplotlib==3.9.0 imageio-ffmpeg==0.5.1 tyro==0.8.5 \
  pykalman==0.9.7 pillow==12.3.0 transformers==4.38.0 onnxruntime-gpu==1.23.2 ninja

# Веса KlingTeam/LivePortrait: размер сверяется с манифестом Hugging Face.
H3_IMAGE_BUILD="$image_build" "$venv/bin/python" - "$lp/pretrained_weights" "$hf_conf" "$root" <<'PY'
import json, os, pathlib, subprocess, sys
root, conf = pathlib.Path(sys.argv[1]), sys.argv[2]
image_build = os.environ["H3_IMAGE_BUILD"] == "1"
api = "https://huggingface.co/api/models/KlingTeam/LivePortrait/tree/main?recursive=true&limit=1000"
auth = [] if image_build else ["-K", conf]  # image build: public manifest, no token on the build box
files = {x["path"]: x["size"] for x in json.loads(subprocess.check_output(["curl", "-fsSL", *auth, api]))
         if x.get("type") == "file"}
needed = {"insightface/models/buffalo_l/2d106det.onnx", "insightface/models/buffalo_l/det_10g.onnx",
          "liveportrait/landmark.onnx", "liveportrait/retargeting_models/stitching_retargeting_module.pth",
          "liveportrait_animals/xpose.pth"}
parts = ("appearance_feature_extractor", "motion_extractor", "spade_generator", "warping_module")
needed |= {f"liveportrait/base_models/{n}.pth" for n in parts}
needed |= {f"liveportrait_animals/base_models_v1.1/{n}.pth" for n in parts}
for name in sorted(needed):
    expected = files.get(name)
    if not isinstance(expected, int) or expected < 1000:
        raise SystemExit(f"нет размера в манифесте: {name}")
    dest = root / name
    if image_build:
        rel = os.path.relpath(dest, sys.argv[3])
        with open(os.environ.get("H3_IMAGE_WEIGHTS", "/tmp/h3-image-weights.tsv"), "a") as out:
            out.write(f"{rel}\t{expected}\t-\tKlingTeam/LivePortrait\tmain\t{name}\n")
        print("Image build: would fetch", rel)
        continue
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == expected:
        continue
    part = dest.with_suffix(dest.suffix + ".part")
    subprocess.run(["curl", "-fLsS", "--retry", "3", "--connect-timeout", "15", "-K", conf, "-C", "-",
                    "-o", str(part), f"https://huggingface.co/KlingTeam/LivePortrait/resolve/main/{name}"], check=True)
    if part.stat().st_size != expected:
        raise SystemExit(f"размер не совпал: {name}")
    os.replace(part, dest)
print("LivePortrait: веса записаны в опись образа" if image_build else "LivePortrait: веса проверены")
PY

# XPose's compiled CUDA extension is tied to GPU compute capabilities. Golden images
# may contain a .so built on another card, so compile for every visible GPU architecture
# on the target host and reuse it only when its marker matches exactly.
if [[ "$image_build" != 1 ]]; then
  arch_list="$("$venv/bin/python" - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit("XPose needs a visible CUDA GPU on the target server")
caps = sorted({torch.cuda.get_device_capability(i) for i in range(torch.cuda.device_count())})
print(";".join(f"{major}.{minor}" for major, minor in caps))
PY
)"
  [[ -n "$arch_list" ]] || { echo "Не удалось определить compute capability GPU" >&2; exit 3; }
  arch_marker="$ops/.h3-xpose-arch"
  if ! ls "$ops"/MultiScaleDeformableAttention*.so >/dev/null 2>&1 \
      || [[ ! -f "$arch_marker" || "$(cat "$arch_marker")" != "$arch_list" ]]; then
    echo "Сборка XPose CUDA для compute capability: $arch_list"
    rm -rf "$ops/build"
    find "$ops" -maxdepth 1 -type f -name 'MultiScaleDeformableAttention*.so' -delete
    (cd "$ops" && TORCH_CUDA_ARCH_LIST="$arch_list" MAX_JOBS=4 "$venv/bin/python" setup.py build_ext --inplace)
    ls "$ops"/MultiScaleDeformableAttention*.so >/dev/null 2>&1 || { echo "Сборка XPose не создала CUDA-модуль" >&2; exit 3; }
    printf '%s\n' "$arch_list" > "$arch_marker.tmp"
    mv "$arch_marker.tmp" "$arch_marker"
  else
    echo "XPose CUDA уже собран для compute capability $arch_list"
  fi
fi

# Smoke: импорт обоих конвейеров в изолированном venv. Маркер включает возможность в GPU API.
(cd "$lp" && H3_IMAGE_BUILD="$image_build" "$venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, "src/utils/dependencies/XPose/models/UniPose/ops")
import torch, MultiScaleDeformableAttention  # noqa: F401
assert torch.cuda.is_available() or os.environ["H3_IMAGE_BUILD"] == "1"
from src.live_portrait_pipeline import LivePortraitPipeline  # noqa: F401
from src.live_portrait_pipeline_animal import LivePortraitPipelineAnimal  # noqa: F401
print("LivePortrait: human + animal импортируются")
PY
)
printf '%s\n' "$commit" > "$lp/.h3-acting-smoke-ok"
mkdir -p "$root/acting-cache" && chmod 700 "$root/acting-cache"
# Старые API-снимки могут не содержать маршруты. Актуальный API уже включает их;
# повторный патч main.py ломает импорт reference_video_short_edge и дублирует router.
if ! grep -q 'app.include_router(acting_router)' "$root/api/app/main.py"; then
  python3 "$root/scripts/enable_acting_remote.py"
fi
