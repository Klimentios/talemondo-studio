# MiniMax H3: единый production-guide по промптам

Проверено по официальным источникам 24 сентября 2026 года. Версия официального GitHub-репозитория при сверке: commit `d21241f0a4b3acbb34c97dae47fa417b7065e438`.

Этот документ отвечает на практический вопрос: как перевести идею, кадры, видео и звук в нативный промпт MiniMax H3, не подменяя официальную грамматику самодельным «универсальным мегапромптом».

## 1. Главный вывод

H3 лучше получает не мешок красивых прилагательных и повторяющихся запретов, а компактную производственную спецификацию:

`режим → роли входов → связи сохранения → таймлайн → действие и камера → речь → физический звук → музыка`

Для обычной текстовой или keyframe-генерации используется трёхсекционная схема. Для full-reference Ref2VA — шестисекционная. Эти схемы являются форматом официального prompt rewriter / Context-IR guidance MiniMax.

Это не означает, что отрицательные ограничения запрещены. Они полезны, когда кратко устраняют конкретный конфликт: например, «transfer only the jacket; do not transfer the person's identity». Они становятся вредным шумом, когда десятки раз повторяют `STRICT`, `ABSOLUTE`, `NO`, не объясняя роли референсов и последовательность событий.

## 2. Что подтверждено официально

| Характеристика | Подтверждённое значение | Область действия |
| --- | --- | --- |
| Длительность результата | 4–15 секунд | H3 / актуальный Context-IR API |
| Частота кадров | 24 fps | системный обзор H3 |
| Звук | нативный stereo, 32 kHz | системный обзор H3 |
| Базовое разрешение | короткая сторона 768 px | H3-Base |
| 2K | H3-Regenerate-2K, а не обычный upscale | официальный серверный pipeline |
| Языки речи | 11 стабильно поддерживаемых, включая русский | системный обзор H3 |
| Ref2VA: изображения | до 9 | модель/API |
| Ref2VA: видео | до 3; каждое 2–15 с; суммарно до 15 с | модель/API |
| Ref2VA: аудио | до 3; каждое 2–15 с; суммарно до 15 с | модель/API |
| Суммарное число reference-файлов | до 12 | системный обзор H3 |
| Context-IR request body | до 64 MB; для больших файлов рекомендованы публичные URL | актуальный API |
| Изображение для API | JPG/JPEG/PNG/WEBP/HEIC/HEIF, до 30 MB, 256–5760 px, aspect 0.4–2.5 | актуальный Context-IR API |
| Reference video для API | MP4/MOV; H.264/H.265; до 50 MB; 23.976–60 fps | актуальный Context-IR API |
| Reference audio для API | WAV/MP3, до 15 MB | актуальный Context-IR API |

Важная граница: open-weight H3-Base, официальный Context-IR API, Hailuo/Hub и сторонние ComfyUI-ноды — разные поверхности. Их поля, ограничения и маршрутизация могут различаться. Перед реальной отправкой проверяется документация именно используемой поверхности.

### Расхождение по audio-only

Официальный китайский README говорит, что reference audio должно использоваться вместе с изображением или видео и не может быть единственным входом. Английский README не содержит этой оговорки, а текущая страница Context-IR описывает Ref2VA как сочетание `reference_image`, `reference_video` и `reference_audio`, не формулируя тот же запрет. Поэтому безопасное production-правило: для переносимого Ref2VA-пакета сопровождать audio визуальным входом, но передавать это не как вечное свойство H3, а как surface-specific ограничение, которое надо повторно проверить перед запуском.

## 3. Выбор режима

| Режим | Когда использовать | Что считается якорем |
| --- | --- | --- |
| T2VA | нет медиа-якорей, всё строится из текста | весь таймлайн описывает prompt |
| I2VA | изображение должно быть точным первым кадром | `<Picture 1>` в 0.00 с |
| FL2VA | есть точный первый и последний кадры | Picture 1 в 0.00 с, Picture 2 в конце |
| L2VA | изображение должно быть точным последним кадром | `<Picture 1>` в конце |
| Ref2VA | нужны персонажи, одежда, локации, стиль, движение, монтаж, продолжение, голос, музыка или другие reference-связи | `<Subject>`, `<Picture>`, `<Video>`, `<Audio>` и retention-связи |

