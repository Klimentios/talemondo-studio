# Гайд по промптам MiniMax H3 (Ref2VA) — v2, 2026-10-06

Цель: живые герои (эмоции, взгляд, поведение) + режиссура, постановка, операторская работа — при точном
лице/костюме из референсов. Гайд собран из официальных гайдов MiniMax (`VIDEO_PROMPT_WRITING_GUIDE_ref_en.md`,
`_base_en.md`), исходников узлов ComfyUI 0.37 на нашем сервере и разбора живых генераций 2026-10-06
(два задания «танец по фото»).

Главная мысль: **промпт = только то, что видно и слышно в кадре.** Всё техническое (длительность, размер,
шаги, кэш, роли файлов, цвет, музыка-референс, ключевые кадры) — это настройки Студии, они включают узлы.
Каждое лишнее предложение модель читает как содержание кадра — это и есть «информационный шум».

---

## 0. Язык H3 — что модель выполняет из задания (измерено 06.11.2026)

Обязательно для любого агента перед любой генерацией. Источник — замеры на одном сиде, правки кода после Qwen и
рабочие дубли (`docs/reference/H3_ENCODER_RESEARCH.md`). Раздел читает MCP `studio_playbook` целиком.

| Что задаём | Как H3 это выполняет | Как писать / где в Studio |
|---|---|---|
| Склейки | диапазон `[Shot 2: 1.63–3.40s]` не читается как склейка (+0,5…1,8 с); официальный `[Shot 2] At 00:01.630, the shot cuts to …` попадает (±0,05–0,28 с) | только «At». Маршруты замены пишут его сами (`video_swap_auto.shot_lines`) |
| Движение камеры | статика, наезд, отъезд, облёт (≈90–120°; LoRA `camera_motion` 0,8 — дальше) | глаголом движения. Ручная и панорама — слабо |
| Крупность | держится, только если действия шота в ней читаются: деталь + руки → средний; общий + мимика → средний | деталь — игра лица; общий — тело и жест; иначе крупность движением («pushes in to an extreme close-up»). Проверка шотов до GPU |
| Объектив, ГРИП, боке, перевод фокуса | игнорирует — резко всё и всегда | не тратить слова; поля оптики в форме на картинку не влияют |
| Свет и время суток | сильнее всего, перебивает время суток фото | писать прямо, один раз |
| Повторяющееся событие (молнии) | одно событие на шот | несколько вспышек — несколько шотов |
| Эмоции | каждая смена — своей фразой (без неё дуга пропадает); тайм-коды `At 0:02 … At 0:03 …` ставят биты ±0,5 с; «сильнее» весом нельзя | эмоцию писать отдельной фразой с секундой |
| Реплика `<d>…</d>` | управляет звуком, почти не трогает картинку | — |
| Картинки | опорные кадры на склейках слушаются сильнее всего; фото героев в латентном пути — слабее всего (облик идёт через код Qwen и кадры сцены) | кадр сцены на каждый шот и внутри длинного шота каждые ~2 с |
| Видео-референс | 19 % токенов, 4 % внимания: даёт движение и раскладку | на рескине — кадры из кадров исходника |
| Формат | любой в холсте H3 (короткая сторона 768, площадь ≤ 768×1344, кратно 32) | 16:9, 9:16, 4:3, 3:4, 1:1; замена мира — формат исходника |
| Детерминизм | тот же граф и сид = побайтно тот же ролик | другой дубль — новый сид или текст, не повтор |
| Скорость без потери качества | Safe cache −23 % (PSNR 39 на рабочем дубле); `ref-match` −14 % (8 картинок); без Qwen −2,5 %; SageAttention 1.x медленнее | Safe — в замене мира; `ref-match` — после просмотра пользователем |

**Два типа работы с видео-референсом:** (1) замена только персонажей — исходное окружение, свет, звук, движение остаются, пары «кто → на какого героя» задаёт пользователь (`video_swap_auto`); (2) рескин мира по мэппингу — из исходника камера, движение, планировка, позы, тайминг, эмоции, время суток; из франшизы герои и облик окружения (`world_swap_auto`). Без принятого рецепта пользователя оба маршрута берут встроенные умолчания (BF16, 24 шага, 0,8 Мп); подробно — `skills/minimax-video-production`.

