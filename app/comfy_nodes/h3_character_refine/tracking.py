"""Track sanity check shared by the crop/composite nodes (tracking itself is SAM2, sam_tracking.py)."""


def check_tracks(images, tracks, character):
    if list(images.shape) != tracks['shape'] or not 0 <= character < len(tracks['names']):
        raise ValueError('Трек не соответствует исходному ролику или герою')