Не выбирать Ref2VA только потому, что загружено изображение. Если изображение должно буквально стать первым или последним кадром, это keyframe-задача. Если оно даёт внешность, одежду, стиль или локацию без покадрового совпадения — это reference generation.

На текущем Context-IR API keyframe-роли `first_frame`/`last_frame` и reference-роли `reference_image`/`reference_video`/`reference_audio` взаимно исключают друг друга в одном запросе.

## 4. Бриф до написания промпта

Минимальная входная карточка:

```text
Цель ролика:
Режим, если известен:
Длительность: 4–15 секунд
Соотношение сторон:
Поверхность: API / Hailuo / Hub / локальный H3 / ComfyUI

Picture 1 — что именно брать и что не брать:
Picture 2 — что именно брать и что не брать:
Video 1 — editing / continuation / camera / motion / rhythm / другое:
Audio 1 — fully copy / partial copy / voice timbre / delivery / beat / music style / SFX:

Главный герой и действие:
Начальное состояние:
Конечное состояние:
Камера:
Свет и стиль:
Точный текст речи/песни и язык:
Физический звук:
Музыка для зрителя или N/A:
Что критично сохранить:
Что допускается изменить:
```

Если пользователь не указал несущественную деталь, можно сделать разумное предположение. Нельзя придумывать точные слова, lyrics, роль неизвестного референса или смешивать личности без явного решения.

## 5. Нативная структура base-режимов

### T2VA

```text
integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

### I2VA

```text
For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

### FL2VA

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot N) aligns with the S.SS-second mark of the target video.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

`N` — реальный номер финального shot, `S.SS` — фактическая длительность с двумя знаками после точки. FL2VA обычно выигрывает от одного непрерывного shot: промпт описывает достижимый путь между двумя состояниями, а не две статичные картинки.

### L2VA

```text
How the reference pictures align with the target video — <Picture 1> (from [Shot N]) aligns with the S.SS-second mark of the target video.

integrated_multimodal_description: [Shot 1] ...

overall_soundscape: ...

non_diegetic_music: ...
```

Здесь Picture 1 — финал, а не старт. Нужно вывести из него правдоподобное предыдущее состояние и постепенно привести к точной конечной композиции.

## 6. Нативная структура Ref2VA

Порядок секций фиксирован:

```text
subject_definitions:
...

summary:
...

retention_analysis:
...

detailed_description:
...

overall_soundscape:
...

non_diegetic_music:
...
```

### 6.1 Что означает каждый label

| Label | Правильное назначение |
| --- | --- |
| `<Subject N>` | повторно используемый видимый объект: персонаж, животное, предмет, одежда, среда, стиль, действие, поза, эффект |
| `<Picture N>` | само изображение является первым/последним/keyframe/composition/storyboard-якорем |
| `<Video N>` | целое видео является источником editing, continuation, camera/cut/rhythm/temporal structure |
| `<Audio N>` | аудиосигнал копируется или служит reference для тембра, delivery, текста, музыки, ритма, SFX или continuity |

Если картинка нужна только как источник внешности или костюма, отдельная строка `<Picture N>` не нужна: источник упоминается внутри определения `<Subject N>`.

Один файл может дать несколько subjects. Один subject может собираться из нескольких файлов, если точно сказано, что даёт каждый источник.

### 6.2 Типы задачи в `summary`

Использовать только подходящие отношения:

- `[keyframe completion]`
- `[reference generation]`
- `[video editing]`
- `[video continuation]`
- `[audio reuse]`
- `[audio reference]`

Комбинировать через ` + ` без дубликатов, например:

```text
[video editing + audio reuse] ...
[reference generation + audio reference] ...
[video continuation + keyframe completion] ...
```

Наличие video/audio само по себе не означает editing/reuse. Если video даёт лишь движение камеры, это обычно `reference generation`. Если audio даёт только тембр, а сигнал не копируется, это `audio reference`.

### 6.3 Retention markers

Видимое содержание:

- `fully_preserved` — роль и определённые признаки сохраняются полностью;
- `partially_preserved` — часть определённых признаков меняется или не используется;
- `attribute_transfer` — признаки переносятся на другой идентифицируемый subject;
- `weak_reference` — сохраняется лишь широкое сходство стиля, категории, композиции или атмосферы.

Audio:

- `fully_copy` — весь сигнал является полной финальной дорожкой;
- `partially_copy` — копируется часть времени/слоёв или после копирования меняется микс;
- `reference` — сигнал не копируется, используются тембр, ритм, стиль, текст или sound texture;
- `weak_reference` — только широкое сходство категории/атмосферы.

Это язык намерения для модели, а не гарантия побитового или визуально идеального результата.

## 7. Таймлайн и камера

- `[Shot 1]` не имеет timestamp.
- Каждый следующий cut начинается с `[Shot N] At MM:SS.mmm, ...`.
- Времена строго возрастают и не выходят за длительность.
- Cut должен приносить новую информацию: новый ракурс, состояние, пространство или время. Для небольшого изменения дистанции лучше движение камеры.
- Камера описывается естественной фразой внутри действия: тип движения, а при необходимости амплитуда и скорость.

Рабочая микроструктура shot:

```text
composition → visible subject and position → action/state change → environment response → camera → expression/dialogue → synchronized sound → end state
```

За 4–6 секунд обычно помещается одно главное действие и одно поведение камеры. За 10–15 секунд возможны 2–4 shots, если диалог и переходы реально укладываются. Количество shots не является метрикой качества.

Для Ref2VA generation официальный ориентир `detailed_description` — обычно 350–500 английских слов. Это не квота: dialogue-heavy сцена подчиняется длительности речи, editing-задача — сложности исходного видео. Утверждение «400–700 слов всегда лучше» официальными guides не подтверждается.

## 8. Речь, песни и текст в кадре

Speaker ID назначается по порядку реальных голосовых событий и остаётся стабильным:

```text
<Subject 1> (S1) says in a calm adult Russian voice: <d>[Russian] Я вернулся.</d>
```

Внутри `<d>` находятся только язык и точные слова. Описание голоса, действие, эмоция и speaker ID остаются снаружи.

Для физически говорящего reference-персонажа писать и subject, и speaker:

```text
<Subject 1> (S1) turns toward the lens and says, <d>[Russian] Теперь всё начнётся.</d>
```

Voiceover:

```text
<Subject 1> (S1) says in an off-screen voiceover: <d>[Russian] Я помню этот день.</d> while his on-screen lips remain completely closed.
```

Если одна реплика пересекает cut, используются `<scenetrans>` и явное указание непрерывности аудио. Если видео обрывает речь, используется `<cutoff>`. Эти маркеры нельзя придумывать без реальной временной необходимости.

Видимый текст в сцене пишется в английских двойных кавычках, но сохраняется дословно и без перевода:

```text
A red sign reading "ВХОД" glows above the door.
```

Неразборчивый reference-audio фрагмент маркируется `[unclear]`, а не угадывается.

## 9. Три разных слоя звука

1. Синхронные речь, пение, музыкальный инструмент или конкретный SFX в текущем shot описываются внутри основного таймлайна.
2. `overall_soundscape` — 1–4 предложения про ambience, физические действия и невербальные звуки. Речь и lyrics здесь не повторяются. `N/A` используется только при явно запрошенной полной тишине.
3. `non_diegetic_music` — 1–3 предложения про музыку только для зрителя: инструменты, tempo, rhythm, dynamics. Если такой музыки нет — `N/A`.