---

## 1. Как модель на самом деле читает промпт (проверено в коде)

| Факт | Что из этого следует |
|---|---|
| Текст идёт в Qwen3-VL-32B **без шаблона чата, без обрезки длины**: каждое слово — условие для каждого кадра | Нет «служебных» фраз. «Do not show captions» = модель думает о субтитрах. Повтор = двойной вес |
| **Нет негативного промпта** (BasicGuider, cfg 1) | Запреты не работают. Только позитивные описания |
| Модель обучена на 6 секциях английского текста (H3-Context-IR) | Любые блоки не из этих 6 секций (CAPS-контракты, «DIRECTOR STATE», «AUDIO DIRECTION») — чужой для модели формат |
| Метки привязаны к файлам по порядку: **все картинки → все видео → все аудио** | `<Picture 1>` = первая картинка в Студии. Звук видео получает свою `<Audio N>` перед видео |
| **Картинка, упомянутая только внутри `<Subject N>`, = источник внешности.** Отдельная строка `<Picture N> is …` = кадр (первый/ключевой) | Лицо/костюм — только внутри `<Subject>`. Иначе модель вставит фото как кадр |
| **Видео-референс НЕ привязан к кадрам ролика.** Он стоит до ролика во времени; Qwen видит его с частотой 2 кадра/с | «Повтори движение в движение, каждый наклон головы» модель выполнить не может — она берёт стиль, ритм, характер движения. Чем больше требуешь копию, тем больше «кукла» |
| Видео короче ролика **не растягивается и не зацикливается** | Видео движения ≥ длительности ролика. Иначе вторая половина — без движения (пример: 7,5 с видео на 15 с ролик → модель заполнила крупными планами лица) |
| Фото лица идёт в размере до 2048 px (`ref_image_size=max`) | Сильная хватка лица, но крупное фото лица тянет модель **начать ролик с крупного плана** (оба ролика 2026-10-06 открылись сверхкрупным лицом) |
| Длина 4–15 с, 24 к/с, кадры 17k+5 | 5 с = 124 кадра, 10 с = 243, 15 с = 362 |
| Нативный холст: короткая сторона 768, ≈1 Мп | 9:16 → 768×1344 или 704×1216; выше — без новых деталей |

## 2. Что пишем в промпте, а что — настройками Студии

| Это | Где | Почему |
|---|---|---|
| Кто в кадре, где, что делает, что чувствует, куда смотрит, что слышно | **Промпт** | Это видно/слышно |
| Свет (источник, направление, характер) | **Промпт**, 1–2 фразы в стилевой фразе и шоте | Модель рисует свет из текста |
| Камера (тип, амплитуда, скорость, крупность) | **Промпт** официальными словами (§6) | Обучена на этом словаре |
| Длительность, формат, Мп, шаги (≥24), кэш, seed | **Студия** | Параметры графа; в тексте бесполезны |
| Роль каждого файла (лицо / костюм / движение / голос) и владелец | **Студия** (+ одна фраза в `<Subject>`) | Студия строит граф и метки |
| Цветокоррекция «как на фото» | **Студия → узел H3ColorGrade** | Точнее слов «тёплый янтарь» |
| Музыка-референс, голос-референс | **Студия → аудио-слот** (+ строка `<Audio N>`) | Звук не попадает в Qwen как текст — только сигнал |
| Первый / последний / ключевой кадр | **Студия → AddGuide** (+ строка `<Picture N> is the first frame…`) | Жёсткая привязка к кадру |
| Звук видео движения | **Выключен** (`use_video_audio=false`) | Иначе музыка/голос исполнителя течёт в ролик |
| Пресеты Студии (камера/объектив/свет/цвет) | **Только как подсказка автору**. В шестисекционном промпте Студия их не применяет | Иначе дубли и спор с текстом (пример: «дневной свет» против «тёплых ламп») |
| LoRA, триггер-слова | **Студия** | Студия вставит триггер сама |

## 3. Бюджет слов (официальный)

