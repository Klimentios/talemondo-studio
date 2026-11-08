# MiniMax H3 — что говорит официальная документация (сверено 04.11.2026)

Источники проверены в этот день, а не взяты из памяти. Здесь собрано то, чего не было в
[H3_PROMPT_GUIDE.md](../guides/H3_PROMPT_GUIDE.md). Сами гайды по промптам (`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md` и `_base_en.md`) на Hugging Face не менялись с августа 2026.

| Источник | Что в нём |
|---|---|
| platform.minimax.io/docs/llms.txt | Индекс всей документации платформы, страницы доступны как `.md` |
| …/guides/video-generation.md | Лимиты входов, длина промпта, теги камеры T2V |
| …/guides/video-prompt.md | Галерея: короткие пользовательские промпты и выход |
| …/guides/local-deploy-h3.md | ComfyUI, SGLang, эталонные параметры, бенчмарки, предупреждения |
| …/api-reference/video-generation-v2-h3-context-ir.md | Context-IR: вход, формат выхода |
| Comfy-Org/workflow_templates @ `3c1df78`, `video_minimax_h3_{r2v,t2v,i2v}.json` | Официальные графы и заметки внутри них |
| github.com/MiniMax-AI/MiniMax-H3, `Ref2VA/model_index.json`, `tokenizer/tokenizer_config.json`, `processor/*` | Сдвиги сигм, токенизатор, процессор |
| `~/H3-ComfyUI-mac/ComfyUI/comfy/supported_models.py`, класс `MiniMaxH3` | Что ComfyUI применяет сам |

## Проверенные факты

**Сэмплинг и граф ComfyUI**
- Сдвиги сигм зашиты в модель: видео `12.0`, аудио `3.0` (`model_index.json` → `sigma_shift_scales`). ComfyUI применяет их сам (`sampling_settings = {shift: 12.0, audio_shift: 3.0}`). Отдельный узел shift в нашем графе не нужен, и его отсутствие не ошибка.
- Модель CFG-distilled: `BasicGuider`, без негативного промпта. У нас так же.
- Официальный шаблон R2V: `res_multistep` + `simple`, 20 шагов, `MiniMaxH3ReferenceToVideo`, `ref_image_size=match`, 0,4 Мп в Resolution Selector. Наш стандарт совпадает (`res_multistep/simple`, 24–25 шагов, ref `max`).
- В заметке шаблона R2V прямо сказано: **«`beta` или `normal` scheduler tends to outperform `simple` for reference-heavy prompts»**. Добавлен профиль `ref_beta` (`res_multistep/beta`) — только для A/B, умолчание не менялось.
- Эталон MiniMax (SGLang): 50 шагов, `flow_shift 12`, `audio_flow_shift 3`, короткая сторона 768. **Для Studio это не повод менять шаги:** по опыту пользователя 50 шагов ничего не дали, а 24 всегда были лучшими (DECISIONS 16.10 и 04.11). Брак 03–04.11 вызвали ошибки сборки промптов, а не число шагов.
- Кэш: Cache-DiT `quality: high` ускоряет в 1,4 раза при SSIM 0,93 против lossless (4×H200). Кэш меняет картинку измеримо. Наш FirstBlockCache `safe` против `off` на одном seed пока не сравнивали.
- `ref_image_size=max` держит референсы до 2048 px по короткой стороне. Их токены идут через каждый шаг: личность точнее, рендер медленнее.
- Длительность снаппится к сетке `17k+5` кадров при 24 fps. Минимум 107 кадров ≈ 4,46 с.
- Нативный холст — короткая сторона 768, максимум 768×1344, кратно 32. 0,4 Мп = 480×864 (по таблице в шаблоне) — ниже нативного. Умолчание Studio с 04.11 — 0,8 Мп (672×1216 вертикально), тоже ниже нативного; 480P остаётся ручным выбором в форме (DECISIONS 04.11).
- Шаблоны Comfy используют INT8 / NVFP4. Это другие артефакты, чем официальный BF16/FP32, качество отличается. Не сравнивать с бенчмарками SGLang.
- SGLang `--use-fsdp-inference true` может молча выдать испорченные видео и аудио (sglang#34227).
- Есть 10 стилевых эмбеддингов `minimaxh3_*.safetensors` (Comfy-Org/MiniMax-H3/embeddings). Мы их не используем. Это кандидат для A/B стиля франшиз.

**Токены и длина промпта**
- Текстовый энкодер — Qwen3-VL (`Qwen2TokenizerFast`, `model_max_length` 262 144). Промпт обрезкой токенов не теряется. Длина вредит иначе: внимание модели расходится на повторы. Отсюда наш предел 4000 знаков для замен.
- Предел API — 7000 символов на `text`.
- Заметка шаблона R2V: «Ref2va's output is very sensitive to prompt wording; matching the reference tags precisely and being explicit about which reference drives which part of the shot tends to work best».

**Синтаксис**
- R2V: теги `<Picture N>`, `<Video N>`, `<Audio N>` в порядке подключения входов, и явно, какой референс отвечает за личность, стиль, движение, камеру или голос.
- T2V: теги камеры `[pan]`, `[zoom]`, `[static]` ставятся сразу после ключевого описания. Допустим таймлайн `[0s-1s] …`.
- Официальные пользовательские промпты в галерее — **60–100 слов**, с ролью каждой картинки («Image 2: character. Image 3: bag»). Остальное дописывает Context-IR.
- Выход Context-IR для T2V — секции `integrated_multimodal_description` (`[Shot N] At 00:02.800, …`), `overall_soundscape`, `non_diegetic_music`. Шесть секций с `subject_definitions` / `retention_analysis` — это схема Ref2VA из гайда на Hugging Face.
- Context-IR и Regenerate-2K закрыты, доступны только через API платформы. Пользователь API не использует (DECISIONS 04.11), поэтому IR пишет наш агент.

**Лимиты входов (Ref2VA)**
- До 9 изображений, 3 видео, 3 аудио, всего не больше 12 файлов.
- Видео и аудио — по 2–15 с на клип, суммарно не больше 15 с.
- Стороны 256–5760 px, соотношение сторон 0,4–2,5.

## Что внесено в Studio

- Профиль сэмплинга `ref_beta`: форма, MCP, GPU API, тест `tests/test_sampling_profiles.py`. Для GPU нужен деплой `app/`.
- Предел замены 4000 знаков и правила для описаний исполнителей (`tests/test_swap_recipe_gate.py`) — по журналу провалов и выводу «чувствителен к формулировкам».

## A/B, когда поднимется GPU (один шот, один seed, 0,8 Мп 672×1216)

Все пробы — на 24 шагах. Готовые команды: [H3_CONFIG_AB_PLAN.md](H3_CONFIG_AB_PLAN.md).

1. `standard` против `ref_beta` на замене героя.
2. Кэш `safe` против `off`.
3. Стилевой эмбеддинг против без него, для 3D-франшизы.

Принимать только по кадрам, с записью в [H3_RENDER_EXPERIMENTS.md](H3_RENDER_EXPERIMENTS.md).
