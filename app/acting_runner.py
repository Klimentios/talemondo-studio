"""Execute the official LivePortrait pipeline in its own Python environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--mode", choices=("human", "animal"), required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--driving", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    source = Path(args.source).resolve(strict=True)
    driving = Path(args.driving).resolve(strict=True)
    if source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"} or driving.suffix.lower() not in {".mp4", ".mov"}:
        raise ValueError("LivePortrait accepts an image and a video, not a template")
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "src/utils/dependencies/XPose/models/UniPose/ops"))
    from src.config.argument_config import ArgumentConfig
    from src.config.inference_config import InferenceConfig
    from src.config.crop_config import CropConfig

    stitching = args.mode == "human"
    options = ArgumentConfig(source=str(source), driving=str(driving), output_dir=args.output_dir,
                             flag_stitching=stitching, flag_pasteback=stitching,
                             flag_crop_driving_video=False, flag_do_torch_compile=False)
    inference = InferenceConfig(**{k: v for k, v in vars(options).items() if k in InferenceConfig.__dataclass_fields__})
    crop = CropConfig(**{k: v for k, v in vars(options).items() if k in CropConfig.__dataclass_fields__})
    if args.mode == "animal":
        from src.live_portrait_pipeline_animal import LivePortraitPipelineAnimal
        output = LivePortraitPipelineAnimal(inference_cfg=inference, crop_cfg=crop).execute(options)[0]
    else:
        from src.live_portrait_pipeline import LivePortraitPipeline
        output = LivePortraitPipeline(inference_cfg=inference, crop_cfg=crop).execute(options)[0]
    if not Path(output).is_file() or Path(output).suffix != ".mp4":
        raise RuntimeError("LivePortrait did not return an MP4")
    print(f"H3_ACTING_OUTPUT={output}")


if __name__ == "__main__":
    main()