| Секция | Объём | Содержание |
|---|---|---|
| `subject_definitions` | 1 строка на субъект, 25–50 слов | Кто это + из какого файла что берётся + приметы **с фото** |
| `summary` | 1–2 предложения | `[reference generation] …` — что в итоге видим |
| `retention_analysis` | 1 строка на субъект/видео/аудио, ≤40 слов | Маркер `fully_preserved` / `attribute_transfer` / `weak_reference` + что именно |
| `detailed_description` | **350–500 слов** | Стилевая фраза + шоты по времени |
| `overall_soundscape` | 1–4 предложения | Атмосфера, шаги, дыхание, ткань |
| `non_diegetic_music` | 1–3 предложения или `N/A` | Инструменты, темп, динамика (без «настроения») |

Весь промпт: **≈550–800 слов, 3,5–5,5 тыс. знаков.** Больше 6 тыс. — уже шум. Наш неудачный пример был 15,5 тыс.

## 4. Режиссура «живого» героя — главное

Модель делает живым только то, что описано **как событие во времени с причиной**. «Лицо живое и
выразительное» — пустая фраза. Работает так:

**4.1. Дай глазам цели.** В сцене должны быть 3–4 конкретных объекта, на которые герой смотрит:
окно и улица за ним, своё отражение в стекле, дверь, телефон на полу, луч света, другой человек, источник звука.
Пиши, **куда** и **почему** смотрит, и **когда** переводит взгляд.

**4.2. Такты по 2–4 секунды.** Каждый такт = триггер → реакция → действие:
`Around the third second a door slams somewhere behind her; her eyes flick toward the sound, her brows lift for a
moment, then she laughs it off and turns back into the step.`

**4.3. Взгляд в камеру — максимум 1–2 раза за ролик и с мотивом** (`as if catching a friend filming her`).
Постоянный взгляд в объектив = «стеклянные» глаза и мёртвая улыбка.

**4.4. Эмоция меняется (арка), а не держится.** Начало → поворот → финал:
`concentration with lips pressed together → a surprised half-smile when she nails the turn → open, breathless laughter`.

**4.5. Тело и дыхание:** вдох перед движением, плечи, руки поправляют волосы/ремень сумки, перенос веса,
пауза после сложного движения.

**4.6. Глаза — НЕ про резкость.** Фразы «sharp irises, crisp catchlights, natural pores, stable pupils» тянут
камеру к лицу и не оживляют. Глаза живые, когда они **двигаются по целям** (4.1) и **моргают по причине**
(после усилия, при смехе). Резкость глаз — это настоящие 24 шага (кэш Safe/Off) и лицо ≥ 150 px в кадре.

**4.7. Второстепенные люди тоже реагируют** (кивок, взгляд, полуулыбка), иначе они манекены.

## 5. Видео «Движение» — правильно

1. В `<Subject>`: `…whose dance movement comes from <Video 1>` (официальная формула).
2. В `retention_analysis`: `<Video 1> (body movement and rhythm): attribute_transfer - the performer's
   choreography, rhythm and energy are transferred to <Subject 1>, who keeps her own face, hair and costume.`
3. В шоте: **стиль и ритм танца из видео + своя игра лица**:
   `She dances the routine from <Video 1> with its rhythm and energy, while her face and eyes react to the room.`
4. **Не пиши** «move for move», «every head tilt / arm path matches». Голова и взгляд — из твоей режиссуры.
5. Видео ≥ длительности ролика. Видео 7,5 с → ролик 7–8 с (или склей/зацикли видео до загрузки).
6. Видео без заставок, надписей и логотипов — обрежь до загрузки (тогда про них не нужно писать ни слова).

## 6. Камера и кадр

- **Одна стилевая фраза + один объектив на весь ролик** (`a 35 mm lens perspective`). Не перечисляй 35/50/85.
- **Первое предложение `[Shot 1]` — крупность и камера открытия**: `[Shot 1] A medium-full shot from the knees up
  frames her in the middle of the studio…` — так меньше шанс, что модель начнёт с фото лица.
- Крупность для лица: от колен / от бедра (лицо ~150–250 px). Полный рост — только если важны ноги, и тогда
  смирись с меньшей точностью лица (или «Уточнение героя» вторым проходом).
- Движение камеры — официальными словами, тип + амплитуда + скорость, одно на шот:
  `The camera tracks with her at a steady distance` · `pushes in with small amplitude at slow speed` ·
  `pulls out` · `pans` · `trucks` · `tilts` · `pedestals` · `arc shot` · `static shot` · `shakes slightly`.
