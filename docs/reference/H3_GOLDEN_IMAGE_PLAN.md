# Повторяемый restore H3 из готового образа и весов

Статус: **однокомандный restore реализован в `infra/h3_up.sh`; повторный полный запуск после аппаратной адаптации ещё нужен для приёмки.**
Сделано: снимок софта (`infra/h3_snapshot_pull.sh`), локальный стенд (`infra/h3_local.sh`),
самодостаточный venv ComfyUI, digest базового образа в `SNAPSHOT.txt`, сборка образа без GPU
(`infra/h3_image_build.sh`, раздел в конце), подъём из R2 или из папки снимка (`infra/h3_up.sh`).
Выгрузка в R2 и восстановление уже использовались. Перед новым трансфером теперь проверяются доступы,
диск и GPU; загрузка остаётся ограничена пропускной способностью хранилища и сети.

## Почему

Полный restore запускается одной командой `infra/h3_up.sh`: незакоммиченные изменения кода/тестов остаются
в исходной рабочей копии, развёртывание использует временную detached worktree последнего commit.
Далее идут предварительная проверка окружения, загрузка
и проверка хешей, распаковка софта и весов, установка только недостающего слоя, проверка Studio и туннеля.
Скорость копирования зависит от сети и объёма манифеста; фиксированный срок не обещаем.

## Что уже есть

| Что | Где | Состояние |
|---|---|---|
| Снимок софта без весов | `infra/h3_snapshot_pull.sh` → `~/H3-Snapshots/h3-<дата>/` | `h3-20261004-1826`: `software.tar.zst` 13,3 ГБ (zstd, ~52 с на сервере), `weights.tsv` (путь, размер, SHA-256), `lock/`, `SNAPSHOT.txt` |
| Эталон «один в один» | `scripts/h3_server_lock.sh` → `deploy/live-lock/` | пакеты каждого venv (только собственные пакеты venv), ревизии, веса |
| Локальный стенд | `infra/h3_local.sh` + `scripts/h3_graph_validate.py` | снимок в Docker без GPU: узлы грузятся, графы `tests/golden` сходятся с `/object_info` |
| venv ComfyUI без системного Python | `vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt`, `h3_comfy_venv_isolate` в `h3_gpu_env.sh` | новые серверы и следующая установка на старые |

Снимок НЕ распаковывать в `/dev/shm`: лимит памяти контейнера 258 ГБ, распаковка перезапустила контейнер.

## Базовый образ: закрепить digest, а не тег

Шаблон: `vastai/base-image:cuda-12.8.1-auto` (Ubuntu 24.04, CUDA 12.8 devel с `nvcc` для XPose;
не `vastai/comfy` — там CUDA 13 и свой ComfyUI). Тег **плавающий**: Vast может пересобрать его.

- Что от него зависело: venv ComfyUI создавался с `--system-site-packages` и молча брал ~100 пакетов
  системного Python (список: `infra/local/system-requirements.txt`). Теперь venv самодостаточен: нужные
  21 пакет закреплены версиями снимка, `pip check` и импорт проверяются при установке. На старых
  серверах (и в снимке `h3-20261004-1826`) флаг ещё включён — переключится при следующей установке.
- Что зависит до сих пор: ОС-библиотеки (glibc, libGL, ffmpeg из apt), CUDA toolkit, системный
  `python3.12`, на котором построены venv'ы, supervisor. Нужен совместимый pinned CUDA 12.8 образ ОС;
  целевая GPU-архитектура проверяется отдельно.
- Digest пишет `infra/h3_snapshot_pull.sh` в `SNAPSHOT.txt` (`base_image_digest`, запрос к Docker Hub с Mac:
  контейнер свой digest не видит). Если `base_image_tag_last_pushed` позже даты аренды сервера, сервер
  работает на более старом digest — взять его вручную в карточке инстанса Vast.
