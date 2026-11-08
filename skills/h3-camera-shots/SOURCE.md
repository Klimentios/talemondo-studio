# Источник

Сторонний навык: https://github.com/loopforge0/minimaxh3-shots-skills (LoopForge), лицензия MIT (`LICENSE`),
коммит `91cf6e2b6ee793b054f1754b2a0f316371fa558c`, взят 2026-11-01. Тексты не правим — обновлять заменой из
upstream. `upstream/snap.py` — оригинал скрипта ускорения зума; в Studio он перенесён в `local_studio/snap_zoom.py`.

Как это ложится на STUDIO: пресеты камеры Studio (`app/director_schema.json`) записаны на двенадцати примитивах
H3 из `references/camera-grammar.md`. Тесты автора — 16:9 1344×768, int8 + турбо выключен, 20 шагов; у нас BF16,
24 шага, 9:16 — длительность зума и сила движения у нас могут отличаться, проверяй по своему MP4.
