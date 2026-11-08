# Новый GPU-сервер H3: от нуля до первого ролика

Единственный источник установки — этот репозиторий (`~/Documents/ChatGPT/Контент`).
Копия `~/Desktop/H3_READY_2026-09-29/...` слита сюда 2026-10-03 и больше не используется для деплоя.

## 0. Что нужно

| | Минимум | Проверено |
|---|---|---|
| GPU | 1× NVIDIA ≥ 96 ГБ VRAM, драйвер с CUDA ≥ 12.8 | RTX PRO 6000 Blackwell Server 96 ГБ (лимит 450 Вт), H200 141 ГБ |
| Диск | ≥ 500 ГБ на инстанс: образ Vast ~117 ГБ + `/workspace` ~220 ГБ (с аудиостеком и «Ключевым кадром» 2511; профиль 2.1 ещё +32,5 ГБ) | 331 ГБ не хватило на шаге «Актёрская игра» (2026-10-04) |
| ОЗУ | ≥ 256 ГБ (веса держатся в page cache между заданиями) | 566 ГБ |
| ОС | Ubuntu/Debian, root по SSH | Vast.ai |
| Доступ | SSH-ключ `~/.ssh/id_ed25519`; токен Hugging Face не нужен (модели открытые), `HF_TOKEN` — по желанию | |

Качество не обсуждается: полный BF16 Ref2VA, без Turbo, без квантизации, шаги и разрешение — как в Studio.

## 1. Одна команда (с Mac, из корня репозитория)

```bash
bash scripts/h3_new_server.sh --host root@HOST --port PORT
```

`--from` — старый рабочий сервер (если он ещё жив). Что делает команда, по шагам; на любой ошибке останавливается:

1. `scripts/check.sh` — тесты и синтаксис пакета.
2. `scripts/h3_server_drift.sh` против старого сервера. Если там есть правки, которых нет в репозитории
   (строки «ПОТЕРЯ»), установка не начинается: сначала перенести их в репозиторий.
3. `scripts/deploy_h3_new_server.sh --apply` — ComfyUI с закреплённой сборкой (torch 2.11.0+cu128),
   веса с проверкой SHA-256, Qwen Director (vLLM 0.15.0 cu128 + патч сна) и второй, неактивный
   сценарист Qwen3.8-27B-FP8 (свой venv с vLLM 0.19.1, см. «Сценарист: два профиля»), узлы H3 (камера, cache,
   color grade, film look, уточнение персонажа; готовые KJNodes, VideoHelperSuite,
   Frame-Interpolation/RIFE), GPU API `127.0.0.1:18189`, watchdog. Затем переводит
   туннель Mac на новый хост и перезапускает Studio. Отказывается, пока в очереди Mac есть незавершённые
   задания; при сбое туннеля откатывает его.
4. `scripts/install_h3_acting.sh` — «Актёрская игра»: LivePortrait (ревизия `9b294b3`) в своём venv
   (torch 2.11.0+cu128, точные версии пакетов с рабочего сервера), веса human + animals (~1,6 ГБ,
   размер сверяется с Hugging Face), CUDA-модуль XPose под архитектуру карты, smoke-тест обоих конвейеров.
5. Режим Qwen: по умолчанию `--qwen sleep` — Qwen спит в памяти (~1,3 ГБ), пробуждение за 0,3 с, веса
   ComfyUI между заданиями не выгружаются (решение 2026-10-04). `--qwen guard` — сторож останавливает Qwen
   на время рендера: +~1 мин 40 с к каждому заданию.
6. `scripts/verify_h3_server.sh` — ничего не рендерит; должно быть «ИТОГ: всё OK».

Очередь, референсы, MP4 и настройки Studio (`~/Library/Application Support/H3 Studio`) не трогаются:
Studio сразу работает со всеми пресетами, проектами и историей. Генерация не запускается.
Повторная установка поверх уже настроенного сервера: добавить `--skip-base`.

## 2. Сверка и проверка по отдельности