- Снимок `h3-20261004-1826`: `sha256:c63434109989a5736181bd1c2ad0a0f12c84e24b73b3bce6b3d604932745b410`
  (manifest list; amd64 `sha256:613382a2fd4238cadc8348389523c90201b131c530a430f46b313441d3280fbb`), тег
  обновлён 2026-09-07 — раньше снимка.
- Проверить вручную: `curl -s https://hub.docker.com/v2/repositories/vastai/base-image/tags/cuda-12.8.1-auto | python3 -m json.tool`
  (поле `digest`) или `docker buildx imagetools inspect vastai/base-image:cuda-12.8.1-auto`.
- В шаблоне Vast указывать образ как `vastai/base-image@sha256:<digest>`.

Проверка самодостаточности venv на стенде (2026-10-04): чистый `ubuntu:24.04` + `python3-venv libgl1
libglib2.0-0 ffmpeg`, venv без системных пакетов, пакеты venv снимка + `comfy-venv-requirements.txt` →
ComfyUI `--cpu` загрузил все 6 узлов (972 класса, как на стенде со снимком), все графы `tests/golden` — OK.

## Хранилище: Cloudflare R2 (решение пользователя 2026-10-04)

- Бюджет ≤ 4 €/мес; HF PRO ($9) отклонён.
- R2: $0,015/ГБ·мес (10 ГБ бесплатно), скачивание бесплатно и без лимита (цены сверены 2026-10-04).
  Всё (~185 ГБ: веса ~170 ГБ + снимок софта ~13 ГБ) ≈ $2,6/мес. Не зависим ни от HF, ни от авторов моделей.
- Бакет `h3-studio`; ключи (Account ID, Access Key ID, Secret) — только в Keychain Mac:
  `h3-r2-account-id`, `h3-r2-access-key-id`, `h3-r2-secret-access-key`. На сервер — через stdin.
- Hugging Face остаётся запасным источником весов (публичные репозитории, закреплённые ревизии и SHA-256).

## Схема: два слоя, снятые с эталонного сервера

