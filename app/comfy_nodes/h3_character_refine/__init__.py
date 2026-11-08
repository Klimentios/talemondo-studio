"""Experimental whole-character H3 crop/refine/composite nodes, humans and animals."""
import numpy as np
import torch
import torch.nn.functional as F
from .tracking import check_tracks
from .masks import character_mask
from .sam_tracking import track_sam2, checkpoint_path
from sam2.build_sam import build_sam2_video_predictor  # noqa: F401 — fail import early if SAM2 is missing
if not checkpoint_path().is_file():
    raise ImportError("H3 Character Refine requires SAM2.1 large checkpoint")


SAM2_VRAM_BYTES = 8 * 1024 ** 3


class H3CharacterTrack:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'images': ('IMAGE',), 'characters_json': ('STRING', {'multiline': True})},
                # Optional so saved refine graphs stay valid: absent = the H3 17k+5 grid.
                'optional': {'frame_grid': (['h3', 'ltx'], {'default': 'h3'})}}
    RETURN_TYPES = ('H3_CHARACTER_TRACKS',)
    FUNCTION = 'track'
    CATEGORY = 'MiniMax H3/Character Refine'

    def track(self, images, characters_json, frame_grid='h3'):
        import comfy.model_management as model_management
        # Free only what SAM2.1-large tracking needs (state is offloaded to CPU). Unloading
        # everything evicted H3 (~117 GB) and every following refine pass reloaded it.
        model_management.free_memory(SAM2_VRAM_BYTES, model_management.get_torch_device())
        model_management.soft_empty_cache()
        tracks = track_sam2(images.detach().cpu().numpy(), characters_json, frame_grid=frame_grid)
        return {'ui': {'text': [__import__('json').dumps({'missing_frames': tracks['missing_frames']}, ensure_ascii=False)]}, 'result': (tracks,)}


class H3CharacterCrop:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'images': ('IMAGE',), 'tracks': ('H3_CHARACTER_TRACKS',),
                            'character': ('INT', {'default': 0, 'min': 0, 'max': 3}),
                            'width': ('INT', {'default': 768, 'min': 512, 'max': 1536, 'step': 32}),
                            'height': ('INT', {'default': 768, 'min': 512, 'max': 1536, 'step': 32})}}
    RETURN_TYPES = ('IMAGE', 'INT')
    FUNCTION = 'crop'
    CATEGORY = 'MiniMax H3/Character Refine'

    def crop(self, images, tracks, character, width, height):
        check_tracks(images, tracks, character)
        crops = []
        for frame_index, (frame, boxes) in enumerate(zip(images, tracks['boxes'])):
            x, y, w, h = boxes[character]
            region = frame[y:y+h, x:x+w].clone()
            if 'masks' in tracks:
                if not tracks['masks'][frame_index][character].any():
                    region.zero_()
                for other, other_mask in enumerate(tracks['masks'][frame_index]):
                    if other != character:
                        foreign = torch.from_numpy(other_mask[y:y+h,x:x+w]).to(region.device)
                        region[foreign] = 0
            crop = region.permute(2, 0, 1)[None]
            scale = min(width/w, height/h)
            rw, rh = max(1, round(w*scale)), max(1, round(h*scale))
            resized = F.interpolate(crop, size=(rh, rw), mode='bicubic', align_corners=False)[0].permute(1, 2, 0)
            canvas = images.new_zeros((height, width, 3))
            px, py = (width-rw)//2, (height-rh)//2
            canvas[py:py+rh, px:px+rw] = resized.clamp(0, 1)
            crops.append(canvas)
        return (torch.stack(crops), len(crops))


class H3CharacterComposite:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'images': ('IMAGE',), 'refined': ('IMAGE',), 'tracks': ('H3_CHARACTER_TRACKS',),
                            'character': ('INT', {'default': 0, 'min': 0, 'max': 3}),
                            'feather': ('FLOAT', {'default': 0.04, 'min': 0.0, 'max': 0.2})}}
    RETURN_TYPES = ('IMAGE',)
    FUNCTION = 'composite'
    CATEGORY = 'MiniMax H3/Character Refine'

    def composite(self, images, refined, tracks, character, feather):
        check_tracks(images, tracks, character)
        if len(images) != len(refined):
            raise ValueError('Уточнение изменило число кадров; склейка запрещена')
        result = images.clone()
        for i, boxes in enumerate(tracks['boxes']):
            x, y, w, h = boxes[character]
            ch, cw = refined.shape[1:3]
            scale = min(cw/w, ch/h)
            rw, rh = max(1, round(w*scale)), max(1, round(h*scale))
            px, py = (cw-rw)//2, (ch-rh)//2
            region = refined[i, py:py+rh, px:px+rw]
            crop = F.interpolate(region.permute(2, 0, 1)[None], size=(h, w), mode='bicubic', align_corners=False)[0].permute(1, 2, 0).to(result)
            dx = torch.minimum(torch.arange(w), torch.arange(w-1, -1, -1)).to(result)
            dy = torch.minimum(torch.arange(h), torch.arange(h-1, -1, -1)).to(result)
            radius = max(1, min(w, h)*feather)
            mask = (torch.minimum(dy[:, None], dx[None, :])/radius).clamp(0, 1)[..., None] if feather else torch.ones((h, w, 1)).to(result)
            if 'masks' in tracks:
                import cv2
                original_mask = tracks['masks'][i][character].astype('uint8')
                # Small contour expansion includes fur/costume edges; protect other original subjects.
                radius_px = max(1, round(min(w,h)*feather))
                alpha = cv2.dilate(original_mask, np.ones((2*radius_px+1,2*radius_px+1), dtype='uint8')).astype('float32')
                for other, other_mask in enumerate(tracks['masks'][i]):
                    if other != character:
                        alpha[other_mask] = 0
                alpha = cv2.GaussianBlur(alpha,(0,0),max(.5,radius_px/2))
                for other, other_mask in enumerate(tracks['masks'][i]):
                    if other != character:
                        alpha[other_mask] = 0
                mask = mask * torch.from_numpy(alpha[y:y+h,x:x+w])[...,None].to(result)
            result[i, y:y+h, x:x+w] = (crop*mask + result[i, y:y+h, x:x+w]*(1-mask)).clamp(0, 1)
        return (result,)


class H3CharacterMask:
    """Per-frame edit mask from character tracks (video edit: «keep these people» / «change this person»).

    Output convention is the latent noise mask: 1 = regenerate, 0 = keep the source pixels.
    keep: everything except the chosen characters is regenerated. replace: only the chosen characters are.
    grow (px) widens the person region (hair, costume edges); feather (px) softens the border.
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'tracks': ('H3_CHARACTER_TRACKS',),
                             'characters': ('STRING', {'default': '0', 'tooltip': 'Индексы героев через запятую'}),
                             'mode': (['keep', 'replace'], {'default': 'keep'}),
                             'grow': ('INT', {'default': 12, 'min': 0, 'max': 128}),
                             'feather': ('INT', {'default': 8, 'min': 0, 'max': 64})}}
    RETURN_TYPES = ('MASK',)
    FUNCTION = 'mask'
    CATEGORY = 'MiniMax H3/Character Refine'

    def mask(self, tracks, characters, mode, grow, feather):
        return (character_mask(tracks, characters, mode, grow, feather),)


NODE_CLASS_MAPPINGS = {c.__name__: c for c in (H3CharacterTrack, H3CharacterCrop, H3CharacterComposite, H3CharacterMask)}
NODE_DISPLAY_NAME_MAPPINGS = {k:k for k in NODE_CLASS_MAPPINGS}
