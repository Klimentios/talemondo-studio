// UI copy only. Never translate project data, reference roles, prompts or API payloads.
const copy = new Map([
  ['Генерация видео', 'Video Generation'], ['Видео студия', 'Video Studio'],
  ['Авто', 'Auto'], ['Да', 'Yes'], ['Нет', 'No'], ['Все', 'All'], ['Все категории', 'All categories'],
  ['Изображения', 'Images'], ['Фото', 'Photo'], ['Видео', 'Video'], ['Аудио', 'Audio'],
  ['Персонажи', 'Characters'], ['Локации', 'Locations'], ['Одежда', 'Clothes'], ['Предметы', 'Props'],
  ['Движение', 'Motion'], ['Стиль', 'Style'], ['Другое', 'Other'], ['сек', 'sec'],
  ['Авто — определить по содержимому', 'Auto — detect from content'], ['Лицо героя', 'Character face'],
  ['Персонаж целиком', 'Full character'], ['Костюм', 'Costume'], ['Окружение', 'Environment'],
  ['Исходное видео', 'Source video'], ['Камера', 'Camera'], ['Ритм сцены', 'Scene rhythm'],
  ['Голос', 'Voice'], ['Музыка', 'Music'], ['Звуковой эффект', 'Sound effect'], ['Атмосфера', 'Atmosphere'],
  ['Сцена + движение', 'Scene + motion'], ['Максимум исходного', 'Preserve most of source'],
  ['Только ориентир', 'Guidance only'],
  ['Генерация идёт', 'Generating'], ['Твой ролик появится здесь', 'Your video will appear here'],
  ['Ожидаем первый черновой кадр с GPU…', 'Waiting for the first draft frame from the GPU…'],
  ['Во время генерации здесь появятся черновые кадры, затем готовое видео.', 'Draft frames will appear here during generation, followed by the finished video.'],
  ['Генерация · черновой кадр', 'Generating · draft frame'], ['Готовый MP4', 'Finished MP4'],
  ['Пока нет генерации', 'No generation yet'],
  ['Кадр обновляется примерно раз в 6 секунд. Цвета и детали предварительные.', 'The frame updates about every 6 seconds. Colors and details are preliminary.'],
  ['Пропорции соответствуют ролику', 'Aspect ratio matches the video'],
  ['Сохраняем готовый MP4…', 'Saving the finished MP4…'], ['Черновик · ожидание сэмплинга', 'Draft · waiting for sampling'],
  ['Не удалось сохранить черновик', 'Could not save the draft'],
  ['Пресетов по этому запросу нет. Измени поиск или создай свой.', 'No presets match. Change the search or create one.'],
  ['Параметры не выбраны', 'No parameters selected'], ['Удалить пресет', 'Delete preset'],
  ['Кадр для палитры', 'Palette image'], ['Иллюстрация пресета', 'Preset image'],
  ['Цвета будут извлечены из изображения и сохранены вместе с пресетом.', 'Colors will be extracted from the image and saved with the preset.'],
  ['Изображение сохранится в локальной медиатеке и покажется на карточке.', 'The image will be saved to the local library and shown on the card.'],
  ['Нет подходящих референсов', 'No matching references'],
  ['Сначала добавь фото, видео или звук', 'Add a photo, video or audio first'],
  ['Добавь персонажа, место, одежду или видео движения', 'Add a character, location, outfit or motion video'],
  ['В промпте есть ссылка на удалённый или неизвестный файл. Замените её через @ перед отправкой.', 'The prompt refers to a removed or unknown file. Replace it with @ before submitting.'],
  ['Для каждой сцены выберите длительность 4–15 сек.', 'Choose a 4–15 second duration for each scene.'],
  ['Недопустимый размер', 'Invalid size'],
  ['Проверьте размер: параметр H3 от 0,20 до 0,98 и не выше нативного лимита.', 'Check the size: the H3 scale must be 0.20–0.98 and within its native limit.'],
  ['Добавь описание кадра сверху', 'Add a shot description above'],
  ['Ошибка импорта', 'Import error'], ['Имя героя', 'Character name'],
  ['Пока нет заданий', 'No jobs yet'], ['▶ Возобновить очередь', '▶ Resume queue'],
  ['⏸ Приостановить очередь', '⏸ Pause queue'],
  ['Пауза очереди · ждёт запуска', 'Queue paused · waiting to start'],
  ['Ждёт запуска на GPU', 'Waiting to start on GPU'], ['Передача референсов', 'Uploading references'],
  ['В очереди GPU', 'Queued on GPU'], ['Генерация на GPU', 'Generating on GPU'],
  ['Останавливается на GPU', 'Stopping on GPU'], ['Скачивание и проверка MP4', 'Downloading and checking MP4'],
  ['Готово', 'Done'], ['Ошибка', 'Error'],
  ['Нужна сверка: автоповтор запрещён', 'Reconciliation needed: automatic retry disabled'],
  ['Сервер потерял задание', 'Server lost the job'], ['Отменено', 'Cancelled'],
  ['Очередь пуста', 'Queue empty'], ['Ожидаем данные о шагах с GPU.', 'Waiting for step data from the GPU.'],
  ['Процент появится во время сэмплинга.', 'Progress will appear during sampling.'],
  ['Здесь будет показан следующий запуск и его этапы.', 'The next run and its stages will appear here.'],
  ['Задания появятся здесь после отправки.', 'Jobs will appear here after submission.'],
  ['Очередь приостановлена: новые задания не отправляются на GPU.', 'Queue paused: new jobs are not sent to the GPU.'],
  ['Очередь и готовые ролики', 'Queue and finished videos'], ['Текущие задания', 'Current jobs'],
  ['Текст задания и анализ референсов', 'Job text and reference analysis'],
  ['Сэмплинг: ожидаем данные о шагах с GPU', 'Sampling: waiting for step data from the GPU'],
  ['▶ Смотреть', '▶ Watch'], ['↓ Скачать', '↓ Download'], ['✎ Править', '✎ Edit'],
  ['↻ Повторить', '↻ Repeat'], ['■ Прервать рендер', '■ Stop render'], ['Отменить', 'Cancel'],
  ['Удалить', 'Delete'], ['Убрать ошибку', 'Hide error'],
  ['Локальный сервис временно недоступен', 'Local service temporarily unavailable'],
  ['Очередь недоступна', 'Queue unavailable'], ['Проверьте локальный сервис Studio.', 'Check the local Studio service.'],
  ['Mac очередь · GPU подключён', 'Mac queue · GPU connected'], ['Mac очередь · ждёт GPU', 'Mac queue · waiting for GPU'],
  ['Только предпросмотр · сервис не запущен', 'Preview only · service not running'],
  ['Это статический предпросмотр. Для рабочей очереди запусти локальный сервис H3 Studio.', 'This is a static preview. Start the local H3 Studio service to use the queue.'],
  ['Библиотека пуста. Загрузи первые файлы.', 'Library empty. Upload your first files.'],
  ['Мои файлы', 'My files'], ['Подборки', 'Collections'], ['Загрузить новые', 'Upload new'],
  ['Окружения', 'Environments'], ['Предметы', 'Props'], ['Костюмы', 'Costumes'], ['Стили', 'Styles'],
  ['Для всех', 'For everyone'], ['Животные', 'Animals'], ['Люди', 'People'],
  ['Любой тон', 'Any tone'], ['Серьёзный', 'Serious'], ['Ироничный', 'Playful'], ['Нейтральный', 'Neutral'],
  ['На разбор', 'Needs review'], ['Копии скрыты', 'Copies hidden'], ['Все копии', 'All copies'],
  ['Сохранить', 'Save'], ['Сохранено', 'Saved'], ['В сцену', 'Use in scene'],
  ['По этому запросу файлов нет. Измени поиск или категорию.', 'No files match. Change the search or category.'],
  ['Ничего не выбрано', 'Nothing selected'], ['Управлять', 'Manage'], ['Восстановить', 'Restore'],
  ['Убрать', 'Hide'], ['Файл и старые задания сохранятся', 'The file and older jobs will be kept'],
  ['Файлы не выбраны', 'No files selected'], ['Сначала выбери файлы', 'Select files first'],
  ['Укажи имя персонажа', 'Enter a character name'], ['Для персонажа выбери изображения', 'Choose images for a character'],
  ['Задание скопировано', 'Job text copied'], ['Не удалось скопировать. Выдели текст задания вручную.', 'Could not copy. Select the job text manually.'],
  ['Задание сохранено в очереди Mac', 'Job saved to the Mac queue'],
]);

