"""Whole-body masks via official SAM2 video predictor; model released before H3 sampling."""
import gc
import json
import os
from pathlib import Path
import tempfile
import numpy as np
import torch
from PIL import Image


def checkpoint_path():
    override = os.environ.get('H3_CHARACTER_SAM2_CHECKPOINT')
    if override:
        return Path(override)
    import folder_paths
    return Path(folder_paths.models_dir)/'sam2'/'sam2.1_hiera_large.pt'


def track_sam2(images, characters_json, checkpoint=None, device=None, admission_check=None, frame_grid='h3'):
    from sam2.build_sam import build_sam2_video_predictor
    chars = json.loads(characters_json)
    if not 1 <= len(chars) <= 4:
        raise ValueError('Нужны 1–4 героя')
    # 'h3': an H3 clip (17k+5 frames, refine pass); 'ltx': a source clip for an LTX-2.5 edit (8n+1 frames).
    if frame_grid == 'h3' and (len(images) < 5 or len(images) % 17 != 5):
        raise ValueError('Нужен H3-ролик с 17k+5 кадрами')
    if frame_grid == 'ltx' and (len(images) < 9 or len(images) % 8 != 1):
        raise ValueError('Нужен ролик с 8n+1 кадрами (LTX)')
    if frame_grid not in ('h3', 'ltx'):
        raise ValueError('frame_grid: h3 или ltx')
    checkpoint = Path(checkpoint or checkpoint_path())
    if not checkpoint.is_file():
        raise ValueError('Не установлены веса SAM2.1 large для отслеживания героев')
    device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
    height, width = images.shape[1:3]
    if admission_check:
        admission_check()
    predictor = None
    state = None
    try:
        with tempfile.TemporaryDirectory(prefix='h3-character-track-') as temp:
            # JPEG copies are only SAM2 analysis inputs. H3 crops use original float frames.
            for i, frame in enumerate(images):
                Image.fromarray(np.clip(frame*255, 0, 255).astype(np.uint8)).save(Path(temp)/f'{i:05d}.jpg', quality=95)
            predictor = build_sam2_video_predictor('configs/sam2.1/sam2.1_hiera_l.yaml', str(checkpoint), device=device, apply_postprocessing=False, hydra_overrides_extra=['++model.non_overlap_masks=true'])
            with torch.inference_mode():
                state = predictor.init_state(temp, offload_video_to_cpu=True, offload_state_to_cpu=True)
                for i, char in enumerate(chars):
                    x,y,w,h = char['box']
                    predictor.add_new_points_or_box(state, frame_idx=0, obj_id=i+1,
                                                   box=np.array([x*width,y*height,(x+w)*width,(y+h)*height],dtype=np.float32))
                boxes_by_frame = [None]*len(images)
                masks_by_frame = [None]*len(images)
                for frame, object_ids, logits in predictor.propagate_in_video(state):
                    if admission_check:
                        admission_check()
                    boxes, masks = [], []
                    for i, char in enumerate(chars):
                        if i+1 not in object_ids:
                            raise ValueError(f"Потерян объект {char['name']} на кадре {frame}")
                        mask = (logits[object_ids.index(i+1),0] > 0).cpu().numpy()
                        ys,xs = np.nonzero(mask)
                        if len(xs) < 64:
                            if frame == 0:
                                raise ValueError(f"Не найдена начальная маска {char['name']}; уточни область")
                            boxes.append(boxes_by_frame[frame-1][i])
                            masks.append(np.zeros((height,width),dtype=bool))
                            continue
                        # Pad the body region for costume edges while keeping original canvas bounds.
                        pad = max(4, round(max(xs.max()-xs.min(),ys.max()-ys.min())*.06))
                        x0,y0=max(0,int(xs.min())-pad),max(0,int(ys.min())-pad)
                        x1,y1=min(width,int(xs.max())+pad+1),min(height,int(ys.max())+pad+1)
                        boxes.append((x0,y0,x1-x0,y1-y0));masks.append(mask)
                    for i, mask in enumerate(masks):
                        for other in masks[i+1:]:
                            if np.any(mask & other):
                                raise ValueError(f'Маски героев пересекаются на кадре {frame}; уточни выбор')
                    boxes_by_frame[frame] = boxes
                    masks_by_frame[frame] = masks
                if any(b is None for b in boxes_by_frame):
                    raise ValueError('SAM2 не вернул треки для всех кадров')
                return {'boxes':boxes_by_frame, 'masks':masks_by_frame, 'shape':list(images.shape),
                        'names':[c['name'] for c in chars],
                        'missing_frames':{c['name']:[f for f,m in enumerate(masks_by_frame) if not m[i].any()] for i,c in enumerate(chars)}}
    finally:
        del state, predictor
        gc.collect()
        if device == 'cuda':
            torch.cuda.empty_cache()
