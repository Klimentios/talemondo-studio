#!/usr/bin/env python3
"""Run one CosyVoice3 line in its own process so VRAM is released on exit.

Call with the CosyVoice venv Python; do not install its dependencies into H3's venv.
The API follows QwenAudio/CosyVoice example.py for CosyVoice3.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--direction", default="")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.repo.is_dir() or not args.model.is_dir() or not args.reference.is_file():
        parser.error("CosyVoice3, его веса или образец голоса недоступны")
    if not args.text.strip() or len(args.text) > 1200 or len(args.direction) > 400:
        parser.error("Неверный текст или стиль реплики")
    sys.path.insert(0, str(args.repo))
    sys.path.insert(0, str(args.repo / "third_party/Matcha-TTS"))
    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import AutoModel

    model = AutoModel(model_dir=str(args.model))
    instruction = args.direction.strip() or "Speak naturally and clearly."
    prompt = f"You are a helpful assistant. {instruction}<|endofprompt|>"
    chunks = [part["tts_speech"] for part in model.inference_instruct2(
        args.text.strip(), prompt, str(args.reference), stream=False)]
    if not chunks:
        raise RuntimeError("CosyVoice3 не вернула звук")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(args.output), torch.cat(chunks, dim=-1), model.sample_rate)


if __name__ == "__main__":
    main()
