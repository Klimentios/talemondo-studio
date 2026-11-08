#!/usr/bin/env bash
# Hardware gate for the current unquantized single-GPU BF16 Studio profile.
set -euo pipefail

command -v nvidia-smi >/dev/null || {
  echo "Нужен NVIDIA GPU с CUDA; CPU/AMD/Apple Silicon для этого профиля не поддерживаются." >&2
  exit 7
}
# Plain VMs (Nebius) keep CUDA outside PATH; prefer 12.8, which XPose needs for the cu128 torch.
for cuda_bin in /usr/local/cuda-12.8/bin /usr/local/cuda/bin; do
  command -v nvcc >/dev/null || { [[ -x "$cuda_bin/nvcc" ]] && PATH="$cuda_bin:$PATH"; }
done
command -v nvcc >/dev/null || {
  echo "Нужен CUDA toolkit с nvcc (используется для сборки XPose под целевую карту)." >&2
  exit 7
}

driver_cuda="$(nvidia-smi 2>/dev/null \
  | sed -n 's/.*CUDA[^0-9]*Version: *\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -n1)"
[[ -n "$driver_cuda" ]] || {
  echo "Не удалось определить версию CUDA драйвера через nvidia-smi." >&2
  exit 7
}
[[ "$(printf '%s\n%s\n' 12.8 "$driver_cuda" | sort -V | head -n1)" == 12.8 ]] || {
  echo "Нужен драйвер с поддержкой CUDA >=12.8; обнаружено $driver_cuda." >&2
  exit 7
}
toolkit_cuda="$(nvcc --version | sed -n 's/.*release \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | tail -n1)"
[[ -n "$toolkit_cuda" ]] && [[ "$(printf '%s\n%s\n' 12.8 "$toolkit_cuda" | sort -V | head -n1)" == 12.8 ]] || {
  echo "Нужен CUDA toolkit >=12.8 с nvcc; обнаружено ${toolkit_cuda:-неизвестно}." >&2
  exit 7
}

max_vram="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits \
  | awk 'BEGIN { max=0 } { gsub(/[^0-9]/, "", $1); if ($1 > max) max=$1 } END { print max }')"
[[ "$max_vram" =~ ^[0-9]+$ ]] && (( max_vram >= 90000 )) || {
  echo "Нужна одна NVIDIA GPU с VRAM не менее 90,000 MiB для полного BF16 профиля; максимум: ${max_vram:-неизвестно} MiB." >&2
  exit 7
}

nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
echo "Совместимо: CUDA driver $driver_cuda / toolkit $toolkit_cuda, GPU VRAM ${max_vram} MiB."