- Камера **мотивирована действием**: наезд — на эмоцию, отъезд — на большое движение, удержание — на паузу.
- Склейка (`[Shot 2] At 00:07.500, the shot cuts to…`) — только если она даёт новую информацию. Хочешь один
  план — пиши один `[Shot 1]` и опиши, как камера держит крупность (`keeps her framed from the knees up throughout`).
- Видео с ролью «Движение и камера»: камеру не описывай, она из видео.

## 7. Свет, цвет, мир

- Свет одной фразой с источником: `soft overcast daylight from the tall factory windows on her left, the
  right side of her face falling into gentle shadow`.
- Цвет — либо одной фразой, либо картинкой-референсом цвета в Студии (H3ColorGrade). Не оба.
- Мир — 3–4 конкретные вещи, **с которыми можно взаимодействовать** (§4.1): не «minimal studio», а
  `a large loft with tall steel-framed windows, a radiator, a stack of chairs by the wall and a phone on a tripod`.

## 8. Звук

- `overall_soundscape`: пространство + физические звуки, совпадающие с действием: `sneaker squeaks on concrete
  on each turn, the rustle of the hoodie, her breathing growing heavier, faint street traffic through the windows`.
- Музыка: в `non_diegetic_music` — инструменты, темп, развитие (`a 110 BPM electronic pop beat with a muted bass
  line that drops out for one bar before the final pose`). Если нужен конкретный трек — аудио-слот «Музыка»
  в Студии + `<Audio N>` с маркером `reference`/`fully_copy`.
- Нет речи → нет `(S1)` и нет `<d>`. Хочешь тишину голоса — опиши то, что слышно (дыхание, смех без слов).
- Речь: `<Subject 1> (S1) says breathlessly, <d>[English] One more time!</d>` — голос описывай снаружи `<d>`.

## 9. Стоп-лист — что создаёт шум

| Убрать | Почему | Вместо |
|---|---|---|
| `Do not / No / never / without …` | Нет негатива; упоминание = подсказка | Позитивное описание (§1) |
| CAPS-блоки, «CONTRACT», «DIRECTOR STATE», «REFERENCE PRIORITY» | Чужой формат, двойной вес | Только 6 секций |
| Повтор примет в каждом абзаце | Тянет к лицу/крупному плану | Приметы 1 раз в `<Subject>` + 1 раз в первом шоте |
| «exactly the face from», «crisp», «sharp irises», «natural pores» ×6 | Камера едет в сверхкрупный | Лицо в кадре нужного размера + цели взгляда |
| «move for move», «every head tilt matches» | Кукла, нереализуемо | §5 |
| «meets the lens» несколько раз | Мёртвый взгляд в камеру | §4.3 |
| Мелкие приметы как главные («ears visible through hair») | Модель раздувает их (уши «эльфа») | Только заметные приметы |
| Русские слова вне `<d>` | Ломают английский IR | Перевод |
| Лекции про объективы/фокус | Модель рисует смену объективов | Один объектив одной фразой |
| Разные имена: Nova / NOVA | Два субъекта | Одно написание |

## 10. Пример: танец по фото (переписанный по гайду)

Настройки Студии: 9:16, 0,8 Мп, **10 с** (видео движения 10+ с), 24 шага, кэш **Safe**, звук видео выкл.,
файлы: Картинка 1 — «Лицо героя» (Nova), Картинка 2 — «Костюм» (Nova, ≥ 1000 px по длинной стороне),
Видео 1 — «Движение» (Nova).

