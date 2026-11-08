"""Edit masks from H3CharacterTrack tracks (numpy/OpenCV only, importable without SAM2)."""
import numpy as np
import torch


def parse_characters(characters, count):
    try:
        chosen = sorted({int(part) for part in str(characters).split(',') if part.strip()})
    except ValueError as exc:
        raise ValueError('characters: индексы героев через запятую') from exc
    if not chosen or any(not 0 <= index < count for index in chosen):
        raise ValueError(f'characters: от 0 до {count - 1}')
    return chosen


def character_mask(tracks, characters, mode, grow, feather):
    import cv2
    if mode not in ('keep', 'replace'):
        raise ValueError('mode: keep или replace')
    count = len(tracks['names'])
    chosen = parse_characters(characters, count)
    frames = []
    kernel = np.ones((2 * grow + 1, 2 * grow + 1), dtype='uint8') if grow else None
    for per_frame in tracks['masks']:
        person = np.zeros(per_frame[0].shape, dtype='uint8')
        for index in chosen:
            person |= per_frame[index].astype('uint8')
        if kernel is not None:
            person = cv2.dilate(person, kernel)
        region = person.astype('float32')
        if feather:
            region = cv2.GaussianBlur(region, (0, 0), feather / 2)
        frames.append(region if mode == 'replace' else 1.0 - region)
    return torch.from_numpy(np.stack(frames)).clamp(0, 1)