Музыка из радио/телефона/сцены является diegetic и остаётся в таймлайне, а не в `non_diegetic_music`.

## 10. Production-шаблон Ref2VA

```text
subject_definitions:
<Subject 1> is [target character] derived from <Picture 1>. <Picture 1> provides [identity traits only].
<Subject 2> is [costume/prop] derived from <Picture 2>. Only [attributes] are referenced; no human identity from <Picture 2> belongs to <Subject 1>.
<Subject 3> is [environment] derived from <Picture 3>, including [stable spatial and lighting anchors].
<Audio 1> is the [voice-timbre / delivery / music / signal] reference for <Subject 1> (S1).

summary:
[reference generation + audio reference] Create a [duration]-second [style] target video in which <Subject 1> wears <Subject 2> and performs inside <Subject 3>. <Audio 1> guides [specific audio properties] without copying the source signal.

retention_analysis:
<Subject 1> (appears in [Shot 1], ...): fully_preserved - preserve [identity/appearance attributes] across the full target video.
<Subject 2> (appears in [Shot 1], ...): attribute_transfer - transfer only [costume/prop attributes] onto <Subject 1>; do not transfer source identity.
<Subject 3> (appears in [Shot 1], ...): fully_preserved - retain [environment anchors].
<Audio 1>: reference - use [timbre/delivery/rhythm] without copying the original signal.

detailed_description:
The target video uses [visual style, lighting, texture, color behavior].
[Shot 1] [composition, subject, blocking, action, camera, synchronized sound, dialogue].
[Shot 2] At 00:XX.XXX, [new information and causal continuation].

overall_soundscape:
[Ambience and physical sounds only.]

non_diegetic_music:
[Instrumentation, tempo, rhythm, dynamic arc, or N/A.]
```

## 11. Identity и attribute transfer

Сильный identity block должен описывать уникальные стабильные признаки один раз, а не повторять «same face» в каждом предложении:

```text
<Subject 1> is the authorized adult fictional performer derived exclusively from <Picture 1>. <Picture 1> is the sole source of the performer's human identity: recognizable head and facial geometry, hairline, hairstyle, eye shape and spacing, eyebrows, nose, mouth proportions, jawline, skin characteristics, apparent age, and natural facial asymmetries.
```

В retention:

```text
<Subject 1> (appears throughout the target video): fully_preserved - maintain one recognizable identity across every frame. Blinking, smiling, speaking, and head movement are natural motion of the same face rather than changes to facial structure or apparent age.
```

Для одежды с фотографии другого человека:

```text
<Subject 2> is the dark wool coat derived from <Picture 2>. Only the coat's cut, fabric, collar, buttons, and color are referenced; the person wearing it in <Picture 2> does not define any target identity.
```

```text
<Subject 2> (appears throughout the target video): attribute_transfer - transfer only the coat's defined clothing attributes onto <Subject 1>; preserve <Subject 1>'s body and identity.
```

## 12. Lip-sync и точная аудиодорожка

Для сгенерированной речи сначала задаются speaker, точный текст и временно достижимая delivery, затем коротко фиксируется артикуляция:

```text
<Subject 1> (S1) sings in a natural adult Russian voice with clear native articulation and restrained phrasing: <d>[Russian] Я слышу ветер над рекой.</d> Visible lip and jaw articulation follows (S1)'s sung phonemes while preserving <Subject 1>'s identity and natural facial anatomy.
```

Если Audio 1 задаёт только голос:

```text
<Audio 1> is the voice-timbre and singing-delivery reference for <Subject 1> (S1).
```

```text
<Audio 1>: reference - its vocal timbre, Russian pronunciation, melodic delivery, and rhythmic phrasing guide <Subject 1> (S1) without copying the source signal.
```

Если требуется использовать готовый сигнал как финальную дорожку:

```text
<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the target video's complete final audio track.
```