| Слой | Что внутри | Где хранится | Размер |
|---|---|---|---|
| Софт | `/workspace/h3` без весов и данных (ComfyUI `b5cc8830` + узлы, venv'ы `venv`, `api-venv`, `qwen-venv` с патчем сна vLLM, `liveportrait-venv`, LivePortrait + собранный XPose, SAM2, `/workspace/h3/api`, скрипты), `/.uv`, `/etc/supervisor/conf.d/h3-*`, `/opt/supervisor-scripts/h3-*` | R2 (`software.tar.zst`) | ~13 ГБ |
| Веса | `ComfyUI/models/**`, `LivePortrait/pretrained_weights/**`, `hf-cache` (Qwen3-VL-8B), аудио | R2, рядом `weights.tsv` | ~170 ГБ |

Секреты (HF-токен, ключи R2) — **не в снимке**: `h3_snapshot_pull.sh` останавливается, если находит
токены или ключи; передаются при старте (Keychain Mac → stdin).

## Шаги

1. **Эталон:** сервер прошёл `verify_h3_server.sh` («ИТОГ: всё OK») и сделал принятый пользователем ролик.
   `scripts/h3_server_lock.sh` → `deploy/live-lock/` (закоммитить), `infra/h3_snapshot_pull.sh` → снимок.
2. **Стенд:** `infra/h3_local.sh up && start && check` на этом снимке — узлы и графы OK.
3. **R2:** выгрузить `software.tar.zst` и веса (`s5cmd`/`rclone`) с `weights.tsv`.
4. **Совместимая машина:** pinned Vast base image digest из `SNAPSHOT.txt`, CUDA toolkit и driver ≥12.8,
   `nvcc`, одна NVIDIA GPU с ≥90,000 MiB VRAM и место под все недостающие веса плюс 60 GB резерва.
   `infra/h3_up.sh` проверяет их до большой загрузки, показывает прогресс, проверяет SHA-256 и запускает
   восстановление автоматически; `onstart` не нужен.
5. **Mac:** аренда через `vastai` CLI (ключ в Keychain) и команда «сервер из образа» (ещё не написана):
   дождаться готовности → режим Qwen → `verify` против эталона → туннель Studio.
6. **Приёмка:** на чистом сервере `verify` — 0 FAIL; отдельно записать время проверки, архива,
   веса, аппаратной сборки и запуска. Время cold download измерять на реальном соединении, не обещать
   предел без контрольного прогона с тем же размером манифеста и классом сети. Затем сверить тестовый
   ролик с эталоном по параметрам и времени шага; эталонные графы (`tests/golden/`) не меняются.

## Обновления

Любое изменение сервера (код, пакет, вес) → новый эталон → новый снимок (по дате и коммиту) и догрузка
новых весов в R2. Снимок без записи в `deploy/live-lock/` не выпускается. Новый пакет для ComfyUI —
только с точной версией в `comfy-venv-requirements.txt`.

## Что нужно от пользователя

- Cloudflare: аккаунт, R2, бакет `h3-studio`, API-ключ только на этот бакет; ключи — в Keychain Mac.
- Ключ API Vast в Keychain (для аренды из командной строки).

## Готово в коде (2026-10-05): заливка и подъём из R2

| Шаг | Команда (с Mac) | Что делает |
|---|---|---|
| Ключи R2 | `security add-generic-password -s h3-r2-account-id -a h3 -w` (и `h3-r2-access-key-id`, `h3-r2-secret-access-key`) | Ключи только в связке Mac; на сервер идут через stdin SSH, на диск не пишутся |
| Эталон → R2 | `bash infra/h3_r2_push.sh --host root@HOST --port PORT` | Проверка на токены, опись весов SHA-256, архив софта собирается на сервере и уходит прямо в R2; веса по SHA (`weights/<sha256>`, дубликаты один раз, уже залитые не перезаливаются); `snapshots/LATEST` двигается только после проверки полноты |
| Новый сервер | `bash infra/h3_up.sh --host root@HOST --port PORT [--stamp STAMP]` | Проверка кода, HF, места и CUDA/GPU до загрузки; передача образа и весов с прогрессом, SHA-256 и докачкой; относительные LoRA-ссылки; актуальный overlay; XPose компилируется под GPU цели; режим Qwen, полная проверка и туннель Studio |

**Два слоя.** Образ — тяжёлое и редко меняющееся (система, ComfyUI, узлы, venv'ы); репозиторий — код и конфиги. Поэтому сервер из любого, даже старого, снимка после `h3_up.sh` равен master; свежий снимок (`h3_r2_push.sh` после проверенного сервера) только ускоряет подъём.

Состав архива и опись весов — `infra/h3_snapshot_spec.sh` (общие для снимка на Mac и заливки в R2).
Проверено на двух временных контейнерах стенда с локальной папкой вместо бакета (`H3_R2_TEST_DIR`):
дедупликация, повторный запуск без перезаливки, восстановление софта и весов, порча файла поймана и докачана.
Реальный бакет R2 и сервер Vast проверяются в первый день на сервере.

## Сборка образа без GPU (offline image build, 2026-10-05)

Новый образ софта собирается на Mac, без сервера, GPU и весов: старый снимок + текущий коммит,
поставленный **теми же установщиками**, что ставят сервер.

```bash
bash infra/h3_image_build.sh [--snapshot ~/H3-Snapshots/h3-20261004-1826] [--out "/Volumes/NEO Storage/H3-Snapshots"]
bash infra/h3_up.sh --host root@HOST --port PORT --snapshot "/Volumes/NEO Storage/H3-Snapshots/h3-<stamp>-<sha>"   # пока нет R2
```

| Шаг | Что делается |
|---|---|
| Нижний слой | `--base fresh`: старый `software.tar.zst` распаковывается в новый том `h3img-<stamp>` (~60 ГБ в VM). `--base overlay` (авто, если места мало): нижний слой — том стенда `h3root` **только для чтения**, сверенный с архивом `tar --diff` (отличия перезаписаны из архива, лишнее скрыто) — нужно ~20 ГБ. `h3root` и контейнер `h3-local` не меняются |
| Установка | одноразовый контейнер `h3-local:base` (Ubuntu 24.04, Python 3.12.3, как `vastai/base-image`), дерево на путях сервера (`/workspace`, `/.uv`, `/etc/supervisor/conf.d`, `/opt/supervisor-scripts`, `infra/local/h3img_box.sh`); бандлы — как на сервер (`deploy_remote_h3_local_studio.sh`, `scripts/h3_overlay_bundle.sh`); `deploy_h3_local_studio.sh --apply --with-models --parallel-models --ref-only` → `install_h3_h200_overlay.sh --apply` → `install_h3_acting.sh` → режим Qwen sleep; всё с `H3_IMAGE_BUILD=1` |
| Опись весов | `infra/h3_image_weights.py`: старый `weights.tsv` минус то, что ни один установщик больше не качает (SDPose, RT-DETR), плюс записанное установщиками (кадр Qwen-Image-Edit-2511, RIFE 4.17, 27B сценарист); размер и SHA-256 — из старой описи или публичного API Hugging Face (только метаданные) |
| Проверка | ComfyUI `--cpu` и GPU API запускаются supervisord'ом по собранным конфигам на разреженных заглушках весов; все узлы и 29 обязательных классов, графы `tests/golden`, `/api/live`, `/api/capabilities` (`keyframe_v1`, `continuation_v1`, `scenarist_v1`), `pip check`, `api/app` = `app/`, удалённое удалено |
| Упаковка | `lock/` (`scripts/h3_server_lock.sh --docker`), архив по `H3_TAR_ARGS` потоком прямо в `--out` (внешний exFAT-диск подходит: там только архив), `zstd -t`, SHA-256, `SNAPSHOT.txt` с digest базового образа из старого снимка и пометкой `build: built offline on stand, no GPU`. Временные контейнеры и том удаляются |

**`H3_IMAGE_BUILD=1`** (`h3_image_build` в `h3_gpu_env.sh`): без проверок драйвера/GPU (проверки версии
torch и сборки CUDA остаются), без скачивания весов (`h3_image_record_weight` пишет, что было бы скачано),
без запуска/перезапуска supervisor и проверок готовности, без туннеля Mac. Без флага установщики прежние
(`tests/test_image_build.py`). Необязательные веса попадают в опись только с флагами
`H3_KEYFRAME_QWEN_IMAGE_21=1`, `H3_SEEDVR2_7B=1`, `H3_CAMERA_EFFECTS=1`.

**`weights.tsv` образа** — шесть колонок: `path size sha256 source revision source_path` (source — репозиторий
HF или URL, `-` — только из копии сервера/R2). Первые три — как раньше; `h3_up.sh` читает `NF>=3`.
`h3_up.sh --snapshot` качает веса с этих источников (каждый по SHA-256), токен HF — из `$HF_TOKEN`/Keychain.

**Что на стенде не проверить** (проверяет `verify_h3_server.sh` на GPU): `torch.cuda`, запуск vLLM 0.15/0.19.1
(сон/пробуждение), ядра XPose после компиляции под фактический GPU, рендер и качество, загрузка реальных весов.
Ловушки, найденные сборкой: архив не содержит `ComfyUI/models`, поэтому после весов нужен
`git checkout -- models` (иначе базовый установщик видит «грязный» ComfyUI — теперь делает `h3_up.sh`);
overlay сливал `app/` поверх старого и оставлял удалённые модули (теперь заменяет, копия в `backup/`).
