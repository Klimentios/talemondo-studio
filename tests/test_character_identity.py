import pytest

from local_studio.character_identity import replacement_identity
from local_studio.store import source_swap_identity_contract, compile_character_swap_prompt
from local_studio.character_identity import validate_replacement_prompt


@pytest.mark.parametrize('prompt', [
    'Preserve the source faces.', "Copy the original actor’s body shape.",
    "Keep the source performers' eye spacing.", 'Match source clothing.',
    'Сохрани телосложение исходного исполнителя.', 'Повтори одежду из видео.',
])
def test_source_traits_conflict_with_target_authority(prompt):
    images = ({'job_owner': 'TARGET', 'job_role': 'Персонаж целиком'},)
    videos = ({'job_role': 'Исходное видео', 'job_performers': [{'owner': 'TARGET', 'performer': 'cat'}]},)
    for compile_prompt in (source_swap_identity_contract, compile_character_swap_prompt):
        with pytest.raises(ValueError, match='референсы целевого героя'):
            compile_prompt(prompt, images, videos)


@pytest.mark.parametrize('prompt', [
    'Do not copy source faces. Preserve source camera and timing.',
    "Never keep the source actor's body mass.", 'Не повтори одежду из видео.',
    'Replace the fat cat on the left with VEGA. Copy source movement.',
])
def test_negation_and_performer_description_are_allowed(prompt):
    validate_replacement_prompt(prompt)


def test_replacement_uses_each_targets_own_face_body_and_clothing_authority():
    images = ({'job_owner': 'ADAM', 'job_role': 'Персонаж целиком'},
              {'job_owner': 'VEGA', 'job_role': 'Персонаж целиком'},
              {'job_owner': 'ADAM', 'job_role': 'Лицо героя'},
              {'job_owner': 'ADAM', 'job_role': 'Костюм'})
    text = replacement_identity('ADAM', '<Video 1>', images)
    assert 'face from <Picture 3>' in text
    assert 'body, proportions and scale from <Picture 1>' in text
    assert 'clothes from <Picture 4>' in text
    assert '<Picture 2>' not in text
    assert len(text) < 220  # one sentence, like the accepted swaps (2026-11-04)
    for owner in ('VEGA', 'MIRA', 'OTHER'):
        own = ({'job_owner': owner, 'job_role': 'Персонаж целиком'},)
        assert 'face from <Picture 1>' in replacement_identity(owner, '<Video 1>', own)


def test_source_and_swap_compilers_apply_policy_and_photo_only_stays_unchanged():
    images = ({'job_owner': 'TARGET', 'job_role': 'Персонаж целиком'},)
    videos = ({'job_role': 'Исходное видео', 'job_performers': [{'owner': 'TARGET', 'performer': 'source actor'}]},)
    for out in (source_swap_identity_contract('A scene', images, videos), compile_character_swap_prompt('A scene', images, videos)):
        assert 'TARGET: face from <Picture 1>' in out and 'mapped performer' in out
    assert source_swap_identity_contract('A scene', images, ()) == 'A scene'
    with pytest.raises(ValueError, match='Персонаж целиком'):
        source_swap_identity_contract('A scene', ({'job_owner': 'TARGET', 'job_role': 'Лицо героя'},), videos)
