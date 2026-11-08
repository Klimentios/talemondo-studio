# A/B задания Studio на новом GPU-сервере

Одна команда отправляет одно и то же задание (seed, размер, шаги, промпт, фото) в нескольких
вариантах, ждёт рендеры, скачивает MP4 и собирает страницу, где ролики идут синхронно рядом.
Обычные задания Studio не меняются: A/B-поля шлёт только `scripts/h3_ab.py`.

## День первый

Сервер установлен (`scripts/h3_new_server.sh … → «ИТОГ: всё OK»`), туннель на Mac работает,
`app/` на сервере из ветки с этим харнессом (в `/api/capabilities` есть `ab_v1`).

```bash
# 0. (по желанию) realism LoRA на сервер — без неё варианты realism-* честно отклоняются 422
#    ssh … 'wget -q -O /workspace/h3/ComfyUI/models/loras/h3-realism-people-t2v-i2v-r2v.safetensors \
#      https://huggingface.co/fal/MiniMax-H3-Realism-People-LoRA/resolve/main/h3-realism-people-t2v-i2v-r2v.safetensors'
# 1. проверить, что уйдёт на сервер (без запросов)
bash scripts/h3_ab.sh --job <ID задания> --dry-run
# 2. A/B: выбранное задание из Studio, 5 вариантов (+ --sparse — шестой)
bash scripts/h3_ab.sh --job <ID задания> --sparse 2>&1 | tee test-results/ab.log
open test-results/ab-*/index.html     # последняя папка
```

Параметры: `--gpu-url` (по умолчанию туннель `http://127.0.0.1:18189`), `--job ID|префикс`
(ваше задание из Studio; база Studio читается только на чтение, файлы сверяются по SHA-256),
`--golden` (фикстура `tests/golden/pictures_studio_job.json` с картинками 1×1 — для стенда),
`--variants baseline,beta,…`, `--tag`, `--merge ПАПКА`, `--poll`, `--timeout`.

Время: варианты идут в общей очереди ComfyUI одним FIFO; 6 вариантов задания
(например, 15 с, 25 шагов) — это 6 полных рендеров. Mac Studio в это время лучше не ставить новые задания.

## Набор качества (2026-11-02)

Цель — найти, что поднимает качество на наших bf16-весах без turbo, квантизации и урезания шагов.
Победитель становится умолчанием только после «да» пользователя (принятый рецепт пользователя — только через A/B).

```bash
python3 scripts/h3_ab.py run --job <ID картинки> --variants quality --tag quality-pictures
python3 scripts/h3_ab.py run --job <ID замены по видео> --variants quality --tag quality-swap
```

`quality` = baseline, hires (два прохода), er-sde-beta (сэмплер шаблона Character Swap), shift-8 и
shift-6 (`MiniMaxH3SigmaShift`: меньше — шаги к деталям, больше — к крупному движению; модель по
умолчанию 12/3), hires-er-sde-beta. Сервер без `ab_v1.shift` в `/api/capabilities` отклоняется до
отправки: старый GPU API молча проигнорировал бы `ab_shift` и показал бы baseline под чужим именем.
Проверено на стенде без весов: все 6 графов собираются и проходят проверку ComfyUI.

## Склейки «Продолжения» (2026-11-02)

ComfyUI-H3-LongTake (MIT) замерил скачок на стыке клипов: 5,4–6,8× обычной смены кадра, когда хвост прошлого
клипа идёт ориентиром (keyframe), до 19× с 22 кадрами контекста, и 1,5–1,7×, когда хвост копируется в латент
под маской (inpaint). Наше «Продолжение» — это keyframe с 22 кадрами (`app/continuation.py`).

```bash
python3 scripts/h3_ab.py run --job <задание с видео «Продолжение»> --variants continuation --tag seams
```

`continuation` = baseline (keyframe 22), anchor-keyframe5, anchor-inpaint5, anchor-inpaint22 (`ab_anchor`;
inpaint — узел `H3ContinuationInpaint` из `app/comfy_nodes/h3_continuation_anchor`, ставит overlay). Для
каждого готового варианта харнесс сам меряет скачок (`scripts/h3_seam_jump.py`: прошлый клип + новый без
первых 22/5 кадров; 1× — склейки не видно) и пишет его в отчёт. Сервер без `ab_v1.anchor` отклоняется до
отправки. Если победит вариант на 5 кадров, Studio нужно научить обрезать 5 кадров перекрытия
(`continuation_trim_frames` сейчас допускает только 22). Проверено на стенде без весов: все 4 графа приняты
ComfyUI и собраны верно; узел проверен в контейнере на поддельных VAE (5 кадров → 2 токена, 22 → 7, звук под маской).

