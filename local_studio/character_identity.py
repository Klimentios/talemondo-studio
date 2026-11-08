"""Role-specific identity authority for character replacement, across franchises."""
from __future__ import annotations
import re


# Conservative preflight for direct conflicting instructions. This is not a
# semantic proof of prompt correctness; the rendered result still requires QC.
_SOURCE_TRAIT = re.compile(
    r"\b(?:match|copy|preserve|retain|keep)\s+(?:the\s+)?(?:original|source)\s+"
    r"(?:(?:performers?|actors?|characters?)(?:['’]s|['’])?\s+)?"
    r"(?:faces?|facial\s+geometry|eye\s+(?:spacing|layout)|body(?:\s+(?:shape|mass|proportions|size))?|"
    r"anatomical\s+proportions|size|shape|clothes|clothing|outfits?|costumes?)\b"
    r"|\b(?:сохрани\w*|скопиру\w*|повтори\w*)\s+(?:исходн\w*\s+)?"
    r"(?:лиц\w*|телосложени\w*|пропорци\w*|форм\w*\s+тел\w*|размер\w*|одежд\w*|костюм\w*|расположени\w*\s+глаз)"
    r"\s+(?:исходн\w*\s+)?(?:исполнител\w*|персонаж\w*|из\s+(?:исходного\s+)?(?:видео|референса))\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"(?:\b(?:do\s+not|don't|never|must\s+not|should\s+not|not\s+to)|\bне)\s*$", re.I)


def validate_replacement_prompt(prompt: str) -> None:
    """Reject explicit source-anatomy/wardrobe copying, allow explicit negation."""
    for match in _SOURCE_TRAIT.finditer(prompt):
        if not _NEGATED.search(prompt[max(0, match.start() - 32):match.start()]):
            raise ValueError("Замена: промпт просит сохранить лица исходных исполнителей, их тело, "
                             "пропорции или одежду; эти признаки должны задавать референсы целевого героя")


def replacement_identity(owner: str, video: str, images: tuple[dict, ...]) -> str:
    roles = {}
    for number, image in enumerate(images, 1):
        if str(image.get('job_owner') or '').casefold() == owner.casefold():
            roles.setdefault(image.get('job_role', image.get('role')), []).append(f'<Picture {number}>')
    body = roles.get('Персонаж целиком', [])
    if not body:
        raise ValueError(f'{owner}: для замены персонажа нужен собственный референс «Персонаж целиком»; '
                         'по одному лицу нельзя закрепить телосложение, пропорции и одежду')
    face = roles.get('Лицо героя') or body
    clothes = roles.get('Костюм') or body
    # One sentence: a 600-character anatomy block pushed swap prompts past 4–8k characters and they failed.
    return (f"{owner}: face from {', '.join(face)}, body, proportions and scale from {', '.join(body)}, "
            f"clothes from {', '.join(clothes)}; {video} gives only the mapped performer's action, expression "
            "and timing.")


PROPORTION_QC = (
    "For replacement video, compare each target to its own approved character references, not the source performer: "
    "head-to-body ratio, torso width/body mass, limb lengths, facial geometry and eye spacing, clothing shape/fit, "
    "and relative scale against the other target characters on the same ground plane. "
    "Separate foreshortening and perspective from anatomical changes. A source performer's body shape, eye layout "
    "or outfit transferred onto a target is a defect. If a trait is obscured or no matching reference exists, report "
    "it as unverified instead of asserting that proportions are correct."
)
