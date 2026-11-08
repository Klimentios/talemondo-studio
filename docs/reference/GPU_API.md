# MiniMax H3 Studio

Локальный интерфейс и REST API для полного MiniMax H3 Ref2VA. Профиль максимального качества использует BF16 transformer, BF16 Qwen3-VL encoder, FP16 video VAE, FP32 audio VAE, `ref_image_size=max`, native 1344×768, 24 FPS, 25 шагов и без Turbo LoRA. Других профилей весов нет (INT8 удалён: его веса не устанавливаются).

ComfyUI остаётся внутренним GPU-движком. Studio даёт отдельный UI и API.

## Reference conditioning

- до 9 изображений: `@ref1`…`@ref9` → `<Picture 1>`…`<Picture 9>`;
- до 3 видео: `@video1`…`@video3` → `<Video 1>`…`<Video 3>`;
- до 3 отдельных аудио: `@audio1`…`@audio3`;
- звук из референсного видео: `@video1audio`.

В H3 аудио-ссылки нумеруются единым списком: сначала дорожки из видео, затем отдельные аудио. Studio пересчитывает номера автоматически. Standalone audio принимается только после подтверждения прав на голос/запись.

Официальный Ref2VA-промпт имеет шесть секций в точном порядке: `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`. UI умеет вставить этот шаблон.

## API

`POST /api/jobs` принимает `multipart/form-data`:

- `prompt` — H3 Ref2VA prompt;
- `duration` — 4–15 секунд;
- `aspect_ratio` — `16:9` или `9:16`;
- `quality` — `compact`/`quick`/`balanced`/`max`/`custom` (+ `megapixels`); по умолчанию `custom` 0.9 Мп
  (все умолчания — `app/contract.py` `DEFAULTS`, те же значения, что шлёт Studio);
- `profile` — только `bf16`;
- `steps` — 20–50, по умолчанию 25;
- `seed` — целое число или пустое значение;
- `images` — повторяющееся поле PNG/JPEG/WebP;
- `videos` — повторяющееся поле MP4/MOV/WebM;
- `audios` — повторяющееся поле WAV/FLAC/MP3/M4A/OGG;
- `use_video_audio` — подключить звук из видео;
- `contract_version` — версия контракта клиента (`app/contract.py` `API_CONTRACT`); GPU отвечает 422,
  если клиент новее сервера. `/api/capabilities` объявляет `api_contract` и `director_schema_sha256`;
  Mac Studio не отправляет задание на сервер со старым контрактом или другой схемой Director.
- `control_type` — устаревшее поле: принимается только `none`/`auto`. Структурный контроль
  (ControlNet, SDPose, RT-DETR, Depth-Anything-3) удалён 2026-10-04: он ломал рендер Ref2VA.
  Движение задаётся обычным видео-референсом с ролью «Движение и камера».

Старые поля `ref1`, `ref2`, `ref3` сохранены для обратной совместимости.

Ответ: `{"job_id":"...","status":"queued"}`. Статус: `GET /api/jobs/{job_id}`. MP4: `GET /api/jobs/{job_id}/video`. OpenAPI: `/docs`.

## Граница 2K

Открытый H3-Base генерирует на native canvas 1344×768. `H3-Regenerate-2K` — отдельный hosted/API этап, его веса не опубликованы. Studio не делает платные API-вызовы без отдельного включения.

## Проверки

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile app/*.py scripts/*.py
```

Тюнинг сохранённого workflow:

```bash
python3 scripts/tune_comfy_workflow.py input.json output.json
```

## Доступ

Studio: `http://127.0.0.1:18189`. ComfyUI: `http://127.0.0.1:18188`. Оба сервиса слушают localhost; внешний доступ должен идти через SSH-туннель.
