# H3 Studio

Локальная студия ИИ-видео на MiniMax H3: референсы, сцены и шоты, очередь рендера, проверка дублей, монтаж
и агенты (Claude Code, Codex) через MCP. Интерфейс работает на Mac, рендер — на вашем GPU-сервере через ComfyUI
или в облаке через MiniMax API.

*English summary below.*

## Что умеет

- **Создать**: фото, видео и звук как референсы с ролями (лицо, персонаж целиком, костюм, окружение, движение, камера),
  промпт, 1–4 варианта, режиссёрские поля (камера, движение, свет, цвет).
- **Сюжет**: сцены и шоты, опорные кадры, сборка сцены цепочкой, «Мир фантазии», проверка стартовых кадров.
- **Франшизы**: паспорт мира и героев, пакет идеи, постановка на плане, проверка шотов по паспорту.
- **Замена героев по видео** и **смена мира по видео** — по встроенным настройкам или по своим принятым рецептам (сохраняются в данных Studio).
- **Где рендерить**: свой GPU (ComfyUI, полный BF16, LoRA, мегапиксели, кэш, Turbo по выбору) или MiniMax API (768P).
- **Свои воркфлоу**: любой граф ComfyUI с любой моделью становится формой Studio, REST-адресом и инструментом агентов.
- **Монтаж**: монтажный лист, переходы, звук, экспорт в Premiere / DaVinci Resolve / Final Cut.
- **Агенты**: MCP-сервер `h3-studio` (`scripts/h3_studio_mcp.py`) — агент собирает сцену, ставит рендер по разрешению,
  проверяет MP4.
- **Проверки до GPU**: формат промпта, склейки в форме MiniMax «At MM:SS.mmm», состав героев, шоты короче 1 с,
  повтор отклонённых дублей.

## Язык интерфейса

Studio работает по-русски и по-английски: переключатель RU/EN внизу левого меню; при первом запуске язык берётся из
языка браузера. Английский слой — `preview/h3-director/dom-translate.mjs` и словарь `preview/h3-director/i18n/en.json`.

## Установка (Mac)

Дважды щёлкните **«Установить Studio.command»** в корне папки (если macOS не даёт открыть — правый щелчок → «Открыть»).
Установщик сам поставит Python, ffmpeg и службу Studio и откроет «Первый запуск» —
`http://127.0.0.1:18187/setup.html`: пять шагов с кнопками, без терминала.
Подробно — [docs/guides/STUDIO_INSTALL.md](docs/guides/STUDIO_INSTALL.md). Данные Studio лежат в
`$H3_MAC_DATA_DIR` (по умолчанию `~/Library/Application Support/H3 Studio`), не в репозитории.

## GPU-сервер

Нужна NVIDIA от 96 ГБ видеопамяти, ОЗУ от 256 ГБ, диск от 500 ГБ, Ubuntu с root по SSH (например, Vast.ai или RunPod).

Проще всего — из Studio: «Первый запуск» → API-ключ Vast.ai → «GPU и модели» → «Арендовать» → «Установить».
Свой сервер — одной командой с Mac:

```bash
bash scripts/h3_new_server.sh --host root@HOST --port PORT
```

Ставится ComfyUI с закреплённой сборкой, открытые веса MiniMax H3 с Hugging Face (без токена, с проверкой SHA-256),
узлы Studio и GPU API; затем Mac подключается туннелем. Проверка без рендера: `bash scripts/verify_h3_server.sh --host root@HOST --port PORT`.
От нуля до первого ролика — [docs/reference/H3_SERVER_FROM_ZERO.md](docs/reference/H3_SERVER_FROM_ZERO.md).

## MiniMax API

Вставьте ключ на «Первом запуске» и выберите «MiniMax API» в «Параметрах ролика». Задания API — 768P, без LoRA,
шагов и кэша. Замена героев по видео идёт только на своём GPU.

## Агенты