const patterns = [
  [/^· кадр (\d+)$/u, (_, n) => `· shot ${n}`],
  [/^Кадр (\d+)$/u, (_, n) => `Shot ${n}`],
  [/^Черновик · шаг (\d+)\/(\d+)$/u, (_, a, b) => `Draft · step ${a}/${b}`],
  [/^Показано (\d+) из (\d+)$/u, (_, a, b) => `Showing ${a} of ${b}`],
  [/^Показано (\d+) из (\d+) · (\d+) копий скрыто$/u, (_, a, b, c) => `Showing ${a} of ${b} · ${c} copies hidden`],
  [/^Показать ещё · (\d+)$/u, (_, n) => `Show more · ${n}`],
  [/^Выбрано: (\d+)$/u, (_, n) => `Selected: ${n}`],
  [/^Выбрано файлов: (\d+)$/u, (_, n) => `Files selected: ${n}`],
  [/^(\d+,\d+) Мп$/u, (_, n) => `${n.replace(',', '.')} MP`],
  [/^(\d+) файлов добавлено в сцену$/u, (_, n) => `${n} files added to the scene`],
  [/^(\d+) файлов сохранено в библиотеке$/u, (_, n) => `${n} files saved to the library`],
  [/^(\d+) в работе\/ожидании · (\d+) всего$/u, (_, a, b) => `${a} running/waiting · ${b} total`],
  [/^Очередь пуста · (\d+) заданий в библиотеке$/u, (_, n) => `Queue empty · ${n} jobs in the library`],
  [/^Сэмплинг (\d+)% · (\d+)\/(\d+) шагов$/u, (_, p, a, b) => `Sampling ${p}% · ${a}/${b} steps`],
  [/^Сэмплинг: (\d+)% \((\d+)\/(\d+) шагов\)\. Затем декодирование и сохранение MP4\.$/u, (_, p, a, b) => `Sampling: ${p}% (${a}/${b} steps). Then decoding and saving MP4.`],
  [/^Фото · (.+)$/u, (_, name) => `Photo · ${name}`],
  [/^Видео · (.+)$/u, (_, name) => `Video · ${name}`],
  [/^Аудио · (.+)$/u, (_, name) => `Audio · ${name}`],
  [/^Просмотреть (.+)$/u, (_, name) => `Preview ${name}`],
  [/^Выбрать (.+)$/u, (_, name) => `Select ${name}`],
  [/^Удалить сцену (\d+)$/u, (_, n) => `Delete scene ${n}`],
  [/^Длительность сцены (\d+) в секундах$/u, (_, n) => `Scene ${n} duration in seconds`],
  [/^Роль файла (.+)$/u, (_, name) => `Role of ${name}`],
  [/^Имя героя для (.+)$/u, (_, name) => `Character name for ${name}`],
  [/^Что сохранить из (.+)$/u, (_, name) => `What to preserve from ${name}`],
  [/^Название (.+)$/u, (_, name) => `Name of ${name}`],
  [/^Категория (.+)$/u, (_, name) => `Category of ${name}`],
  [/^Тип элемента (.+)$/u, (_, name) => `Element type of ${name}`],
];