```bash
bash scripts/h3_server_drift.sh --host root@HOST --port PORT
```
Сравнивает живой сервер с репозиторием. «ПОТЕРЯ» — код, который есть только на сервере (его нужно
перенести в репозиторий до переезда). «НЕ-ВЫЛОЖЕНО» — правки репозитория, которых на сервере ещё нет.
Запускать после любой правки прямо на сервере.

```bash
bash scripts/verify_h3_server.sh --host root@HOST --port PORT
```
Каждая строка из раздела «Ловушки» ниже проверяется здесь автоматически, в том числе
«весь код GPU API совпадает с Mac».

## 3. Что нужно заранее

- SSH-ключ `~/.ssh/id_ed25519` добавлен в аккаунт Vast до аренды.
- Токен Hugging Face не нужен: все веса открытые (`HF_TOKEN` можно задать по желанию).
- Очередь Studio на Mac пуста (нет заданий в работе).

## 4. Первый ролик

Короткий (5 с) прогон из Studio, затем рабочий. Ориентиры на RTX PRO 6000 (10 с, 672×1152, 24 шага, cache safe):

| Этап | Время |
|---|---|
| Qwen: пробуждение | 0,3 с |
| Qwen: план (≈2,5 тыс. токенов, 83 ток/с) | ~30 с на попытку |
| Загрузка весов из RAM до первого шага | ~20 с (первый запуск после рестарта ComfyUI — ~3 мин, чтение 66 + 51 ГБ с диска) |
| Шаг сэмплера | ~29 с; cache safe пропускает часть поздних шагов (~0,6 с) |
| Сэмплинг целиком | ~8 мин |

Во время шага: GPU 100 %, ~450 Вт (лимит карты), ComfyUI ~94 ГБ VRAM, спящий Qwen ~1,3 ГБ, PCIe ≈ 0.
Если PCIe растёт, а GPU ниже 100 % — веса не помещаются в VRAM, ищите, кто ещё держит память.

### Из чего складывается время шага

Время шага определяется длиной последовательности токенов (внимание растёт ~квадратично):
кадр ролика W×H даёт (W/16)·(H/16) токенов на кадр латента (10 с = 61 кадр латента); видео-референс
— столько же по своему размеру; каждое фото — один раз. Пример 768×1344, 10 с: ролик 245 952,
гид камеры 256×448 — 27 328, два фото 1024×1536 — 12 288. Неподвижная камера, pan/handheld/tracking,
цветокор и film look токенов не добавляют.

## 5. Ловушки (2026-10-03 и 2026-10-04) — что было, почему, где закрыто

