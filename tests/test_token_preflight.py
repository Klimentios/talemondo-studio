"""Pre-GPU text check against the job's inputs (local_studio/token_preflight.py)."""
import pytest

from local_studio.token_preflight import dialogue_problems, label_problems, preflight


def test_labels_must_name_a_connected_input_of_their_type():
    assert label_problems("<Picture 1> and <Video 1> with <Audio 1>", pictures=1, videos=1, audios=1) == []
    wrong = label_problems("<Picture 3> walks; <Video 2> moves; <Audio 1> plays", pictures=2, videos=1, audios=0)
    assert [w.split(":")[0] for w in wrong] == ["<Audio 1>", "<Picture 3>", "<Video 2>"]


def test_dialogue_tokens_are_closed_and_tagged():
    assert dialogue_problems("<Subject 1> (S1) says, <d>[English] Go!</d>") == []
    assert dialogue_problems("says, <d>[English] Go!") == ["реплики не закрыты: <d> 1, </d> 0"]
    assert "реплика без языка: нужно <d>[English] …</d>" in dialogue_problems("says, <d>Go!</d>")


def test_preflight_blocks_before_gpu():
    with pytest.raises(ValueError, match="<Picture 2>"):
        preflight("<Picture 2> runs", pictures=1, videos=0, audios=0)
    preflight("<Picture 1> runs", pictures=1, videos=0, audios=0)
