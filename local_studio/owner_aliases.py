"""Resolve spelling aliases only when they identify one existing hero."""
import re

_CYRILLIC = dict(zip('абвгдеёжзийклмнопрстуфхцчшщыьъэюя',
    ('a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','ts','ch','sh','shch','y','','','e','yu','ya')))


def name_key(value: str, *, strip_suffix: bool = False) -> str:
    value = value.strip().casefold()
    if strip_suffix:
        value = re.sub(r'[\s_-]+\d+$', '', value)
    return re.sub('[^a-z0-9]', '', ''.join(_CYRILLIC.get(c, c) for c in value))


def resolve_owner(value: str, names: list[str]) -> str | None:
    if not isinstance(value, str):
        return None
    for strip in (False, True):
        key = name_key(value, strip_suffix=strip)
        matches = [name for name in names if key and name_key(name, strip_suffix=strip) == key]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None