Даже `fully_copy` является запросом к pipeline, а не доказательством результата. Для коммерчески точного мастера нужно проверить waveform/duration после генерации; при необходимости сохранить исходную дорожку отдельно и выполнить контролируемый remux в монтаже.

## 13. Полный сильный пример Ref2VA

Сценарий: Picture 1 — разрешённая внешность вымышленного певца; Picture 2 — костюм; Picture 3 — интерьер; Audio 1 — пример тембра, но не готовый soundtrack; 12 секунд, 16:9.

```text
subject_definitions:
<Subject 1> is the authorized adult fictional male performer derived exclusively from <Picture 1>. <Picture 1> is the sole source of his recognizable facial identity, head shape, hairline, short dark hair, eye spacing, nose, mouth proportions, jawline, skin characteristics, apparent age, and natural facial asymmetries.
<Subject 2> is the tailored charcoal wool suit, white shirt, and narrow black tie derived from <Picture 2>. Only the garments' cut, materials, fit, and colors are referenced; no human identity from <Picture 2> belongs to <Subject 1>.
<Subject 3> is the small 1960s recording studio derived from <Picture 3>, including the warm wood wall panels, suspended ribbon microphone, amber practical lamps, dark acoustic curtains, and compact spatial layout.
<Audio 1> is the voice-timbre and restrained singing-delivery reference for <Subject 1> (S1), containing an adult male Russian vocal sample.

summary:
[reference generation + audio reference] Create a 12-second realistic cinematic performance in which <Subject 1> wears <Subject 2> and sings inside <Subject 3>. <Audio 1> guides (S1)'s vocal timbre, Russian pronunciation, and measured delivery without copying the source signal.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - preserve one recognizable facial identity, head geometry, hair, apparent age, skin characteristics, and natural asymmetries across every shot.
<Subject 2> (appears in [Shot 1], [Shot 2], [Shot 3]): attribute_transfer - transfer only the charcoal suit, white shirt, and narrow black tie onto <Subject 1>; do not transfer any identity from <Picture 2>.
<Subject 3> (appears in [Shot 1], [Shot 2], [Shot 3]): fully_preserved - retain the studio's wood panels, ribbon microphone, amber practical lamps, acoustic curtains, and intimate spatial proportions.
<Audio 1>: reference - use its adult male vocal timbre, native Russian articulation, and restrained phrasing to guide (S1) without copying the original audio signal.

detailed_description:
The target video uses a realistic cinematic performance style with warm tungsten practicals, soft facial contrast, restrained film grain, natural skin texture, and shallow but stable depth of field.
[Shot 1] A medium-wide frontal composition establishes <Subject 3>. <Subject 1>, wearing <Subject 2>, stands one step behind the suspended ribbon microphone, shoulders relaxed and hands resting naturally at his sides. The camera pushes in with small amplitude at slow speed. He takes one quiet breath, raises his eyes toward the microphone, and begins to sing in the adult male Russian timbre referenced from <Audio 1>. <Subject 1> (S1) sings with clear native articulation and measured phrasing: <d>[Russian] Я слышу ветер над рекой.</d> His lips, jaw, cheeks, and breath follow the sung phonemes while his recognizable facial structure remains stable. The microphone stays fixed as the nearest amber lamp creates a small warm reflection along its metal grille.
[Shot 2] At 00:04.500, the camera cuts to a close three-quarter view from camera-left. <Subject 1> remains predominantly frontal enough for his eyes and mouth to stay readable. He shifts his weight slightly toward his front foot, one hand opening beside his thigh as the phrase continues. <Subject 1> (S1) sings in the same voice and tempo: <d>[Russian] Он помнит путь, которого нет.</d> His expression tightens briefly around the eyes, then releases; the suit fabric responds with a small delayed fold at the elbow. The acoustic curtain and wood panels remain spatially consistent behind him.
[Shot 3] At 00:08.500, the shot cuts to a tight frontal close-up with the microphone softly out of focus at frame-left. The camera holds static. <Subject 1> (S1) finishes in the same referenced timbre, <d>[Russian] И всё же я иду вперёд.</d> Exactly when the final word ends, his jaw stops speaking motion, his lips meet naturally, and he exhales through his nose. He keeps his gaze just past the microphone while the amber reflections settle into a quiet final frame.

overall_soundscape:
Low recording-room ambience continues beneath the performance, with quiet breathing before the first line, subtle suit-fabric movement during the weight shift, and a faint mechanical resonance from the suspended microphone mount. No additional crowd, exterior, or environmental sounds are present.

non_diegetic_music:
Sparse felt-piano notes at a slow tempo with a sustained low cello tone, remaining below the vocal and fading during the final exhale.
```

