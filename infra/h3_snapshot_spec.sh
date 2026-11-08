# Shared by infra/h3_snapshot_pull.sh (snapshot -> Mac) and infra/h3_r2_push.sh (snapshot -> R2):
# what the software archive contains and how the weights inventory is computed. Sourced, not run.

# Paths from the server root. Weights and job data are excluded; they are listed in weights.tsv.
H3_TAR_ARGS='--one-file-system
--exclude=workspace/h3/ComfyUI/models
--exclude=workspace/h3/LoRA_Library
--exclude=workspace/h3/hf-cache
--exclude=workspace/h3/audio/ace-checkpoints
--exclude=workspace/h3/audio/CosyVoice/pretrained_models
--exclude=workspace/h3/LivePortrait/pretrained_weights
--exclude=workspace/h3/input
--exclude=workspace/h3/output
--exclude=workspace/h3/api-data
--exclude=workspace/h3/acting-*
--exclude=workspace/h3/backup*
--exclude=workspace/h3/model-staging
--exclude=workspace/h3/audio-*.log
--exclude=*/__pycache__
--exclude=*.part
--exclude=.uv/cache'
# Few-step / turbo H3 LoRAs are banned for good (user, 2026-10-06/08: «никогда»). They were downloaded onto a
# server by hand once and rode along in snapshots; this pattern keeps them out of new snapshots and out of every
# deploy, including deploys from old snapshots that still list them. Matched against weights.tsv / symlinks.tsv
# lines (path first). The ACE-Step «turbo» music model lives in audio/ and is not matched.
# Old snapshots also carry staged experiments never wired into Studio. Keep the active
# face adapter, the canonical ComfyUI X2 VAE and current H3/audio/acting weights. LTX and Qwen-Image(-Edit)
# are gone from Studio (2026-11-03: keyframes are drawn by Codex) and never restored. The
# optional Qwen Director 8B is not deployed: Studio uses subscription agents and H3 keeps its 32B encoder.
# ACE-Step uses SFT in audio_acestep_music.py; the old unused turbo checkpoint is omitted.
H3_BANNED_WEIGHTS_RE='^(LoRA_Library|ComfyUI/models/loras)/[^[:space:]]*(turbo|hyperflow|lightx2v|taomate|dmad|fasth3|pika_h3_flex|[0-9]step)|^ComfyUI/models/(diffusion_models|text_encoders|vae|loras)/[^[:space:]]*[Ll][Tt][Xx]|^hf-cache/hub/models--Qwen--Qwen3-VL-8B-Instruct/|^hf-cache/hub/models--Qwen--Qwen3\.[68]-27B-FP8/|^LoRA_Library/(experimental/|loras/(camera-motion|crossview-ref2va|2d-looping-sketch|transformation)/|components/(experimental-vae|latent-upscaler)/)|^audio/ace-checkpoints/acestep-v15-turbo/|^ComfyUI/models/[^[:space:]]*([Qq]wen[_-][Ii]mage|qwen_2\.5_vl_7b|seedvr2)'
H3_WEIGHT_DIRS='ComfyUI/models LoRA_Library hf-cache audio/ace-checkpoints audio/CosyVoice/pretrained_models LivePortrait/pretrained_weights'

# weights.tsv: "path<TAB>size<TAB>sha256" per weight file (paths relative to /workspace/h3). Images built
# offline (infra/h3_image_build.sh) add "<TAB>source<TAB>revision<TAB>source_path": source = Hugging Face
# repo id or https URL, "-" = only from a server/R2 copy. Readers use the first three columns (NF>=3).
# Remote shell snippet: one "path<TAB>size<TAB>sha256" line per weight file, sorted (run in /workspace/h3).
h3_weights_inventory_cmd() {
  printf '%s' "cd /workspace/h3 && find $H3_WEIGHT_DIRS -type f -size +0 ! -name '*.part' ! -path '*/__pycache__/*' -print0 2>/dev/null \
  | xargs -0 -P 32 -n 4 sh -c 'for f; do printf \"%s\t%s\t%s\n\" \"\$f\" \"\$(stat -c %s \"\$f\")\" \"\$(sha256sum \"\$f\" | cut -d\" \" -f1)\"; done' _ \
  | sort | { grep -viE '$H3_BANNED_WEIGHTS_RE' || true; }"
}

# Remote shell snippet: token/key leak scan of everything that goes into the archive (empty output = clean).
h3_leak_scan_cmd() {
  printf '%s' "cd /workspace/h3 && grep -rlIE 'hf_[A-Za-z0-9]{30,}|BEGIN (OPENSSH|RSA|EC) PRIVATE KEY|AKIA[0-9A-Z]{16}' \
  --exclude-dir=models --exclude-dir=hf-cache --exclude-dir=ace-checkpoints --exclude-dir=pretrained_models \
  --exclude-dir=pretrained_weights --exclude-dir=input --exclude-dir=output --exclude-dir=api-data \
  --exclude-dir=__pycache__ --exclude-dir=site-packages --exclude-dir=venv --exclude-dir=api-venv \
  --exclude-dir=liveportrait-venv --exclude-dir=.venv --exclude-dir=node_modules \
  . /etc/supervisor/conf.d /opt/supervisor-scripts 2>/dev/null || true"
}