## Набор замены героя (2026-11-04)

Один шот замены по исходному видео: 24 шага, один seed, 0,8 Мп. Нужен GPU с задеплоенным `app/` и установщиком. Установщик ставит V1.5, `h3-swap-mask` и SAM2.1.

```bash
python3 scripts/h3_ab.py run --job <ID замены> --variants swap --tag swap-kit \
  --swap-mask '{"performers": [{"name": "HERO", "box": [x, y, w, h]}]}'
```

| Вариант | Что меняется |
|---|---|
| `swap-v15` | Character Swap LoRA V1.5 вместо V1 |
| `ref-beta` | `res_multistep` / `beta` вместо `simple` |
| `src-480` | исходное видео перекодируется до 480 по короткой стороне, SHA в манифесте пересчитывается |
| `swap-mask` | маска исполнителей: перерисовываются только они (`swap_mask_json`) |
| `swap-mask-v15` | маска вместе с V1.5 |

Рамка `box` — исполнитель на **первом кадре** исходника в долях кадра: x, y, ширина, высота. Её видно через `asset_frames` или кадр из MP4. Принимать только по кадрам: лишние персонажи, шапка или костюм исходника, лицо героя на общем плане, стыки маски.

## Варианты

| Вариант | Поле формы `POST /api/jobs` | Что меняется в графе |
|---|---|---|
| `baseline` | `ab_variant=baseline` | ничего (граф = эталон `tests/golden`) |
| `beta`, `normal` | `ab_scheduler=beta\|normal` | `BasicScheduler.scheduler` (узел 124), по умолчанию `simple` |
| `realism-0.6`, `realism-1.0` | `ab_lora=realism_people`, `ab_lora_strength=0.6\|1.0` | узел 312 `LoraLoaderModelOnly` (после FirstBlockCache) + триггер в промпте |
| `sparse` (`--sparse`) | `ab_sparse_attention=sol-attn` | узел 313 `BlockSparseAttention` сразу после `UNETLoader` |
| `768p` | `ab_resolution=768p` | `width/height` узла 136 → 1344×768 / 768×1344 (официальный 768p по аспекту) |
| `cache-off` | `ab_cache=off` | узел 310 FirstBlockCache убран, модель идёт прямо в сэмплер |
| `camera-words` | `ab_camera_words=on` | legacy-промпт с траекторией: движение словами гайда (`app/camera_words.py`) вместо числового текста |
| `colormatch-v2` | `ab_color_match=colormatch_v2` | узел 339 `ColorMatchV2` (KJNodes, покадрово) вместо `H3ColorGrade`; нужен `color_match` в задании |
| `upscale-3b`, `upscale-7b` | `h3_ab.py upscale RUN_DIR --models 3b,7b` → `/api/upscale/jobs ab_seedvr2_model` | SeedVR2 3B / 7B fp16 на готовом baseline; 7B — только с `H3_SEEDVR2_7B=1` |

- **Realism LoRA** — `fal/MiniMax-H3-Realism-People-LoRA`, файл `h3-realism-people-t2v-i2v-r2v.safetensors`
  в `models/loras`. Триггер `r34l1sm`: по карточке модели — в начало промпта; в официальном
  шестиразделовом формате (`subject_definitions:` …) — в начало `detailed_description`, как у `style3d`.
  Сила 0.0–1.2 (1.0 — задуманная, 0.6–0.8 — мягче). Не совмещается со стилевой LoRA задания (422).
  Также разрешена `ab_lora=style3d` (наша 3D LoRA, триггер `3dsrx`).
- **BlockSparseAttention** — родной узел ComfyUI `b5cc8830` (`comfy_extras/nodes_sparse_attention.py`,
  экспериментальный) с его значениями по умолчанию: метод `sol-attn` (без обучения; `sla`/`vsa` требуют
  специальных весов и не предлагаются), `tau=1.3`, `start_percent=0.2`, `end_percent=1.0`,
  `dense_blocks=""`, `min_tokens=12288`, `extra_tokens=256`, `sink_conditioning=exact_kv_and_rows`
  (текст/референсы и аудио-строки H3 остаются плотными).
- Значения проверяются строго: неизвестный планировщик/LoRA/режим, сила вне 0–1.2 → **422**. Нет узла
  или файла LoRA → **422** до Qwen и до очереди (`missing_model_files`), с именем файла.
