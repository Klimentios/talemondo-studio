# H3 Studio — документация

Вся документация проекта лежит здесь. Если факт есть в коде — прав код.

## Журналы этой установки

| Файл | Что там |
|---|---|
| [STATE.md](STATE.md) | Текущее состояние: сервер, туннель, снимок весов, действующие умолчания |
| [DECISIONS.md](DECISIONS.md) | Решения и запреты владельца установки (его словами, с датой) |
| [CHANGELOG.md](CHANGELOG.md) | Одна строка на изменение: дата · кто · коммит · что · проверено ли |
| [JOURNAL.md](JOURNAL.md) | Задачи и вопросы агентов друг другу |
| [TOOLS.md](TOOLS.md) | Команды и процессы: деплой, проверки, бэкапы, A/B, снимки, навыки |

## Как сделать

- [guides/STUDIO_INSTALL.md](guides/STUDIO_INSTALL.md) — установка Studio на Mac и подключение GPU-сервера
- [guides/H3_PROMPT_GUIDE.md](guides/H3_PROMPT_GUIDE.md) — как писать промпты для MiniMax H3; раздел «0. Язык H3» — что модель реально выполняет
- [guides/H3_CHAT_PROMPT_WRITER.md](guides/H3_CHAT_PROMPT_WRITER.md) — писатель промптов в чате
- [guides/PRODUCER_QC.md](guides/PRODUCER_QC.md) — проверка промпта до GPU и готового MP4 до показа
- [guides/H3_AB_HARNESS.md](guides/H3_AB_HARNESS.md) · [AB_EXPERIMENT_TEMPLATE.json](guides/AB_EXPERIMENT_TEMPLATE.json) — A/B на одинаковом входе
- [guides/STUDIO_BACKUP.md](guides/STUDIO_BACKUP.md) — резервная копия данных Studio
- [guides/STUDIO_COMFYUI_ECOSYSTEM_PLAN.md](guides/STUDIO_COMFYUI_ECOSYSTEM_PLAN.md) — свои узлы, модели и воркфлоу ComfyUI в Studio

## Как устроено

- [reference/STUDIO_STRUCTURE.md](reference/STUDIO_STRUCTURE.md) — карта модулей, путь от кнопки до узла ComfyUI
- [reference/STUDIO_SERVER_CONTRACT.md](reference/STUDIO_SERVER_CONTRACT.md) · [reference/GPU_API.md](reference/GPU_API.md) — задание Studio → GPU API, REST API сервера
- [reference/H3_SERVER_FROM_ZERO.md](reference/H3_SERVER_FROM_ZERO.md) — новый GPU-сервер от нуля до первого ролика
- [reference/H3_SERVER_QUEUE.md](reference/H3_SERVER_QUEUE.md) — очередь на сервере (Mac можно выключить)
- [reference/H3_ENGINE_INTERNALS.md](reference/H3_ENGINE_INTERNALS.md) · [reference/H3_ENCODER_RESEARCH.md](reference/H3_ENCODER_RESEARCH.md) — как H3 читает промпт и референсы (замеры)
- [reference/MINIMAX_H3_OFFICIAL_DOCS.md](reference/MINIMAX_H3_OFFICIAL_DOCS.md) — сводка официальной документации MiniMax
- [reference/H3_CONTEXT_IR_ARCHITECTURE.md](reference/H3_CONTEXT_IR_ARCHITECTURE.md) · [IMPLEMENTATION](reference/H3_CONTEXT_IR_IMPLEMENTATION.md) — Context-IR
- [reference/H3_LORA_AUTO_ROUTING.md](reference/H3_LORA_AUTO_ROUTING.md) · [H3_RENDER_EXPERIMENTS.md](reference/H3_RENDER_EXPERIMENTS.md) · [H3_CONFIG_AB_PLAN.md](reference/H3_CONFIG_AB_PLAN.md) — LoRA, опциональные модели, A/B настроек
- [reference/H3_GOLDEN_IMAGE_PLAN.md](reference/H3_GOLDEN_IMAGE_PLAN.md) — свой снимок сервера в Cloudflare R2 (необязательно)
- [reference/EDIT_ENGINE.md](reference/EDIT_ENGINE.md) — монтажный движок
- [reference/AI_FILMMAKING_KNOWLEDGE.md](reference/AI_FILMMAKING_KNOWLEDGE.md) · [PROMPT_DIRECTION.md](reference/PROMPT_DIRECTION.md) — киноязык и режиссура для ИИ-видео
- [reference/REFERENCE_EXCERPTS.md](reference/REFERENCE_EXCERPTS.md) — вырезки видео/аудио-референсов
- [reference/SUBSCRIPTION_CONNECTOR.md](reference/SUBSCRIPTION_CONNECTOR.md) — агенты по подписке (Claude Code, Codex) вместо платного API

`prompts/` — эталонные промпты, их читают тесты. `../skills/` — навыки агентов. `../logs/` — сырые логи (не в git).

## Правила, чтобы не копился мусор

1. Не создавать датированные файлы-сводки: состояние — в `STATE.md`, итог работы — в `CHANGELOG.md`, вопрос агенту — в `JOURNAL.md`.
2. Новый документ — только для новой подсистемы, сразу со ссылкой в этом индексе.
3. Устаревший документ удаляется (`git rm`): история остаётся в git.
4. Хосты, порты и имена снимков — только в `STATE.md`. Ключи и токены — никогда в docs, skills и logs.