```
subject_definitions:
<Subject 1> is Nova, a slim woman in her early thirties whose face and hair come from <Picture 1>, whose costume comes from <Picture 2>, and whose dance movement comes from <Video 1>: a chestnut-brown chin-length bob with short blunt bangs, warm dark-brown eyes, thick dark eyebrows, light skin with scattered freckles, and long dark drop earrings.
<Subject 2> is the sunlit loft studio: tall steel-framed windows, a pale concrete floor, a radiator under the windows, a stack of chairs by the far wall, and a phone on a small tripod near the camera.

summary:
[reference generation] <Subject 1> dances the routine from <Video 1> alone in <Subject 2>, rehearsing for the phone on the tripod; she starts focused, gets distracted by the street outside, and finishes laughing at her own final pose.

retention_analysis:
<Subject 1> (appears in [Shot 1]): fully_preserved - Nova's face, bob with bangs, dark-brown eyes, freckles, earrings and the costume from <Picture 2> are retained.
<Video 1> (body movement and rhythm): attribute_transfer - the performer's choreography, rhythm and energy are transferred to <Subject 1>, who keeps her own face, hair, build and costume.
<Subject 2> (appears in [Shot 1]): fully_preserved - the loft with tall windows, concrete floor and the phone on the tripod.

detailed_description:
The target video is live-action and cinematic, shot on a 35 mm lens in soft overcast daylight from the tall windows on her left, with a clean, slightly cool grade.
[Shot 1] A medium-full shot from the knees up frames <Subject 1>, Nova, in the middle of <Subject 2>, her chestnut bob and dark drop earrings catching the window light, wearing the costume from <Picture 2>. She glances down at the phone on the tripod, takes a breath, rolls her shoulders and starts the routine from <Video 1> on the first beat, lips pressed together in concentration, eyes fixed on an imaginary point just above the phone. The camera tracks with her at a steady distance, keeping her framed from the knees up as she travels across the floor.
Around the third second a car horn sounds outside; her eyes flick to the windows for a split second, her brows lift, and a small amused smile breaks her concentration before she snaps back into the rhythm.
On the turn she catches her own reflection in the dark window glass and checks it with a quick sideways look, then pushes the next move harder, her bob swinging and settling, the earrings swaying, her breathing now visible in her chest and shoulders.
Midway through, the bag strap slides off her shoulder; without stopping she hooks it back with two fingers, rolls her eyes at it with a quick crooked smile, and finds the beat again on the next step. A patch of brighter light from the window slides across the floor as she crosses it, warming her face for a moment and making her squint slightly before she turns away from it.
In the last part of the routine her face opens up: she is enjoying it, she bites her lip on a difficult step, and when she lands it she lets out a short breathless laugh. The camera pushes in with small amplitude at slow speed as she hits the final pose and holds it for a beat, chin up, looking straight into the phone lens like into a friend's eyes.
Then she drops the pose, lets her shoulders fall, laughs at herself, wipes a strand of hair off her forehead and glances toward the window again, still catching her breath.

overall_soundscape:
Sneaker squeaks and soft thuds on the concrete on every step and turn, the rustle of her clothes and the swing of the bag strap, her breathing growing heavier, one distant car horn and faint street traffic through the windows, and a short breathless laugh at the end.

non_diegetic_music:
An upbeat modern pop instrumental at about 110 BPM with a punchy kick and a muted bass line that drops out for one bar before the final pose, then resolves on a single held synth chord.
```

(≈ 380 слов в detailed_description, ни одного запрета, взгляд меняется 6 раз, у каждого изменения есть причина.)

## 11. Предложение для Студии (Codex): параметры в шапке промпта

Чтобы автор (или чат) задавал параметры там же, где пишет текст, а в H3 уходил чистый промпт:

```
#H3 duration=10 aspect=9:16 mp=0.8 steps=24 cache=safe seed=random video_audio=off
#H3 Picture1=face:Nova Picture2=costume:Nova Video1=motion:Nova
#H3 grade=Picture3 music=Audio1 first_frame=Picture4
subject_definitions:
...
```

Студия: читает строки `#H3`, выставляет настройки и роли → включает узлы (AddGuide для `first_frame`,
H3ColorGrade для `grade`, аудио-слот для `music`), **вырезает** строки `#H3` и отправляет в H3 только 6 секций.
Проверки перед отправкой (предупреждение, не молча):
- шагов < 24 → нельзя;
- видео движения короче длительности → предложить укоротить ролик;
- картинка роли «Лицо»/«Костюм» < 768 px по длинной стороне → предупредить;
- выбраны пресеты камеры/света/цвета + шестисекционный промпт → «пресеты не применяются»;
- в тексте есть `do not|no |never|CAPS-блок` → подсветить;
- `detailed_description` вне 300–600 слов → предупредить;
- имя героя пишется по-разному → предупредить.

