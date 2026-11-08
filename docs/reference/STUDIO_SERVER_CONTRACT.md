# Контракт Studio → GPU-сервер

Сервер не запускает интерфейс Studio. Он получает задание (`POST /api/jobs` на GPU API, порт 18189)
и собирает под него граф ComfyUI в `app/graph.py`. Каждая настройка Studio должна приводить к узлу или
параметру ниже; если узла или модели нет на хосте, `GET /api/capabilities` это показывает, и Studio
должна выключить соответствующую кнопку.

## `GET /api/capabilities` (GPU API)

Возвращает то, что реально установлено (узлы ComfyUI + файлы моделей + переменные окружения API),
кешируется на 60 с. Mac Studio отдаёт этот же ответ в `/api/health` как `gpu_capabilities`,
а список `style_adapters` там уже урезан до реально установленных LoRA.

Поля: `style_adapters`, `motion_control`, `camera_guide.available`, `camera_guide.state_versions`,
`scene_guides`, `cache_modes`, `aspect_ratios`, `qualities`, `megapixels`, `duration_seconds`,
`steps`, `ref_image_size`, `limits`, `roles`, `ab_v1` (только для A/B-харнесса),
`camera_words_v1` — словарь официальных слов камеры из `app/camera_words.py`
(`movements` {движение: [направления, "" = без направления]}, `aliases`, `speeds`, `amplitudes`,
`no_modifiers` — движения без скорости/амплитуды). Studio берёт список отсюда, а не держит копию.

## Версия контракта (`api_contract`)

`app/contract.py` `API_CONTRACT`; Mac Studio шлёт его как `contract_version` и отказывается работать с более
старым GPU API. Повышается при каждом новом поле формы или смене смысла поля.

- **5** — поле `allow_short_motion_reference`. В отпечаток `client_job_id` попадает только при `true`,
  поэтому отпечатки уже принятых заданий не меняются.

A/B-поля `POST /api/jobs` (`ab_variant`, `ab_scheduler`, `ab_lora`, `ab_lora_strength`,
`ab_sparse_attention`, `ab_director_plan_json`) Studio не отправляет; без них граф и отпечаток задания
прежние. Описание и проверки: [H3_AB_HARNESS.md](../guides/H3_AB_HARNESS.md).

## Настройки → узлы

| Studio (settings) | Поле API | Что в графе | Установка на сервере |
|---|---|---|---|
| всегда | — | `UNETLoader` Ref2VA BF16, `CLIPLoader` Qwen3-VL-32B BF16, 2× `VAELoader`, `MiniMaxH3ReferenceToVideo`, `KSamplerSelect` res_multistep, `BasicScheduler`, `BasicGuider`, `SamplerCustomAdvanced`, `VAEDecode`, `VAEDecodeAudio`, `CreateVideo`, `SaveVideo` | базовый deploy (модели с SHA-256) |
| файлы (картинка/видео/аудио) | `images`, `videos`, `audios` + `manifest_json` | `LoadImage`, `LoadVideo`+`GetVideoComponents`, `LoadAudio` | ComfyUI core |
| роль файла (14 ролей) | `manifest_json` (`role`, `owner`) | текст: Qwen Director назначает роль и пишет контракт в промпт | Qwen Director (vLLM) |
| `aspect_ratio`, `quality`, `megapixels`, `duration` | те же | `width/height/length` у `MiniMaxH3ReferenceToVideo` (кадры 17n+5, 124–362) | — |
| `steps` (20–50) | `steps` | `BasicScheduler.steps` | — |
| `seed` | `seed` | `RandomNoise.noise_seed` | — |
| `ref_image_size` | то же | вход `ref_image_size` | — |
| `use_video_audio` | то же | аудиодорожки `GetVideoComponents` → `ref_video_audios` | — |
| `cache_mode` safe/fast/off | `cache_mode` | `ApplyMiniMaxH3FirstBlockCache` (off — узла нет) | FirstBlockCache `f7a2712…` |
| `style_adapter` / режим «3D-мультфильм» | `style_adapter`, `mode` | `LoraLoaderModelOnly` + триггер `3dsrx` | LoRA `3dsrx` (SHA-256) |
| `camera_state` (+ `camera_optics`) | `camera_state_json` | по умолчанию только текст траектории `compile_projected`; видео-гид `H3CameraGuide` / `H3OperatorCamera` — при `H3_CAMERA_GUIDE_VIDEO=1` (`app/graph.py`) | camera guide `8460999…` + vendor v3 |
| ключевые кадры сцены | `guide_frames_json` (план Director) | `MiniMaxH3AddGuide` | ComfyUI core |
| движение из видео | `videos` (роль «Движение и камера») | нативный видео-референс Ref2VA; ControlNet/SDPose/RT-DETR/DA3 удалены из кода 2026-10-04, `control_type` принимает только `none` | не ставится |
| `emotional_direction` | то же | контракт игры в промпте (Qwen) | Qwen Director |
| «Короткое видео движения» (контракт 5) | `allow_short_motion_reference` (`true`/`false`, по умолчанию `false`) | видео с ролью «Движение» / «Движение и камера» короче ролика принимается как есть; без поля — 422 «Видео «Движение» длится … с» | — |

## Камера из Studio