export function translateAppText(value, locale = 'ru') {
  if (!String(locale).toLowerCase().startsWith('en')) return value;
  const raw = String(value);
  const trimmed = raw.trim();
  let result = copy.get(trimmed);
  if (!result) {
    for (const [pattern, format] of patterns) {
      const match = trimmed.match(pattern);
      if (match) { result = format(...match); break; }
    }
  }
  if (!result) return value;
  return raw.replace(trimmed, result);
}

const protectedContent = 'textarea, input, pre, #brief, .reference-name, .library-item-title, .library-item-category, .cut-preview, .job-reference-read, [data-user-content]';
const originals = new WeakMap();
const originalAttributes = new WeakMap();
export function nextLocalizedState(previous, current, locale) {
  const known = previous && [previous.original, previous.lastApplied, translateAppText(previous.original, 'en')].includes(current);
  const original = known ? previous.original : current;
  return { original, lastApplied: translateAppText(original, locale) };
}
function protectedNode(node) {
  const element = node.nodeType === 1 ? node : node.parentElement;
  return !element || Boolean(element.closest(protectedContent));
}
function translateNode(node, locale) {
  if (node.nodeType === 3) {
    if (protectedNode(node)) return;
    const state = nextLocalizedState(originals.get(node), node.nodeValue, locale);
    originals.set(node, state);
    if (node.nodeValue !== state.lastApplied) node.nodeValue = state.lastApplied;
    return;
  }
  if (node.nodeType !== 1) return;
  if (protectedNode(node) && !node.matches('input, textarea')) return;
  const attrs = ['aria-label', 'title', 'placeholder'];
  let values = originalAttributes.get(node);
  if (!values) { values = new Map(); originalAttributes.set(node, values); }
  for (const attr of attrs) {
    if (!node.hasAttribute(attr)) continue;
    const state = nextLocalizedState(values.get(attr), node.getAttribute(attr), locale);
    values.set(attr, state);
    if (node.getAttribute(attr) !== state.lastApplied) node.setAttribute(attr, state.lastApplied);
  }
  for (const child of node.childNodes) translateNode(child, locale);
}

export function installAppI18n(root, getLocale) {
  const refresh = () => translateNode(root, getLocale());
  refresh();
  const observer = new MutationObserver((records) => {
    const locale = getLocale();
    for (const record of records) {
      if (record.type === 'childList') for (const node of record.addedNodes) translateNode(node, locale);
      else if (record.type === 'characterData') translateNode(record.target, locale);
      else if (record.type === 'attributes') translateNode(record.target, locale);
    }
  });
  observer.observe(root, { childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ['aria-label', 'title', 'placeholder'] });
  window.addEventListener('studio:localechange', refresh);
  return () => { observer.disconnect(); window.removeEventListener('studio:localechange', refresh); };
}