## 14. Сильные короткие шаблоны base-mode

### T2VA: продуктовый macro-shot, 6 секунд

```text
integrated_multimodal_description: [Shot 1] Live-action macro cinematography, a sealed glass perfume bottle stands on wet black stone in a dark studio. A narrow warm backlight outlines the amber liquid while cool mist moves slowly behind it. The camera trucks right with small amplitude at slow speed as one droplet slides from the metal cap down the front glass, bends the engraved label through refraction, and reaches the stone at the end of the shot. The bottle, label, cap, and liquid remain structurally unchanged.

overall_soundscape: A quiet studio room tone sits beneath the close, delicate sound of one water droplet moving over glass and touching wet stone.

non_diegetic_music: One sustained low synthesizer note with a soft high harmonic, gradually increasing in volume before stopping at the final frame.
```

### FL2VA: причинный переход, 8 секунд

```text
How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; Picture 2 (from Shot 1) aligns with the 8.00-second mark of the target video.

integrated_multimodal_description: [Shot 1] Live-action, cinematic, the woman begins in the pose, clothing, rainy street position, and medium-wide framing established by Picture 1, holding a closed black umbrella beside her leg. The camera pulls out with small amplitude at slow speed as she releases the retaining strap, raises the umbrella above her shoulder, pushes the runner upward until the canopy opens, then rotates the handle and takes one measured step beneath it. Rainwater rolls from the expanding fabric. Her movement progressively narrows every difference from Picture 2 until her posture, umbrella angle, spacing, camera perspective, and final composition match Picture 2 at the end of the shot.

overall_soundscape: Steady rain strikes pavement and fabric, followed by the metallic click of the umbrella runner and the soft snap of the canopy opening. Distant traffic remains low and continuous.

non_diegetic_music: N/A
```

## 15. Антипаттерны

- Писать Ref2VA как base prompt без `subject_definitions` и `retention_analysis`.
- Называть каждую reference-картинку `<Picture N>`, даже когда она только источник внешности или одежды.
- Смешивать `fully_preserved` и `attribute_transfer` без указания, что именно переносится и на кого.
- Использовать `video editing`, когда video даёт только движение камеры.
- Использовать `audio reuse`, когда нужен лишь тембр.
- Вставлять описание голоса внутрь `<d>` или переводить пользовательскую реплику.
- Повторять полный текст песни в `overall_soundscape` и `non_diegetic_music`.
- Ставить timestamp на Shot 1 или делать cut за пределами длительности.
- Требовать одновременно несовместимые движения камеры и композиционные якоря.
- Укладывать в пять секунд несколько действий, смену локации, длинную речь и сложный camera move.
- Считать 15 000 символов или количество `NO` признаком качества.
- Называть prompt «проверенным» без реального render и просмотра результата.

## 16. Финальный QC перед отправкой

### Schema

- выбран правильный режим;
- field names и порядок точные;
- keyframe и reference-роли не смешаны без подтверждения поверхности;
- каждый label определён один раз и означает одно и то же везде;
- retention охватывает все отдельно отслеживаемые references.

### Timeline

- длительность 4–15 секунд;
- Shot 1 без времени, следующие cut times строго возрастают;
- действия физически успевают завершиться;
- начальный/конечный frame явно достигнут;
- диалог помещается в отведённое время.