- Studio шлёт `camera_state` в формате CameraGuide v2 (`director-camera.mjs`) и `camera_optics`
  (формат сенсора, ISO, выдержка, режим фокуса).
- Mac worker превращает это в CameraGuide v3: высота сенсора (full frame 24 мм, Super 35 18,66 мм),
  ISO, угол затвора, `focus_mode`; FOV пересчитывается из фокусного расстояния и сенсора.
- GPU API проверяет траекторию (`validated_client_camera`: формат узла, ≤ 12 ключей, не дальше конца
  ролика) и **использует её вместо траектории, которую предложил бы Qwen**: явный выбор пользователя главнее.
- Траектория камеры + видео с ролью «Камера» в одном задании → 422 с понятной ошибкой.
- Камера занимает один из трёх видеослотов H3.

## Как добавить новую кнопку

1. Поле в `settingsSnapshot()` (Studio) **и в `SETTINGS_KEYS` (`local_studio/store.py`)** — неизвестный
   ключ settings теперь 422 «Неизвестные параметры генерации: …» → передать в `local_studio/worker.py` (`data`).
2. `Form(...)` в `app/main.py` + проверка значения (ошибка → 422).
3. Узел в `app/graph.py`; его класс — в `required_nodes_for_plan`, файл модели — в `missing_model_files`.
4. Установка узла/модели с закреплённым коммитом или SHA-256 в `scripts/install_h3_h200_overlay.sh`
   и проверка класса узла в конце установки.
5. Отразить в `capabilities_from_nodes`, тест в `tests/`.
6. Политика качества: ничего, что снижает качество (см. `AGENTS.md`).

## Очередь Mac Studio: сверка и отмена

- `needs_reconciliation` (H3 и побочные задания: улучшение ролика, звук, ключевой кадр) больше **не держит
  очередь**: диспетчер раз в 30 с ищет такое задание на GPU по `client_job_id` (повторно не отправляет) и
  через 2 ч закрывает его как `failed` — и при выключенном GPU тоже.
- Побочное задание получает внеочередной проход, только пока оно реально на GPU
  (`uploading/queued_gpu/running/downloading`).
- Отмена без глобальной блокировки: `POST /api/jobs/{id}/cancel` (`queued_local`, `needs_reconciliation` → `cancelled`;
  `queued_gpu/running` → `stopping`), `POST /api/upscale/jobs/{id}/cancel`, `POST /api/audio/jobs/{id}/cancel`,
  `POST /api/keyframes/jobs/{id}/cancel` (любое активное состояние → `cancelled`; у побочных заданий на GPU
  отмены нет — GPU может доделать, результат не скачивается). 404 — нет задания, 409 — уже завершено.
- Потерянное на GPU (`unknown`+`lost`, или 404 три опроса подряд — новый сервер) → `failed`; скачивание
  результата — не больше 5 ошибок подряд или 30 мин без связи.

## Дополнительные поля (из бывшего docs/README, журнал 2026-10-05)

| Поле / роль | Где в задании | Значения | Возможность в `/api/capabilities` |
|---|---|---|---|
| роль видео **«Продолжение»** | `reference_roles[asset_id]` | одно видео, последним среди видео; не упоминать его `<Video N>` в промпте | `continuation_v1: true` |
| `guide_frames` | `settings.guide_frames` | `[[номер фото «Кадр сцены», frame_idx]]`; `-1` = последний кадр; с «Продолжением» — не раньше кадра 22 | `scene_guides` |
| `camera_effect` | `settings.camera_effect` | `art_is_explosion`, `blooming_flowers`, `bullet_time`, `dark_magic`, `fire_breath`, `four_seasons`, `kiss_camera`, `spiral_ascent`, `storm_magic`, `truman_show`; показывать только из `available` | `camera_effects_v1: {available, known}` |
| `color_match` | `settings.color_match` | прежний `{picture, strength, method}`; теперь рендерится H3ColorGrade на весь клип | `color_match_v1` (= H3ColorGrade) |
| `camera` сценариста | тело `POST /api/scenarist` | `{movement, direction?, speed?, amplitude?, shot?}` или список; movement: push, pull, zoom, pan, truck, tilt, pedestal, arc, tracking, static, shake, roll, pov (+ push_in, pull_out, orbit…); speed slow/normal/fast; amplitude small/medium/large | — |

Ошибки по-русски приходят 422 (Mac: `ValueError` при создании задания; GPU: при отправке). Для «Продолжения»
GPU проверяет длину клипа (≥ 22 кадров при 24 к/с). Варианты A/B (`ab_resolution=768p`, `ab_cache=off`,
`ab_camera_words=on`, `ab_color_match=colormatch_v2`, апскейл `ab_seedvr2_model=7b`) Studio не отправляет —
только `scripts/h3_ab.py` ([H3_AB_HARNESS.md](../guides/H3_AB_HARNESS.md)). Официальные эмбеддинги камеры теперь включены по умолчанию
после разрешения 2026-10-05 (`H3_CAMERA_EFFECTS=0` их отключает); SeedVR2 7B — `H3_SEEDVR2_7B=1`. Контракт API — 5 (`app/contract.py`): Mac этой ветки
требует обновлённый GPU API (`scripts/deploy_h3_new_server.sh`).