## 12. Чек-лист перед запуском

- [ ] 6 секций, первая строка `subject_definitions:`, всё по-английски (кроме `<d>`).
- [ ] Каждый файл: роль и владелец; метки в тексте = файлы в Студии.
- [ ] Приметы — с фото, только заметные; одно имя героя.
- [ ] `detailed_description` 350–500 слов; первая фраза — стиль + свет; `[Shot 1]` начинается с крупности.
- [ ] Минимум 3 цели для взгляда в мире; эмоция меняется с причинами; в камеру ≤ 2 раз.
- [ ] Ни одного «do not / no / never»; нет CAPS-блоков; нет «crisp irises/pores».
- [ ] Видео движения ≥ длительности; звук видео выключен (если он не нужен).
- [ ] Шаги ≥ 24, кэш Safe (лицо/движение) или Off; фото костюма ≥ 1000 px.
- [ ] Пресеты Студии не противоречат тексту (в шестисекционном режиме они не применяются).

## 13. Что в старой инструкции для чата (`H3_CHAT_PROMPT_WRITER.md`) мешает

- §4: «в каждом шоте фраза про crisp face, sharp irises, catchlights» + «focus tracks her eyes» → тянет в
  сверхкрупный план и не оживляет. Заменить на §4 этого гайда (цели взгляда, такты).
- §4: «приметы в КАЖДОМ шоте» → перегруз лица. Достаточно `<Subject>` + первого шота + имени.
- §4а: «on-screen text, captions, logos… stay in the reference file» и «clean footage» — упоминание надписей.
  Лучше обрезать видео до загрузки и не писать об этом ничего.
- Нет правил: длина видео движения, цели взгляда, крупность открытия, запрет «move for move».
- §9 «кэш safe по умолчанию / fast для картинок» — по нашему правилу шаги ≥ 24 настоящих: Fast реально
  считает ≈10 из 24.

## 14. Что подтверждают обсуждения MiniMax H3 на Hugging Face

Проверено 2026-10-15. Пользовательские сообщения здесь — сигналы и гипотезы, не спецификация и не гарантия.

- В обсуждении [структуры Ref2VA](https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/71) сопровождающий MiniMax поясняет разные задачи секций: `subject_definitions` описывает референс, `retention_analysis` фиксирует сохранение/перенос, `detailed_description` описывает кадр и действие по шотам. Не требуется дублировать текст дословно. Отдельный Subject для костюма оправдан, когда костюм нужно отслеживать независимо; связь с героем должна быть явной.
- В обсуждении [следования промпту при разных разрешениях](https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/65) авторы сообщают о композиционных расхождениях на больших холстах. Это не контролируемое сравнение и не основание менять настройки Studio. Для A/B фиксировать референсы, длительность, seed, sampler, steps и текст; менять только разрешение, отдельно оценивать число/позиции героев, камеру, стартовый кадр и ключевое действие.
- Обсуждение [маленьких лиц и быстрых движений](https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/57) подтверждает жалобы на искажения, но не предлагает универсального технического исправления. Держим лицо читаемым, не перегружаем кадр одновременными действиями и оцениваем сам MP4.
- В теме [редкого моргания](https://huggingface.co/MiniMaxAI/MiniMax-H3/discussions/96) предложено просить конкретное действие взгляда в заданный момент. Это режиссёрский эксперимент: задавать видимый триггер и реакцию, затем проверять результат.

Полезные проекты для изучения, но не зависимости Studio: [ComfyUI-MiniMaxDirector](https://github.com/imbutus/ComfyUI-MiniMaxDirector) показывает временную шкалу и lint контракта; [ComfyUI-H3-OpenNodes](https://github.com/juntaosun/ComfyUI-H3-OpenNodes) — карточную привязку медиа к персонажу и сборку промпта. В Studio уже есть отдельная режиссура сцены и проверка связей; переносим идеи ясных ролей и проверяемого контракта, не дублируем workflow.

Space [multimodalart/minimax-h3](https://huggingface.co/spaces/multimodalart/minimax-h3) использует отдельную split-инфраструктуру и собственные AoTI/attention-модули. Это не drop-in замена нашего ComfyUI графа и не доказательство качества на нашей конфигурации.