- Без `ab_*` полей граф, отпечаток задания и сводка Director прежние — `tests/test_render_golden.py`
  и `tests/test_ab_variant.py` это закрепляют.

### Один промпт на все варианты

Baseline идёт первым и планируется Qwen как обычное задание Studio. В ответе (`director.ab.director_plan`)
GPU API возвращает сырой план Qwen; остальные варианты отправляются с `ab_director_plan_json` и рендерят
**тот же план**: Qwen не просыпается рядом с идущим рендером (иначе GPU API отказал бы «ComfyUI занят»),
промпт, роли и guide-кадры совпадают. Если Qwen не смогла спланировать baseline, все варианты повторяют
тот же запасной маршрут (`{"director_fallback": …}`). Отчёт сверяет `base_prompt_sha256` (промпт до
A/B-изменений) и предупреждает, если он разный.

## Что получается

`test-results/ab-<дата-время>[-tag]/`:

- `NN-<вариант>.mp4`, `index.html` — ролики рядом: «▶ Все», «С начала», общий ползунок, звук первого;
- `report.md` — по варианту: статус, рендер (с), сэмплинг (с), **с/шаг** (фаза sampling / шаги),
  **пик VRAM**, `base prompt` и `graph` хеши, ссылка на MP4 или текст ошибки;
- `prompts/NN-<вариант>.txt` — точный промпт Ref2VA, `director_plan.json`, `results.json`.

Пик VRAM: для A/B-заданий GPU API на каждом опросе статуса читает `/system_stats` ComfyUI и хранит
максимум `vram_total − vram_free` (поле `vram` в `GET /api/jobs/{id}`). Это память всей карты (вместе
со спящей Qwen ~1,3 ГБ), снятая с шагом опроса (`--poll`, 10 с), а не `torch.cuda.max_memory_allocated`.
Рендер не затрагивается; для заданий Studio запрос не делается.

`graph_sha256` считается по графу с нормализованными именами загруженных файлов: одинаковый хеш = тот же
граф (seed, размер, промпт, узлы).

## Сравнение двух сборок ComfyUI

Сейчас ComfyUI закреплён в `vendor/minimax-h3-local-studio/scripts/deploy_h3_local_studio.sh`
(`comfy_commit=b5cc8830…`). Флага установщика для другой ревизии пока нет — установщики ведёт другой
агент. Предлагаемый флаг: `scripts/h3_new_server.sh … --comfy-ref SHA` (передаётся в deploy как
`comfy_commit`, после смены — повторная проверка узлов `/object_info` и `verify_h3_server.sh`).
До его появления — вручную, с пустой очередью ComfyUI:

```bash
bash scripts/h3_ab.sh --variants baseline --tag comfy-b5cc8830          # 1. эталон на текущей сборке
# 2. на сервере: git -C /workspace/h3/ComfyUI fetch && git -C /workspace/h3/ComfyUI checkout NEW_SHA
#    (локальный патч server.py переложить, как делает deploy_h3_local_studio.sh), supervisorctl restart h3-comfyui,
#    затем bash scripts/verify_h3_server.sh --host … --port … → «ИТОГ: всё OK»
bash scripts/h3_ab.sh --variants baseline --tag comfy-NEW --merge test-results/ab-…-comfy-b5cc8830
# 3. вернуть b5cc8830 (checkout + restart), если новая сборка хуже
```

Одинаковый `graph` хеш у двух baseline подтверждает, что различие — только в ComfyUI. Повторная сборка
отчёта: `bash scripts/h3_ab.sh report ПАПКА --merge ДРУГАЯ_ПАПКА`.

## Проверка без GPU

```bash
bash infra/h3_ab_stand.sh
```

Временный контейнер `h3-ab-stand` (образ `h3-local:base`, том `h3root` только на чтение, `app/` этой копии,
порты 38188/38189; стенд `h3-local` не трогается, контейнер удаляется):
1. настоящий ComfyUI `--cpu` без файла realism LoRA → `realism-*` получают 422, остальные графы
   (включая `BlockSparseAttention`) принимает валидация ComfyUI, рендер падает на пустых весах;
2. с пустой заглушкой LoRA в `/tmp` контейнера → все шесть графов приняты ComfyUI;
3. mock ComfyUI (`infra/ab_mock_comfyui.py`: реальный `/object_info`, проверка графа
   `scripts/h3_graph_validate.py`, события `/ws`, тестовые MP4) → отправка, опрос, скачивание,
   тайминги, VRAM, отчёт; затем повтор baseline с `--merge`.