Claude Code и Codex подключаются к Studio как к MCP-серверу; команду с путями вашего Mac показывает «Первый запуск».
Правила для агентов — [AGENTS.md](AGENTS.md), навыки — `skills/`.

## Примеры и благодарности

[Обзор Studio и семь видеопримеров](https://github.com/Klimentios/Klimentios/blob/main/writing/talemondo-studio.md) опубликованы отдельно от кода и данных Studio.
[MiniMax AI](https://huggingface.co/MiniMaxAI/MiniMax-H3) создала модель H3;
[Comfy-Org](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_h3.py) реализовала нативные узлы H3 для ComfyUI.
Инструменты [Kijai](https://github.com/kijai/ComfyUI-KJNodes) входят в экосистему Studio, в том числе в дополнительный путь подбора цвета.
TaleMondo Studio — самостоятельное приложение автора, не официальный выпуск этих проектов.

## Тесты

```bash
python -m pytest tests
```

```bash
cd preview/h3-director && node --test *.test.mjs
```

## Автор и лицензия

© 2026 **Klimentios** — Klimentioss@gmail.com.

Studio распространяется по лицензии [PolyForm Noncommercial 1.0.0](LICENSE): её можно бесплатно использовать,
изучать, менять, дополнять своими моделями и узлами и делиться изменениями — **только не в коммерческих целях**.
При любом распространении (в том числе изменённой версии) сохраняйте файл `LICENSE` со строкой
`Required Notice: Copyright (c) 2026 Klimentios`. Коммерческое использование — только по отдельному
разрешению автора (пишите на почту выше). Сторонние компоненты остаются под своими лицензиями — см. [NOTICE](NOTICE).

«TaleMondo» — название автора. Лицензия даёт права на код, но не на название: изменённые версии распространяйте под
другим именем и не выдавайте их за версию автора.

## Лицензии моделей

Код Studio не содержит весов. MiniMax H3 и LoRA скачиваются с их страниц; условия использования каждой модели
(в том числе некоммерческие) проверяйте сами перед работой.

---

## English summary

H3 Studio is a local AI-video studio for MiniMax H3: references with roles, scenes and shots, a render queue,
take review, editing and MCP access for coding agents. The UI runs on your Mac in English or Russian (RU/EN switch at the bottom of the side menu; the browser language picks the first one); rendering
runs on your own GPU server through ComfyUI, or in the cloud through the MiniMax API.

- Install on macOS: double-click «Установить Studio.command»; the first-run wizard opens at `http://127.0.0.1:18187/setup.html`.
- GPU server (≥96 GB VRAM): rent and install from the Studio UI (Vast.ai), or `bash scripts/h3_new_server.sh --host root@HOST --port PORT`. All models are public; no Hugging Face token is needed.
- Any ComfyUI workflow (any model) can be imported in «Свои воркфлоу» and becomes a Studio form and an agent tool.
- Defaults favour quality (BF16, 24 steps); Turbo LoRAs, other profiles and 4–50 steps are opt-in.
- Model weights are not included; check each model's licence yourself.
- [Product walkthrough and seven video examples](https://github.com/Klimentios/Klimentios/blob/main/writing/talemondo-studio.md). MiniMax AI created H3; Comfy-Org supplies native H3 nodes; Kijai's ComfyUI tools support optional Studio paths. This is an independent application.

### Author and licence

© 2026 **Klimentios** — Klimentioss@gmail.com. Licensed under [PolyForm Noncommercial 1.0.0](LICENSE): free to use,
study, modify, extend with your own models and nodes, and share — for noncommercial purposes only. Keep the
`LICENSE` file and its `Required Notice: Copyright (c) 2026 Klimentios` line in every copy, modified or not.
Commercial use needs the author's written permission. Third-party components keep their own licences ([NOTICE](NOTICE)).
“TaleMondo” is the author's name for the studio: the licence covers the code, not the name, so modified versions must
use a different name and must not present themselves as the author's.