### Visual continuity

- identity, wardrobe, props и environment не конфликтуют;
- attribute transfer не переносит чужую личность;
- камера имеет одно понятное поведение на каждом отрезке;
- финальное состояние является следствием действия, а не новой несвязанной картинкой.

### Speech and sound

- speaker IDs стабильны;
- точные слова находятся только внутри `<d>`;
- язык не переведён;
- voiceover сопровождается закрытыми губами on-screen character;
- физический звук и audience-only score разведены;
- `N/A` используется осознанно.

### Truthfulness

- prompt помечен как compiled, а не generated;
- нет обещания идеального face lock, lip-sync или text rendering;
- ограничения относятся к конкретной поверхности;
- непроверенные community-наблюдения не выдаются за официальные возможности.

## 17. Практическая проверка после генерации

Один успешный task status не доказывает качество. Для A/B сравнения фиксировать seed и менять один фактор за раз. Проверять:

- реальную длительность, fps и наличие stereo audio через `ffprobe`;
- первый, средний и последний кадр, а также каждый cut;
- узнаваемость identity, возраст, одежду, руки и props;
- достижение first/last-frame anchor;
- lip-sync по началу, середине и последним 1–2 секундам;
- окончание движения губ вместе с окончанием речи;
- лишний ambience/music и фактическое соответствие copied/reference audio;
- целостность видимого текста;
- визуально — не только техническое наличие MP4.

## 18. Что из исходного исследования скорректировано

- Официальный ориентир Ref2VA `detailed_description` — обычно 350–500 английских слов, а не универсальные 400–700.
- Audio-only ограничение сформулировано неодинаково в официальных поверхностях; его нельзя объявлять вечным свойством модели.
- Утверждения о систематическом lip-sync drift, продолжающемся движении губ и неизбежном ambience не найдены в официальном guide. В официальных GitHub issues найден отдельный пользовательский report о коротком аудио-pop в начале результата; это bug report без подтверждённой причины и не правило prompting.
- `fully_copy` и `fully_preserved` — семантические relationship markers, а не гарантия качества или побитового совпадения результата.

## 19. Первоисточники

1. [Официальный MiniMax-H3 GitHub repository](https://github.com/MiniMax-AI/MiniMax-H3)
2. [Официальный README на проверенном commit](https://github.com/MiniMax-AI/MiniMax-H3/blob/d21241f0a4b3acbb34c97dae47fa417b7065e438/README.md)
3. [Официальный h3-prompt-writing skill](https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing)
4. [Официальный MiniMax-H3 на Hugging Face](https://huggingface.co/MiniMaxAI/MiniMax-H3)
5. [Base Prompt Writing Guide: T2VA / I2VA / FL2VA / L2VA](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)
6. [Full-Reference Ref2VA Prompt Writing Guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)
7. [MiniMax H3 Open: system overview, workflows and tools](https://design.minimax.io/h3)
8. [MiniMax Research: H3 design overview](https://www.minimax.io/blog/minimax-h3)
9. [H3-Context-IR API](https://platform.minimax.io/docs/api-reference/video-generation-v2-h3-context-ir)
10. [Video Generation v2 API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create)
11. [H3 Regenerate-2K API](https://platform.minimax.io/docs/api-reference/video-generation-v2-regeneration)
12. [MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE)
13. [Пользовательский GitHub issue #50 об audio pop](https://github.com/MiniMax-AI/MiniMax-H3/issues/50) — эмпирический report, не официальная гарантия и не подтверждённая причина.

Официальная команда установки исходного skill:

```bash
npx skills add https://github.com/MiniMax-AI/MiniMax-H3 --skill h3-prompt-writing
```

Локальная версия этого skill расширена production-cookbook, но официальные `base-en.txt` и `ref-en.txt` сохранены без изменений. Повторная установка upstream skill может перезаписать локальные дополнения.
