---
name: h3-prompt-writing
description: Write, rewrite, and audit production-ready MiniMax H3 audiovisual prompts for T2VA, I2VA, FL2VA, L2VA, and full-reference Ref2VA. Use when selecting an H3 mode, assigning image/video/audio reference roles, preserving identity or attributes, compiling shot timelines, dialogue, lyrics, lip-sync cues, soundscape, or native H3 Context-IR-style schemas.
---

# MiniMax H3 Prompt Writing

Compile the user's creative intent into MiniMax H3's native prompt shape. A finished prompt is **compiled**, not generated or creatively accepted.

## Route the Request

1. Establish the target duration, aspect ratio, desired output, exact dialogue or lyrics, and the role of every supplied asset.
2. Choose one route:
   - T2VA: text alone defines the audiovisual timeline.
   - I2VA: one image is the exact first frame.
   - FL2VA: two images are the exact first and last frames; normally prefer one continuous shot.
   - L2VA: one image is the exact last frame.
   - Full-reference Ref2VA: images, videos, or audio supply reusable subjects, style, movement, editing, continuation, composition, voice, music, or other reference relationships.
3. Do not silently mix a keyframe route with Ref2VA. On the current H3 Context-IR API, `first_frame`/`last_frame` roles and `reference_*` roles are mutually exclusive.
4. Ask only for missing information that changes the schema or would force invention of exact dialogue, lyrics, asset roles, or duration. Otherwise state a concise assumption and proceed.

## Load the Right Reference

- For T2VA, I2VA, FL2VA, or L2VA, read [references/base-en.txt](references/base-en.txt) completely and preserve its field names, alignment sentence, section order, speaker syntax, and timing notation.
- For full-reference Ref2VA, read [references/ref-en.txt](references/ref-en.txt) completely and preserve its six-section schema, label rules, task types, and retention markers. Also apply the shared dialogue, camera, and sound rules in `base-en.txt`.
- For Russian briefing, mode selection, templates, identity/audio patterns, production examples, anti-patterns, source scope, and QC, read [references/h3-prompting-ru.md](references/h3-prompting-ru.md).

## Compile the Prompt

### Base modes

Keep the exact three fields in this order:

1. `integrated_multimodal_description`
2. `overall_soundscape`
3. `non_diegetic_music`

Place the exact official alignment sentence before them for I2VA, FL2VA, and L2VA. T2VA has no alignment sentence.

### Full-reference mode

Keep the exact six sections in this order:

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

Use `<Subject N>` for reusable visible content, `<Picture N>` only when the image itself is a frame/composition anchor, `<Video N>` for whole-video editing, continuation, or temporal structure, and `<Audio N>` for copied or referenced signals. Keep every label's meaning stable in all sections.

## Non-negotiable Writing Rules

- Write descriptive sections in English. Preserve dialogue, lyrics, and visible text in their original language. Follow the user's language decision in `docs/DECISIONS.md` for speech and visible text (`<d>[Language] …</d>`).
- Put exact spoken or sung text only inside `<d>[Language] ...</d>` and keep stable speaker IDs such as `(S1)`.
- Use `[Shot 1]` without a timestamp. Use strictly increasing `[Shot N] At MM:SS.mmm, ...` cut times for later shots, all within the requested duration.
- Describe composition, subject, environment, action/state change, camera, and synchronized sound in playback order. Prefer causal motion and a reachable end state over adjective stacks.
- Separate diegetic world sound from audience-only music. Do not repeat full dialogue or lyrics in the sound sections.
- Preserve exact user-supplied words and punctuation unless the full-reference guide explicitly requires normalization for reused reference audio. Never invent missing lyrics or transcribe unclear audio by guessing.
- Use fixed Ref2VA relationship markers only: visible content uses `fully_preserved`, `partially_preserved`, `attribute_transfer`, or `weak_reference`; audio uses `fully_copy`, `partially_copy`, `reference`, or `weak_reference`.
- Express reference roles positively and precisely. Use short observable constraints only to resolve a real conflict; do not substitute long repeated `NO` lists for reference semantics.
- Do not promise perfect identity, lip-sync, text rendering, audio copying, or creative quality. Prompt markers express the requested relationship, not a deterministic guarantee.

