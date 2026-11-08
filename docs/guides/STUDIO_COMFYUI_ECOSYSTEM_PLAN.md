# Studio + ComfyUI для MiniMax H3: одна система (план от 04.11.2026)

**Цель.** Studio остаётся единственной точкой управления. Всё, что умеет ComfyUI, доходит до узла через наш построитель графа (`app/h3graph`), обязательные узлы (`required_nodes`) и проверку возможностей GPU (`/api/capabilities`). Новые пакеты нод и веса ставит только `scripts/install_h3_h200_overlay.sh`: закреплённая ревизия плюс SHA. `scripts/verify_h3_server.sh` проверяет весь набор. Ручных правок на сервере нет.

Основа плана: код ComfyUI ([H3_ENGINE_INTERNALS.md](../reference/H3_ENGINE_INTERNALS.md)), официальная документация ([MINIMAX_H3_OFFICIAL_DOCS.md](../reference/MINIMAX_H3_OFFICIAL_DOCS.md)), обзор пакетов сообщества и 41 брак замен за 03–04.11. Каждый пункт принимается только по кадрам, на 24 шагах, на одном seed (DECISIONS). Турбо и дистилляции шагов не берём.

## Где мы сейчас

- ComfyUI `b5cc8830` (22.09.2026) на сервере и на Mac. Это на 276 коммитов позади `master`. Исправления швов тайлов VAE (`fc584aaa`) и падения rms_rope (`912fca4f`) у нас уже есть.
- Граф совпадает с официальным шаблоном R2V Comfy-Org: `res_multistep` + `simple`, сдвиги 12/3 модель применяет сама.
- Замена героя идёт через Character Swap LoRA от akatz **V1** (`h3_character_swap_pro4500_1000`).

## P1 — качество замен (главная боль 03–04.11)

| Пункт | Что даёт | Доказательство | Работа |
|---|---|---|---|
| **Character Swap V1.5** | Автор: меньше возврата к облику исходника, лучше держит стилизованные лица | Сравнение V1/V1.5 в репозитории автора, выборочное. Наш брак ровно этого типа | Веса уже в установщике (SHA `8f2c3c3e…`). Осталось подключить адаптер `character_swap_v15` в Studio и на GPU, затем A/B V1 против V1.5 на одном шоте |
| **Маска исполнителя** — сделано 04.11 (без GPU) | Перерисовываются только выбранные исполнители, остальное — пиксели исходника. Лишний кот или актёр вне маски появиться не может | Тот же принцип, что в рецепте автора LoRA (SAM + edit mask). Наш код: `app/swap_mask.py`, узлы `h3-swap-mask` (`H3SourceFrames`, `H3SourceEditMask`) поверх нашего трека SAM2.1 (`H3CharacterTrack`/`H3CharacterMask`). SAM3 не нужен | Настройка `swap_mask` (боксы исполнителей на первом кадре части), флаг GPU `swap_mask_v1`. Узел проверен в окружении ComfyUI на подставном VAE. Рендер — A/B на GPU |
| Уточнение героя (crop → H3 → composite) | Повторный проход по герою для мелких лиц и фигур | У нас уже есть: `character_refinement` (`h3-character-refine`, SAM2.1). H3-FaceRefine ставить не нужно | Проверить на общем плане после A/B маски |
| Короткий позитивный промпт | Рецепт автора LoRA: 4 фразы «Replace… Keep… Follow… Preserve…» | Совпадает с нашим журналом провалов | Уже сделано: предел 4000 знаков, исполнитель по месту, guard без описаний (b66f4f9b) |

## P2 — стабильность и инфраструктура