| Симптом | Причина | Исправление | Где проверяется |
|---|---|---|---|
| Шаг 65 с вместо 29 с, ролик ~30 мин | Studio при неподвижной камере всё равно прикладывала видеогид на всю длину ролика → токенов вдвое больше | `graph.py`: неподвижная камера идёт текстом (объектив/диафрагма/фокус сохраняются); `H3_STATIC_CAMERA_GUIDE=1` возвращает старое | `verify_h3_server.sh`, `tests/test_graph.py` |
| Наезд/облёт: шаг 84 с на 768×1344 | Видеогид камеры 512×896 кодируется в своём размере и добавлял 30 % токенов (109 тыс. из 367 тыс.) | `graph.py: camera_guide_size` — гид 256×448 (−22 % последовательности), кадр ролика без изменений; `H3_CAMERA_GUIDE_SHORT_EDGE=512` — откат | `verify_h3_server.sh`, `tests/test_graph.py` |
| Спящий Qwen держит 18,5 ГБ | vLLM 0.15.0 `gpu_worker.py`: `with pool(tag="weights") and set_config()` — из-за `and` веса грузятся мимо пула сна | Установщик Qwen патчит строку на `,`; после — 1,3 ГБ во сне, пробуждение 0,3 с | `verify_h3_server.sh` (патч + фактическая память) |
| Qwen в цикле падений после рестарта | vLLM по умолчанию требует 90 % VRAM свободными, а прогретый ComfyUI держит 55 ГБ | `H3_QWEN_GPU_UTIL=0.30`, `H3_QWEN_MAX_MODEL_LEN=32768` в `deploy/h3-qwen-director.conf` | `verify_h3_server.sh` |
| Ролик с диалогом вышел бы немым | Qwen ставил `audio_intent=silent` | `director.py`: если в тексте речь/звук/музыка, `silent` заменяется на нативный звук | `tests/test_director.py` |
| Промпт растёт при каждом повторе (12,5 → 17 тыс. символов) | В текст задания попадал уже собранный промпт, и Studio добавляла блоки ещё раз | `store.py: strip_generated_blocks`, `app.mjs`: повтор по исходному промпту | `tests/test_local_store.py` |
| Каждое задание падает «ответ не по JSON-контракту» | vLLM не применяет схему (сырой ответ — другая структура) | Запасной путь `director_fallback`: рендер по промпту Studio с теми же seed/размером/шагами/референсами; починка схемы — отдельная задача | `verify_h3_server.sh` |
| vLLM не стартует: `libcudart.so.13` | `--torch-backend=auto` тянул vLLM 0.30 + CUDA 13 на драйвере 12.8 | `h3_gpu_env.sh`: vLLM 0.15.0 cu128 | установщик |
| Сторож не установлен на новом сервере | `h3_runtime_watchdog.py` был только в копии на рабочем столе | слит сюда (`deploy/h3-watchdog.conf`) | `verify_h3_server.sh` |
| Правки, сделанные прямо на сервере, теряются при переезде (2026-10-04: проверка траектории камеры и кэш `/api/capabilities` в `main.py` были только на сервере) | код копировался на сервер руками, обратно в репозиторий не попадал | `h3_server_drift.sh`; `h3_new_server.sh --from` не ставит новый сервер, пока есть «ПОТЕРЯ» | `verify_h3_server.sh`: отпечаток всего `app/*.py` |
| +~1 мин 40 с к каждому заданию | сторож Qwen (`H3_QWEN_AUTOMANAGE=1`) останавливает Qwen на рендер: холодный старт Qwen ~50 с + ComfyUI выгружает и заново грузит веса (72 с вместо 19) | `h3_new_server.sh --qwen sleep` — режим по умолчанию с 2026-10-04 | `verify_h3_server.sh` |
| Наезд: персонаж теряет костюм с фото | видеогид-манекен модель воспринимала как видео-референс с персонажем | движение камеры по умолчанию текстом; `H3_CAMERA_GUIDE_VIDEO=1` — вернуть видеогид | `tests/test_graph.py` |
| Фото не привязаны к персонажам | в режиме режиссуры эмоций на сервер уходил текст без блоков «Picture N = ИМЯ» | `c55e52f`; в Studio на каждом фото роль «Персонаж целиком» и владелец | `tests/` |
| Видео «Движение»: шаг ~2× дольше | Ref2VA кодирует видео-референс в его размере (до 768×1344) | `video_reference.py`: «Движение» и «Движение и камера» 512p, «Камера»/«Ритм сцены» 256p; `H3_MOTION_REF_SHORT_EDGE=0` — полный размер | `verify_h3_server.sh` |
| «Не удалось прочитать кадры видео IMG_xxxx.MP4» | у MP4 с iPhone индекс в конце файла, поток его не видит | `director.py` читает кадры из временного файла (`77dbe5b`) | `verify_h3_server.sh`, регрессионный тест |
| «Актёрская игра» недоступна на новом сервере | LivePortrait ставили вручную, ни один установщик его не содержал | `scripts/install_h3_acting.sh` (шаг 4 единой команды) | `verify_h3_server.sh`: `/api/acting/capabilities` human + animal |
| Восстановленный снимок сломается, если Vast обновит `vastai/base-image:cuda-12.8.1-auto` | venv ComfyUI создавался с `--system-site-packages` и молча брал ~100 пакетов (numpy, pillow, requests, …) из системного Python образа | venv без системных пакетов + закреплённые `comfy-venv-requirements.txt`; digest образа пишется в `SNAPSHOT.txt` | `h3_comfy_venv_isolate`: `pip check` + импорт; локальный стенд |
| `h3-api` перезапущен посреди рендера | проверка очереди не блокировала рестарт | рестарт только если `queue_running` пуст — команда должна выходить с ошибкой иначе | правило в `AGENTS.md` |