## Motion, Camera, and Reference Ownership

### Полная изоляция исходных исполнителей

Физический масштаб: животные и люди существуют в одной размерной системе. Взрослый человек выше кота на той же плоскости; рост героев задавай сравнением и держи одним на всю серию. Перспектива может менять размер на экране, но не превращает кота в персонажа человеческого роста. В кадре без человека не добавляй его ради сравнения; сверяй масштаб по дверям, сиденьям, мебели, рукам и точкам контакта с землёй. Проверяй это на готовом MP4, а не только в промпте.

Когда пользователь требует заменить исходных героев без следов, это критерий приёмки для **каждого кадра**: не должны оставаться исходные голова, морда, шерсть, волосы, уши, хвост, лапы, руки, ноги, одежда, аксессуары, силуэт или отражение. Сохраняй только назначенные движение, тайминг, контакты и камеру. Если исходное окружение закрепляет чужие детали, разрешено пересоздать собственную совместимую сцену и свет. Не проси одновременно точно сохранить исходные пиксели сцены и полностью исключить тело исходного героя.

Роль видео выбирай по тому, что должен передать референс: `Исходное видео` — замена героев **внутри исходной сцены** (машина, комната, остальные участники сохраняются); `Движение и камера` — перенос движения и кадрирования на героя в **новой** сцене. На практике замены внутри сцены получались с `Исходное видео`, а с `Движение и камера` в той же задаче проваливались. Рецепт по умолчанию и проверки — в навыке `minimax-video-production`. Ни одна роль сама не гарантирует полного удаления исходных исполнителей. По возможности подготовь и проверь стартовый кадр с правильными героями; текущий Context-IR не позволяет одновременно передать роли keyframe и reference. Не запускай платный повтор только ради проверки формулировки.

В чистом кадре также не должны появляться перенесённые или заново нарисованные буквы, цифры, таймкоды, ID камеры, логотипы, водяные знаки, титры, значки и интерфейс исходника. Это относится ко всему кадру, включая нижнюю границу, предметы и отражения. Даже очищенный входной референс не гарантирует, что модель не восстановит такую графику: проверяй готовый результат.

После рендера проверь первый кадр, переходы и весь ролик с частой выборкой кадров, особенно края костюма, лапы, хвосты, отражения и границы кадра. Любая видимая деталь исходного исполнителя или чужая надпись/графика означает QC FAIL; технический MP4 или обрезка начала не делают такой ролик готовым.

### Типовой сбой: исходные исполнители и лишняя анатомия

Бывает, что готовый MP4 оставляет исходных людей почти до конца или добавляет героев позади исходных исполнителей, хотя задание уже имело роль `Движение и камера`, `character_swap`, отдельные фото героев и подробный запрет утечки. Значит, нельзя объявлять причиной одну лишь роль видео или отсутствие запрета: модель не закрепила назначенных героев за исходными исполнителями на всём таймлайне. Длинные скомпилированные промпты (примерно 6,8–7,3 тыс. знаков против 2,9–3,6 тыс. в удачных задачах) могут ослабить приоритет замены; это корреляция, а не доказанная причина.

Другой типовой случай: человек-танцор заменён на героя в костюме, но модель дорисовала хвост, которого нет ни в исходнике, ни на фото костюма, а предметы в руках на фото костюма перешли в результат. Не утверждай, что одно слово в промпте доказанно вызвало хвост, но не повторяй универсальный список `fur/ears/tail/paws` в заданиях про человека: перечисляй анатомию по виду персонажа и описывай нужный человеческий силуэт положительно.

Порядок для следующих замен:

1. До очереди просмотри **каждый** исходный фотоассет и первые/переходные кадры видео. Предмет в руке на фото считается частью визуального условия, даже если текст просит пустые руки. Сначала исправь изображение и назначь новый asset ID.
2. Для каждого исполнителя зафиксируй одну пару «исходный исполнитель → герой» и начальное положение героя. Составь короткие шесть разделов Ref2VA без повторяющихся общих запретов. Видео описывает действия и камеру, фото — лицо, тело и костюм. Для человека говори о человеческих руках, ногах и контуре спины; для кота — о его собственной шерсти, лапах и хвосте, если хвост нужен.
3. Сначала ставь **один** диагностический фрагмент. Проверь первый кадр, все смены плана и частую выборку всего MP4: нет ли исходного исполнителя, двойника, неверных лап/рук, предмета в руке, лишнего хвоста. Если первый фрагмент не прошёл, удержи следующие части той же серии и не склеивай их.
4. При повторной утечке меняй визуальный вход или маршрут обусловливания (например, выделенный участок исполнителя, очищенный референс либо совместимый маршрут с кадровым якорем после проверки API). Не отправляй тот же ролик снова с удлинённым списком запретов и новым seed: такая правка сама по себе не доказала улучшения.

### Полная замена героя

Для **полной** замены явно назначай героя видимому исполнителю (`reference_performers`, `keep_others=true` для неизменяемых остальных), задавай героя **одним** фото `Персонаж целиком` (лицо и полный костюм). Пара `Лицо героя` + `Костюм` на том же исходнике проваливалась, а одно `Персонаж целиком` с планом шотов проходило. В тексте различай, что видео задаёт движение/камеру/тайминг, а фото — всю внешность героя, включая лапы и одежду. Исходный звук при необходимости сохраняй `keep_source_audio=true` и проверяй готовый MP4. Рабочие настройки по умолчанию: BF16, 24 шага, safe cache, 0,8 Мп, `character_swap`, `ref_image_size=max`; для новых роликов длительность и разрешение выбирай по задаче и действующим решениям пользователя. При двух героях закрепляй каждого за отдельным исполнителем по месту и действию (приметы — только чтобы различить; в тексте не повторять), по одному фото `Персонаж целиком` на героя, и расписывай в `detailed_description` шоты: когда и где появляется каждый . Для 3+ героев схема не проверена.

For a shot where physical performance matters, plan the visible sequence before compiling the H3 fields: starting pose and support → initiating movement and weight shift → path and contact → reaction and reachable landing pose. State only features observable at the shot scale. For an animal, check species-specific gait, contact, body proportions, and landing against a suitable motion reference; keep its identity/coat/face reference separate. See the installed `ai-video-cinematic-director` skill and its `references/physical-motion-and-prompt-engineering.md` when detailed staging is needed.

Assign each supplied source one explicit job before choosing labels: appearance/identity, transferable attributes, environment, motion/timing, camera path, or audio. A motion video can guide timing, path, and contacts without becoming the target person's or animal's identity. If a source contains an unwanted performer, define the intended visible subject from its own image and describe the video as a movement source; use the documented retention relationship that matches what is truly retained. Do not claim this wording guarantees isolation: inspect the output frames for appearance leakage.

In `detailed_description` or `integrated_multimodal_description`, describe subject motion and camera motion separately and in playback order. If a reference video owns a camera path, cite it where that path matters instead of inventing conflicting speed or amplitude. Keep a complex contact action in one continuous shot only when duration and source frames make it plausible; otherwise split at a usable cut or use a controlled motion plate. The official MiniMax schema and the actual target workflow outrank community prompt patterns.

## Validate Before Delivery

Check all of the following silently:

- mode matches the supplied assets and target task;
- exact field names and order are preserved;
- each label is defined once and used consistently;
- retention entries cover every separately tracked reference;
- the timeline fits 4–15 seconds and every action can plausibly complete;
- first/last-frame landing is explicit when applicable;
- speakers, language tags, dialogue, and voiceover rules are consistent;
- diegetic sound and non-diegetic music are separated;
- no unsupported asset detail, API guarantee, or invented dialogue was added;
- the response distinguishes prompt compilation from generation/testing.

Return the final prompt in one copyable text block. Put assumptions or a brief asset-role ledger before the block only when they help the user catch a material mismatch.