| Пункт | Что даёт | Работа |
|---|---|---|
| **Обновить ComfyUI** до текущего `master` | Ниже пиковый VRAM у H3 (`2d6b7328`, у нас было ~130/144 ГБ), исправлена выгрузка VAE (`83071e1a`), латентный апскейлер H3 в ядре (`35060f01`) | На отдельном сервере: установщик → `verify_h3_server.sh` (все входы по `/object_info`) → golden-графы → один рендер сравнения. Наши узлы H3FilmLook/H3ColorGrade/кэш проверить на новом Linear API (`0752bcb2`, `62c49c4d`) |
| ↳ статическая проверка обновления (04.11, без GPU) | Сверено с `master` `7ad7adb3` (04.11.2026): схемы узлов H3, которые мы используем (`MiniMaxH3ReferenceToVideo`, `AddGuide`, `SigmaShift`, `EmptyMiniMaxH3LatentAV`), не изменились, добавлены только `MiniMaxH3LatentUpscaleWithModel` и `PH3ImageConditioning`. Внутренние функции наших узлов (`_encode_ref_audio`, `_resize`, `FRAME_PER_TOKEN`, `FRAME_RESCALE`, `comfy.nested_tensor`) на месте. Контракт `patches_replace["dit"]` и сигнатура `forward` для FirstBlockCache (duckyshell `f7a2712`) те же. Входы `BlockSparseAttention` те же. Метки токенизатора без изменений | Осталось проверить на GPU: новый Linear API (`0752bcb2`, `62c49c4d`) во время рендера, golden-графы и один рендер |
| **HybridLoader** (FL2VA + adaLN Ref2VA, блоки 25–49) | Возможно чище картинка Ref2VA: веса совпадают на 97%, различие в adaLN | A/B. Оценка автора субъективная |
| Бюджет потока в карточке | Видно, куда уходят токены (сделано 740b8a72) | — |

## P3 — скорость без потери качества

| Пункт | Ожидание | Работа |
|---|---|---|
| **Исходник замены в 480×864** | Внимание ×0,46 по `app/h3_budget.py` | Опция плана замены, A/B на движении рук и мимике |
| **VPack** (сжатие токенов видео-референса) | Автор: −24% времени DiT | A/B, один тест автора |
| **Апскейл только принятых дублей** (латентный апскейлер ядра, LBH) | Черновик 0,8 Мп → апскейл принятого | После обновления ComfyUI |
| Sol-H3-VAE | Декодирование ×3 быстрее: секунды из ~10 минут | Низкий приоритет |

## Не берём (и почему)

- PDD, DMD, lightx2v, турбо-LoRA, VSA/VDN/Spectrum: дистилляция шагов или приближения. Решение пользователя — без турбо.
- Стек xmarre (Flow-Aligned Regenerate и др.): много патчей модели в строгом порядке, часть под GPL.
- RH_MinMaxH3, xiaolibai MiniMaxH3: заменяют ядро своим рантаймом. Рефайнеры xiaolibai ходят в платный API, а у нас только подписки.
- MiniMaxDirector, shots-skills: это уже есть в Studio (компилятор шотов, `h3-camera-shots`).
- MiniMax API для видео: отключён (DECISIONS 04.11).

## Порядок, когда поднимется GPU

1. Деплой текущего `app/`, чтобы появились `ref_beta` и все правки 04.11.
2. Один шот замены героев как эталон после правок промптов: проверить, ушли ли утечки.
3. A/B на том же шоте: V1 против V1.5; `standard` против `ref_beta`; исходник 768 против 480.
4. Обновление ComfyUI на отдельном сервере, потом перенос.
5. Маршрут с SAM-маской и FaceRefine — по результатам шагов 2–3.

Источники обзора пакетов: [akatz-ai Character Swap LoRA](https://huggingface.co/akatz-ai/MiniMax-H3-Character-Swap-LoRA), [h3-relay](https://github.com/akatz-ai/h3-relay), [HybridLoader](https://github.com/scottmudge/ComfyUI_MinimaxH3HybridLoader), [H3-FaceRefine](https://github.com/Carasibana/H3-FaceRefine), [коммиты ComfyUI по H3](https://github.com/Comfy-Org/ComfyUI/commits/master/comfy/ldm/minimax), [Sol-H3-VAE PR](https://github.com/Comfy-Org/ComfyUI/pull/17411), [латентный апскейлер PR](https://github.com/Comfy-Org/ComfyUI/pull/17412).
