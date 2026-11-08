"""Run one ACE-Step SFT music job in a disposable GPU process."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import shutil
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--checkpoints", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--lyrics", default="")
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bpm", type=int, default=0)
    parser.add_argument("--keyscale", default="")
    parser.add_argument("--timesignature", default="")
    parser.add_argument("--seed", type=int, default=-1)
    args = parser.parse_args()
    if not 10 <= args.duration <= 600 or not args.prompt.strip() or len(args.prompt) > 500:
        parser.error("Музыкальный запрос должен быть непустым, длительность 10–600 секунд")
    if len(args.lyrics) > 4096:
        parser.error("Текст песни слишком длинный")
    if args.bpm and not 30 <= args.bpm <= 300:
        parser.error("Темп музыки должен быть от 30 до 300 BPM")
    if args.keyscale and not re.fullmatch(r"[A-G](?:#|b)?(?: ?(?:Major|minor)|m)?", args.keyscale):
        parser.error("Неверная тональность музыки")
    if args.timesignature not in {"", "2", "3", "4", "6"} or not -1 <= args.seed <= 2147483647:
        parser.error("Неверный размер или seed музыки")
    if not (args.checkpoints / "acestep-v15-sft/model.safetensors").is_file():
        parser.error("Веса ACE-Step SFT не найдены")
    os.environ["ACESTEP_CHECKPOINTS_DIR"] = str(args.checkpoints.resolve())
    sys.path.insert(0, str(args.repo))
    from acestep.handler import AceStepHandler
    from acestep.llm_inference import LLMHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music

    dit = AceStepHandler()
    dit.initialize_service(project_root=str(args.repo), config_path="acestep-v15-sft", device="cuda")
    lm = LLMHandler()
    lm.initialize(checkpoint_dir=str(args.checkpoints), lm_model_path="acestep-5Hz-lm-1.7B",
                  backend="vllm", device="cuda")
    lyrics = args.lyrics.strip()
    params = GenerationParams(task_type="text2music", caption=args.prompt.strip(),
                              lyrics=lyrics or "[Instrumental]", instrumental=not bool(lyrics),
                              duration=args.duration,
                              bpm=args.bpm or None, keyscale=args.keyscale,
                              timesignature=args.timesignature, seed=args.seed,
                              inference_steps=64, guidance_scale=8.0, use_adg=True,
                              shift=3.0, thinking=True, use_cot_caption=False)
    config = GenerationConfig(batch_size=1, audio_format="wav",
                              use_random_seed=args.seed < 0,
                              seeds=[args.seed] if args.seed >= 0 else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = generate_music(dit, lm, params, config, save_dir=str(args.output.parent))
    if not result.success or not result.audios:
        raise RuntimeError(f"ACE-Step: {result.error or 'пустой результат'}")
    source = Path(result.audios[0]["path"]).resolve()
    if not source.is_file() or args.output.resolve() == source:
        if not source.is_file():
            raise RuntimeError("ACE-Step не сохранила аудио")
    else:
        shutil.copyfile(source, args.output)


if __name__ == "__main__":
    main()
