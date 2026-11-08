"""H3CharacterMask: noise-mask convention 1 = regenerate, 0 = keep source pixels."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("torch")
spec = importlib.util.spec_from_file_location(
    "h3_masks", Path(__file__).resolve().parents[1] / "app/comfy_nodes/h3_character_refine/masks.py")
masks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(masks)


def tracks():
    me = np.zeros((32, 32), bool); me[8:24, 4:12] = True
    grandpa = np.zeros((32, 32), bool); grandpa[8:24, 20:28] = True
    return {"names": ["Я", "Дед"], "masks": [[me, grandpa]] * 3}


def test_keep_regenerates_everything_but_me():
    out = masks.character_mask(tracks(), "0", "keep", grow=0, feather=0).numpy()
    assert out.shape == (3, 32, 32)
    assert out[0, 16, 8] == 0.0 and out[0, 16, 24] == 1.0 and out[0, 0, 0] == 1.0


def test_replace_regenerates_only_the_grandfather():
    out = masks.character_mask(tracks(), "1", "replace", grow=0, feather=0).numpy()
    assert out[0, 16, 24] == 1.0 and out[0, 16, 8] == 0.0 and out[0, 0, 0] == 0.0


def test_grow_and_feather_widen_the_kept_person():
    hard = masks.character_mask(tracks(), "0", "keep", grow=0, feather=0).numpy()
    grown = masks.character_mask(tracks(), "0", "keep", grow=2, feather=2).numpy()
    assert hard[0, 16, 13] == 1.0 and grown[0, 16, 13] < 0.5  # 1 px beside me is now protected


@pytest.mark.parametrize("bad", ["", "2", "x"])
def test_character_indexes_are_checked(bad):
    with pytest.raises(ValueError):
        masks.character_mask(tracks(), bad, "keep", 0, 0)
