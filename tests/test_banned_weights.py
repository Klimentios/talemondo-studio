import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BANNED = [
    "LoRA_Library/experimental/dmad/dmad_minimax_h3_4step_lora_critic.safetensors",
    "LoRA_Library/experimental/fl2va-only/minimax_h3_fl2va_fasth3_preview_v0.2_lora_pruned_rank128_fp16.safetensors",
    "LoRA_Library/experimental/fl2va-only/minimax_h3_taomate_3step_lora_avg_rank_19_bf16.safetensors",
    "LoRA_Library/experimental/hyperflow-comfyui/minimax_h3_hyperflow_8step_v1.0_comfyui_bf16_resized_avg_rank_20_bf16.safetensors",
    "LoRA_Library/experimental/turbo-ref2v/minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_resized_avg_rank_20_bf16.safetensors",
    "ComfyUI/models/loras/minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_resized_avg_rank_20_bf16.safetensors",
    "ComfyUI/models/loras/minimax_h3_fl2v_lightx2v_turbo_4step_v0.1_comfy.safetensors",
    "ComfyUI/models/loras/pika_h3_flex_ref2va_comfy_bf16.safetensors",  # six-NFE distillation, same class as turbo
    "LoRA_Library/loras/crossview-ref2va/MiniMax-H3_Ref2VA-LoRA-CrossView-Warp_v1_3500.safetensors",
    "LoRA_Library/components/experimental-vae/MiniMax-H3-X2-Detail-v1.safetensors",
    "hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/blobs/unused",
    "audio/ace-checkpoints/acestep-v15-turbo/model.safetensors",
]
KEPT = [
    "ComfyUI/models/loras/camera_motion_h3_lora_v1_3000_pruned.safetensors",  # kept since f0b2792c
    "audio/ace-checkpoints/acestep-v15-sft/model.safetensors",  # active music model, not an H3 LoRA
    "LoRA_Library/loras/face-closeup/minimax-h3-facial-realism-closeup-cp2000.safetensors",
    "ComfyUI/models/vae/MiniMax-H3-X2-Detail-v1.safetensors",
    "ComfyUI/models/embeddings/minimaxh3_spiral_ascent.safetensors",
    "ComfyUI/models/loras/minimax-h3-facial-realism-closeup-cp2000.safetensors",
    "ComfyUI/models/diffusion_models/minimax_h3_ref2va_bf16.safetensors",
]


def test_turbo_and_few_step_loras_never_reach_a_snapshot_or_deploy():
    lines = "".join(f"{path}\t1\tabc\n" for path in BANNED + KEPT)
    kept = subprocess.run(
        ["bash", "-c", '. infra/h3_snapshot_spec.sh; grep -viE "$H3_BANNED_WEIGHTS_RE"'],
        cwd=ROOT, input=lines, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert [line.split("\t")[0] for line in kept] == KEPT
