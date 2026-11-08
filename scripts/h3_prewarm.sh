#!/usr/bin/env bash
# Read the H3 weights into the Linux page cache once after the server starts (supervisor h3-prewarm, autostart).
# Without it the first job after a boot streams ~117 GB from disk inside the sampler (Nebius disk ~110 MB/s:
# the first step took ~10 min on 05.11). Lowest IO priority, never fails the server, logs the time per file.
# Same files as the full BF16 Ref2VA profile; missing files are skipped.
set -u
models=/workspace/h3/ComfyUI/models
files=(
  "$models/text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors"
  "$models/diffusion_models/minimax_h3_ref2va_bf16.safetensors"
  "$models/vae/minimax_h3_video_vae_fp16.safetensors"
  "$models/vae/minimax_h3_audio_vae_fp32.safetensors"
)
# Network disks serve parallel reads faster than one stream: each file is read as N slices at once.
streams="${H3_PREWARM_STREAMS:-8}"
# Idle IO priority only while ComfyUI is busy, so a job that starts during the prewarm wins the disk.
io_class() {  # 3 = idle when ComfyUI is rendering; 2 = normal otherwise (also while it is still starting)
  local q; q=$(curl -fsS -m2 http://127.0.0.1:18188/queue 2>/dev/null) || { echo 2; return; }
  grep -q '"queue_running": \[\]' <<<"$q" && echo 2 || echo 3
}
started=$(date +%s)
for f in "${files[@]}"; do
  [[ -f "$f" ]] || { echo "нет $f — пропуск"; continue; }
  t0=$(date +%s)
  size=$(stat -c %s "$f")
  slice=$(( (size / streams / 16777216 + 1) ))   # 16 MiB blocks per slice
  cls=$(io_class)
  for i in $(seq 0 $((streams - 1))); do
    ionice -c "$cls" nice -n 19 dd if="$f" of=/dev/null bs=16M skip=$((i * slice)) count="$slice" status=none &
  done
  wait || echo "чтение $f прервано"
  dt=$(( $(date +%s) - t0 ))
  echo "$(basename "$f"): $(( size / 1000000000 )) ГБ за ${dt} с ($(( size / 1000000 / (dt > 0 ? dt : 1) )) МБ/с)"
done
echo "прогрев завершён за $(( $(date +%s) - started )) с; в кэше: $(free -g | awk '/Mem:/ {print $6}') ГБ"
# supervisor: a oneshot that exits 0 stays EXITED until the next boot
exit 0