## Сценарист: два профиля

> **2026-10-09:** профиль 27B исключён из восстановления и overlay (`6eb8c55`), `qwen-next-venv` на сервере удалён.
> Активен только 8B. Раздел ниже — справка на случай возврата 27B по решению пользователя.

Установщик ставит оба варианта, активен прежний 8B — пока A/B на сервере не подтвердит 27B (у гибридных
моделей в новых vLLM есть известные проблемы сна/пробуждения, vLLM issue #48310).

| Профиль | Модель (Apache-2.0) | vLLM / venv | VRAM | Рядом с рендером H3 |
|---|---|---|---|---|
| `default` | Qwen/Qwen3-VL-8B-Instruct | 0.15.0 cu128 + патч сна, `qwen-venv` | 0.30 (~28 ГБ), во сне ~1,3 ГБ | да |
| `qwen3.8-27b-fp8` | Qwen/Qwen3.8-27B-FP8 @`017b9c7a` (гибрид Gated DeltaNet + attention, VLM) | 0.19.1, `qwen-next-venv` | 0.45 (~43 ГБ из 96), веса 31 ГБ | нет: просыпается только при пустой очереди ComfyUI и выгруженной H3 |
| `qwen3.6-27b-fp8` | Qwen/Qwen3.6-27B-FP8 @`e89b16eb` (та же архитектура) | 0.19.1, `qwen-next-venv` | как выше | нет |

- **Установка**: `install_h3_h200_overlay.sh` после `install_h3_qwen_director.sh` запускает
  `install_h3_scenarist_next.sh`: venv `/workspace/h3/qwen-next-venv` (`uv pip install --torch-backend=cu128
  vllm==0.19.1`), снимок модели с закреплённой ревизией в `hf-cache`, SHA-256 каждого LFS-файла против
  `deploy/scenarist-models/<профиль>.sha256`. Ничего не активирует и не перезапускает. Нужно ~44 ГБ диска:
  `H3_SCENARIST_27B=0` — не ставить, `=1` — обязательно; без переменной ставится, если хватает места.
  Второй профиль 3.6: `H3_SCENARIST_NEXT_PROFILE=qwen3.6-27b-fp8 bash /workspace/h3/scripts/install_h3_scenarist_next.sh`.
- **Почему vLLM 0.19.1**: архитектура `Qwen3_5ForConditionalGeneration` есть с 0.17.0 (рецепт
  recipes.vllm.ai/Qwen/Qwen3.8-27B: «0.17.0+»); в 0.19 — исправления Qwen3.5 GDN-квантованных моделей и FP8,
  SM120 blockwise-FP8 CUTLASS GEMM, утечка памяти режима сна. Упаковка та же, что у проверенной 0.15.0:
  колесо PyPI с ядрами CUDA 12.9 (sm_120 есть) на torch 2.10.0 из индекса cu128 — работает там же, где 0.15.0
  (драйвер ≥ 12.8; драйвер 595 поддерживает CUDA 13.x). С 0.20 по умолчанию torch 2.11 + CUDA 13 (нужен
  драйвер ≥ 580), а на GitHub есть только вариант cu129 — без нужды не переходим. Патч сна 0.15 не нужен:
  в 0.16+ `load_model` входит в оба контекста (установщик это проверяет).
- **Флаги 27B** (`scripts/run_h3_qwen_director.sh`): `--revision <sha>`, `HF_HUB_OFFLINE=1`,
  `--enable-sleep-mode`, `--gpu-memory-utilization 0.45`, `--max-model-len 32768`, `--max-num-seqs 4`,
  `--limit-mm-per-prompt {"image":24,"video":0}`, `--reasoning-parser qwen3`,
  `--default-chat-template-kwargs {"enable_thinking":false}`, тот же xgrammar `--structured-outputs-config`.
  API дополнительно шлёт `chat_template_kwargs: {"enable_thinking": false}` в каждом запросе (парсер qwen3
  читает именно параметры запроса); сценарист ещё `top_p 0.8, top_k 20` (карточка модели, без
  `presence_penalty 1.5` — он штрафует повторяющиеся метки `<Subject N>`). Из рецепта выключены до проверки
  на GPU: `H3_QWEN_KV_FP8=1` (`--kv-cache-dtype fp8`: #48310 SW3 — сброс KV-масштабов при пробуждении,
  #37618 — точность FP8-KV у Qwen3.5), `H3_QWEN_MTP=1` (`--speculative-config {"method":"mtp",...}`),
  `H3_QWEN_ENFORCE_EAGER=1` (меньше VRAM во сне: CUDA-графы во сне не освобождаются, #47700).
- **Сон**: `H3_QWEN_SLEEP_LEVEL=1` (по умолчанию; веса в RAM хоста — нужно ~31 ГБ свободной RAM) или `2`
  (веса выбрасываются; пробуждение = `wake_up?tags=weights` → `collective_rpc reload_weights` →
  `wake_up?tags=kv_cache`, дольше). Если сон/пробуждение 27B ломает ответы — режим `--qwen guard`
  (полная остановка/запуск сервиса вокруг рендера, `H3_QWEN_START_TIMEOUT=900`) или профиль `default`.
- **Переключение без переустановки** (на сервере, при пустой очереди ComfyUI):
  `bash /workspace/h3/scripts/h3_scenarist_switch.sh --profile qwen3.8-27b-fp8|qwen3.6-27b-fp8|default`
  (`H3_QWEN_GPU_UTIL`, `H3_QWEN_SLEEP_LEVEL` — по желанию). Держит блокировку плана, меняет окружение
  `h3-qwen-director.conf` (PATH, бюджет, профиль; `scripts/h3_scenarist_conf.sh`) и `h3-api.conf`,
  перезапускает h3-qwen-director, ждёт `/health` и модель в `/v1/models`, делает короткий запрос и два
  цикла сна/пробуждения с тем же ответом, оставляет спящим. При любой ошибке возвращает оба конфига и
  прежний профиль. Повторная установка overlay сохраняет активный профиль.
- **Память**: и Director (`main.py`), и сценарист (`/api/scenarist`) сначала вызывают
  `comfy.release_models_if_idle()` (очередь пуста → `/free`). Для 27B `LocalQwenDirector` перед каждым
  `wake_up` под блокировкой плана ещё раз проверяет очередь и выгружает модели ComfyUI; занят — 409/запасной
  путь без плана, Qwen не просыпается. `/api/capabilities` → `scenarist_v1` (профиль, модель, ревизия, vLLM,
  бюджет, `coexists_with_render`, подсказка).
- **A/B**: `python3 scripts/h3_scenarist_ab.py --only 8b=http://127.0.0.1:18189`, переключить профиль,
  `--only 27b=...`, затем `--compare 8b 27b` (или `--a`/`--b` для двух серверов). 20 русских брифов
  (`scripts/data/scenarist_ab_briefs.json`, включая кино-ограбления и оружие): валидный формат %, проблем на
  промпт (`app/h3_ir.validate_ir`), отказы, ошибки, секунды. Результаты — `test-results/scenarist-ab/`.
- **Сверка**: `verify_h3_server.sh` показывает активный профиль, проверяет совпадение профиля у Qwen и API,
  vLLM 0.19.1 и веса 27B (если установлены), порог памяти во сне 3 ГБ для 8B и 8 ГБ для 27B (уточнить на GPU);
  `h3_server_drift.sh` считает конфиг профиля «OK», если он совпадает с `h3_scenarist_conf.sh`;
  `h3_server_lock.sh` снимает и `qwen-next-venv` (эталон пакетов появится после первой установки).

## 6. Обслуживание

- Рестарт сервисов — только при пустой очереди ComfyUI (`curl -s 127.0.0.1:18188/queue`).
- Не трогать веса на сервере и `~/Library/Application Support/H3 Studio` на Mac.
- Python — только в venv (`/workspace/h3/venv`, `api-venv`, `qwen-venv`, `qwen-next-venv`, `liveportrait-venv`). Новый пакет для
  ComfyUI — с точной версией в `comfy-venv-requirements.txt` (системного Python venv не видит).
- Перед правкой — `git status`; с Codex делим файлы через журнал.

## 7. Что ставится на сервер (опись рабочего сервера 2026-10-04)

| Компонент | Ревизия / размер | Кто ставит |
|---|---|---|
| ComfyUI | `b5cc8830`, torch 2.11.0+cu128 | vendor base |
| FirstBlockCache | `f7a2712` | overlay |
| h3-3D-camera-guide | `8460999` + файлы `app/camera_guide_vendor` | overlay |
| h3_native_helpers, h3-color-grade, h3-character-refine | из репозитория | base / overlay |
| ComfyUI-KJNodes (GPL-3.0; ColorMatch, узлы MiniMax H3 для A/B) | `d3cfe21` | overlay |
| ComfyUI-VideoHelperSuite (GPL-3.0; VHS_LoadVideo/VideoCombine/SelectImages) | `4d907be` | overlay |
| ComfyUI-Frame-Interpolation (MIT; RIFE VFI) + `ckpts/rife/rife417.pth` (21,5 МБ, SHA-256 в установщике; rife426 из релиза не грузится узлом) | `26545cc` | overlay |
| пакеты этих узлов (color-matcher + зависимости, `--no-deps`, cv2/numpy/torch не трогаются) | `vendor/minimax-h3-local-studio/assets/comfy-extra-nodes-requirements.txt` (12 пакетов) | overlay |
| Ref2VA BF16 | 66,3 ГБ | vendor base |
| Текстовый энкодер Qwen3-VL-32B BF16 | 51,5 ГБ | vendor base |
| Видео-VAE fp16 / аудио-VAE fp32 | 5,2 ГБ / 0,6 ГБ | vendor base |
| LoRA 3dsrx (style3d) | 0,3 ГБ | overlay |
| SAM2.1 hiera large (уточнение персонажа) | 0,9 ГБ, SAM2 `2b90b9f` | overlay |
| Qwen3-VL-8B-Instruct (режиссёр), vLLM 0.15.0 cu128 + патч сна | 17 ГБ | install_h3_qwen_director |
| Qwen3.8-27B-FP8 (второй сценарист, не активен), vLLM 0.19.1 в `qwen-next-venv` | 31 ГБ + ~12 ГБ venv, ревизия `017b9c7a`, SHA-256 в `deploy/scenarist-models/` | install_h3_scenarist_next (overlay; `H3_SCENARIST_27B=0` — пропустить) |
| LivePortrait human + animals | 1,6 ГБ + venv | install_h3_acting |
| «Ключевой кадр»: Qwen-Image-Edit-2511 fp8mixed (Apache-2.0) + Qwen2.5-VL-7B fp8_scaled + VAE Qwen-Image | 20,5 + 9,4 + 0,25 ГБ, ревизии HF и SHA-256 в установщике; VRAM ≈ 30–35 ГБ, только между заданиями H3 | overlay |
| «Ключевой кадр», профиль Qwen-Image 2.1 (bf16 DiT + Qwen3-VL-8B bf16 + VAE; **Qwen Research License, некоммерческая**) | 14,2 + 17,5 + 0,7 ГБ, только с `H3_KEYFRAME_QWEN_IMAGE_21=1` | overlay |
| venv ComfyUI без системных пакетов | `vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt` (21 пакет, версии снимка 2026-10-04) | vendor base + overlay (`h3_comfy_venv_isolate`) |
| Сервисы supervisor | h3-comfyui, h3-api, h3-qwen-director, h3-watchdog (+ h3-qwen-vram-guard в режиме guard); gradio `h3-git-studio` (:18190) установщик удаляет | все установщики |

На Mac ничего не ставится: Студия (`com.minimax.h3-mac-studio`, порт 18187) и туннель (`com.minimax.h3-tunnel`,
порты 18188 и 18189) уже есть, единая команда только переводит туннель на новый адрес.
ComfyUI запускается без `--enable-cors-header`: к нему обращается только GPU API.
