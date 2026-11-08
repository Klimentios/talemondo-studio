const STORAGE_KEY = 'h3-studio-locale';
const LANGUAGES = new Set(['ru', 'en']);

// The source markup stays in Russian. Only visible text and accessible labels are translated;
// option values, IDs, reference roles and API payloads are never changed.
const EN = {
  'TaleMondo — локальная очередь': 'TaleMondo — local queue',
  'TaleMondo — создание': 'TaleMondo — create',
  'Основные разделы': 'Main sections',
  'Разделы Studio': 'Studio sections',
  'Рабочие разделы': 'Work sections',
  'Вычисления': 'Compute',
  'Баланс, серверы и подходящие GPU для MiniMax H3 — в одном месте.': 'Balance, servers and compatible GPUs for MiniMax H3 in one place.',
  'Обновить состояние': 'Refresh status',
  'Баланс Vast': 'Vast balance',
  'Подключи API key, чтобы увидеть баланс': 'Connect an API key to see the balance',
  'Сервер Studio': 'Studio server',
  'Проверяем подключение…': 'Checking connection…',
  'Управление сервером ↓': 'Manage server ↓',
  'Подключение Vast.ai': 'Connect Vast.ai',
  'Ключ будет сохранён только в macOS Keychain.': 'The key is stored only in macOS Keychain.',
  'API key': 'API key',
  'Вставь API key Vast': 'Paste your Vast API key',
  'Проверить и подключить': 'Verify and connect',
  'Отключить Vast': 'Disconnect Vast',
  'Твои серверы': 'Your servers',
  'Открой сервер, чтобы посмотреть подключение, стоимость и доступные действия.': 'Open a server to view its connection, cost and available actions.',
  'Обновить': 'Refresh',
  'Сначала подключи Vast, затем обнови список.': 'Connect Vast, then refresh the list.',
  'Найти GPU для H3': 'Find a GPU for H3',
  'Фильтр уже учитывает требования полного H3 профиля. Выбор и аренда всегда подтверждаются вручную.': 'The filter includes full H3 profile requirements. You always confirm a selection and rental.',
  'Максимум за час, USD': 'Maximum per hour, USD',
  'Найти серверы': 'Find servers',
  'Подключи Vast и нажми «Найти серверы».': 'Connect Vast and select “Find servers”.',
  'Оплата, остановка и удаление выполняются в Vast. Остановленный сервер продолжает тарифицировать диск; удаление безвозвратно уничтожает данные на сервере. Файлы Studio на этом Mac не удаляются.': 'Billing, stopping and deletion are handled by Vast. A stopped server still incurs storage charges; deletion permanently removes its data. Studio files on this Mac are not deleted.',
  'Звук и речь': 'Sound and voice',
  'Проекты и исходные файлы хранятся на Mac.': 'Projects and source files stay on your Mac.',
  'Создать': 'Create',
  'Генерация видео': 'Video Generation',
  'Видео студия': 'Video Studio',
  'Сцены': 'Scenes',
  'Очередь': 'Queue',
  'Моя библиотека': 'My Library',
  'Подключение локальной очереди…': 'Connecting to local queue…',
  'НОВЫЙ ПРОЕКТ': 'NEW PROJECT',
  'Референс → видео': 'Reference → video',
  'Добавь героя и исходный ролик, опиши желаемое изменение. Всё в одном месте.': 'Add a character and source clip, then describe the change. All in one place.',
  '● Оригиналы и готовые MP4 сохраняются на Mac': '● Originals and finished MP4s stay on your Mac',
  'Состояние очереди': 'Queue status',
  'Очередь пуста': 'Queue is empty',
  'Задания появятся здесь после отправки.': 'Jobs appear here after submission.',
  'Открыть очередь ↓': 'Open queue ↓',
  'Результат генерации': 'Generation result',
  'Результат': 'Result',
  'Пока нет генерации': 'No generation yet',
  'Твой ролик появится здесь': 'Your video will appear here',
  'Результат появится после генерации. Исходные файлы можно открыть в задании ниже.': 'The result appears after generation. Source files are available in the job below.',
  'Редактировать задание': 'Edit job',
  'Результат появится только после реальной генерации': 'A result appears after an actual generation',
  'MiniMax H3 · полный BF16': 'MiniMax H3 · full BF16',
  'Референсы': 'References',
  'Герои, места, образы и движение для этого кадра': 'Characters, locations, looks and motion for this shot',
  '＋ Добавить': '＋ Add',
  'Типы референсов': 'Reference types',
  'Добавить фото, видео или аудио': 'Add an image, video or audio file',
  'Референс': 'Reference',
  'фото / видео / звук': 'image / video / audio',
  'Добавить лицо героя': 'Add a character face',
  'Лицо героя': 'Character face',
  'один герой': 'one character',
  'Добавить одежду героя': 'Add character clothing',
  'Одежда': 'Clothing',
  'укажи героя': 'name the character',
  'Добавить изображение окружения': 'Add a location image',
  'Окружение': 'Environment',
  'фото сцены': 'scene image',
  'Описание сцены': 'Scene description',
  '· кадр 01': '· shot 01',
  'Кто в кадре, что делает, как движется камера и что меняется к концу сцены…': 'Who is in the shot, what they do, how the camera moves and what changes by the end…',
  'Референсы, упомянутые в промпте': 'References mentioned in the prompt',
  'Напиши': 'Type',
  ', чтобы вставить референс в описание': ' to insert a reference into the description',
  'Упомянуть референс': 'Mention a reference',
  'Нужен локальный сервис': 'Local service required',
  'Поставить в очередь': 'Add to queue',
  'Создать видео': 'Create video',
  'Выбрать референс': 'Choose a reference',
  'Просмотр исходного файла': 'Source file preview',
  'Исходный файл': 'Source file',
  'Закрыть просмотр исходного файла': 'Close source file preview',
  'Задания и референсы сохраняются на Mac. При отключённом GPU очередь ждёт подключения.': 'Jobs and references stay on your Mac. The queue waits while the GPU is offline.',
  'Режиссура кадра': 'Shot direction',
  'Готовый профиль': 'Ready profile',
  'Готовый режиссёрский профиль': 'Ready director profile',
  'Свой набор': 'Custom set',
  'Параметры генерации': 'Generation settings',
  'Создать ролик': 'Create video',
  'Все настройки генерации': 'All generation settings',
  '⚙ Настройки': '⚙ Settings',
  'Сценарий': 'Mode',
  'Замена героя': 'Character swap',
  'Реализм': 'Realism',
  '3D-мультфильм': '3D animation',
  '2D-мультфильм': '2D animation',
  'Формат': 'Format',
  'Размер': 'Size',
  'Мегапиксели': 'Megapixels',
  '0,90 Мп': '0.90 MP',
  'Длина': 'Length',
  'сек': 'sec',
  'Шаги': 'Steps',
  'Случайный': 'Random',
  'Кеш MiniMax H3': 'MiniMax H3 cache',
  'H3 Safe · качество': 'H3 Safe · quality',
  'H3 Fast · быстрее': 'H3 Fast · faster',
  'Выкл. · эталон': 'Off · baseline',
  'Параметры H3: 672×1216 · 124 кадра · 24 шага · ref max': 'H3 settings: 672×1216 · 124 frames · 24 steps · ref max',
  'Выбери кадр, чтобы изменить его описание и режиссуру': 'Select a shot to edit its description and direction',
  'Настройки новой сцены': 'New scene settings',
  'Наследовать настройки': 'Inherit settings',
  'Начать с Auto': 'Start with Auto',
  '＋ Новая сцена': '＋ New scene',
  'Очередь и результаты': 'Queue and results',
  'Текущие задания': 'Current jobs',
  'Пока нет заданий': 'No jobs yet',
  'Вся история →': 'Full history →',
  'Приостановить очередь': 'Pause queue',
  'Удалить всё': 'Delete all',
  'Дополнительные параметры H3': 'Additional H3 settings',
  'Референсы, звук и текст задания': 'References, audio and job text',
  'Замена персонажа': 'Character swap',
  'Сохраняем сцену и движение исходного видео, меняем героя по фото-референсу.': 'Keep the source scene and motion while changing the character using an image reference.',
  '＋ Добавить сцену': '＋ Add scene',
  '＋ Ещё сцена / CUT': '＋ Another scene / CUT',
  'Детализация референсов': 'Reference detail',
  'Повтор движения из первого видео': 'Motion from first video',
  'Выкл. — видео как референс': 'Off — video as reference',
  'Поза человека — временно недоступно': 'Human pose — temporarily unavailable',
  'Контуры кадра — временно недоступно': 'Frame edges — temporarily unavailable',
  'Стилевая LoRA': 'Style LoRA',
  'Автовыбор Director': 'Director auto select',
  '2D Sketch · не назначается автоматически': '2D Sketch · not assigned automatically',
  'Звук исходного видео': 'Source video audio',
  'Многосценная генерация пока не подключена: несколько CUT можно планировать, но отправить как один H3 job нельзя.': 'Multi-scene generation is not connected yet: you can plan several cuts, but cannot submit them as one H3 job.',
  'Что получит H3': 'What H3 receives',
  'Скопировать текст': 'Copy text',
  'МЕДИАТЕКА': 'MEDIA LIBRARY',
  'Файлы остаются на Mac. Выбранные материалы получат номера для текущей сцены.': 'Files stay on your Mac. Selected assets receive numbers for the current scene.',
  'Закрыть библиотеку': 'Close library',
  'Источник референсов': 'Reference source',
  'Из библиотеки': 'From library',
  'Загрузить новые': 'Upload new',
  'Типы элементов': 'Asset types',
  'Поиск по имени или файлу': 'Search by name or file',
  'Поиск референсов': 'Search references',
  'Категория библиотеки': 'Library category',
  'Скрытые': 'Hidden',
  'Управлять': 'Manage',
  'Показать ещё': 'Show more',
  'Ничего не выбрано': 'Nothing selected',
  'Добавить в сцену': 'Add to scene',
  'Выбрать фото, видео или аудио': 'Choose images, videos or audio',
  'Можно выбрать несколько файлов сразу': 'You can select several files at once',
  'Тип элемента': 'Asset type',
  'Персонаж': 'Character',
  'Локация': 'Location',
  'Предмет': 'Prop',
  'Движение': 'Motion',
  'Стиль': 'Style',
  'Другое': 'Other',
  'Имя персонажа': 'Character name',
  'Например, HERO': 'For example, HERO',
  'Категория': 'Category',
  'Например, HERO / герои': 'For example, HERO / characters',
  'Роль в сцене': 'Role in scene',
  'Авто — определить по содержимому': 'Auto — detect from content',
  'Персонаж целиком': 'Full character',
  'Костюм': 'Costume',
  'Исходное видео': 'Source video',
  'Камера': 'Camera',
  'Голос': 'Voice',
  'Музыка': 'Music',
  'Добавить загруженные файлы в текущую сцену': 'Add uploaded files to the current scene',
  'Файлы не выбраны': 'No files selected',
  'Сохранить файлы': 'Save files',
  'H3 DIRECTOR · ПРЕСЕТЫ': 'H3 DIRECTOR · PRESETS',
  'Выбери визуальное направление для сцены.': 'Choose a visual direction for the scene.',
  'Поиск пресетов': 'Search presets',
  'Показать пресеты': 'Show presets',
  'Все': 'All',
  'Избранное': 'Favorites',
  'Недавние': 'Recent',
  'Мои пресеты': 'My presets',
  '＋ Свой пресет': '＋ Custom preset',
  'Настроить пресет': 'Configure preset',
  'Название': 'Name',
  'Например, HERO ночью': 'For example, HERO at night',
  'Кадр для палитры': 'Frame for palette',
  'Цвета будут извлечены из изображения и сохранены вместе с пресетом.': 'Colors will be extracted from the image and saved with the preset.',
  'Режиссёрское описание (необязательно)': 'Director description (optional)',
  'Необязательно — если пусто, описание соберётся из параметров': 'Optional — if empty, it is built from the parameters',
  'Отмена': 'Cancel',
  'Сохранить пресет': 'Save preset',
  'Авто': 'Auto',
  '＋ Референс для стиля': '＋ Style reference',
  'Настроить': 'Configure',
  'Сбросить': 'Reset',
  'Готово': 'Done',
};

const staticText = [];
const staticAttributes = [];
let initialized = false;
let locale = readSavedLocale();

function readSavedLocale() {
  try {
    const saved = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (LANGUAGES.has(saved)) return saved;
  } catch { /* storage blocked: fall back to the browser language */ }
  // No choice yet: a Russian browser gets Russian, every other one English (dom-translate.mjs).
  if (typeof document === 'undefined') return 'ru';
  const languages = globalThis.navigator?.languages?.length ? navigator.languages : [globalThis.navigator?.language || 'ru'];
  return String(languages[0] || 'ru').toLowerCase().startsWith('ru') ? 'ru' : 'en';
}

function collectStaticStrings() {
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const parent = node.parentElement;
    if (!parent || parent.closest('script,style')) continue;
    const match = node.nodeValue.match(/^(\s*)([\s\S]*?)(\s*)$/);
    const ru = match?.[2];
    if (ru && EN[ru]) staticText.push({ node, ru: node.nodeValue, en: `${match[1]}${EN[ru]}${match[3]}` });
  }
  for (const element of document.querySelectorAll('[aria-label],[placeholder],[title]')) {
    for (const attr of ['aria-label', 'placeholder', 'title']) {
      const ru = element.getAttribute(attr);
      if (ru && EN[ru]) staticAttributes.push({ element, attr, ru, en: EN[ru] });
    }
  }
}

function applyLocale(next) {
  if (typeof document === 'undefined') return;
  document.documentElement.lang = next;
  for (const entry of staticText) {
    if (!entry.node.isConnected) continue;
    if (entry.node.nodeValue === entry.ru || entry.node.nodeValue === entry.en)
      entry.node.nodeValue = next === 'en' ? entry.en : entry.ru;
  }
  for (const entry of staticAttributes) {
    if (!entry.element.isConnected) continue;
    const value = entry.element.getAttribute(entry.attr);
    if (value === entry.ru || value === entry.en)
      entry.element.setAttribute(entry.attr, next === 'en' ? entry.en : entry.ru);
  }
  const switcher = document.getElementById('localeSwitch');
  if (switcher) {
    switcher.dataset.locale = next;
    const label = next === 'en' ? 'Переключить на русский' : 'Switch to English';
    switcher.setAttribute('aria-label', label);
    switcher.title = label;
  }
}

export function getLocale() { return locale; }

export function setLocale(next) {
  if (!LANGUAGES.has(next)) return locale;
  const changed = next !== locale;
  locale = next;
  try { globalThis.localStorage?.setItem(STORAGE_KEY, next); } catch { /* Private storage can be unavailable. */ }
  applyLocale(next);
  if (changed && typeof window !== 'undefined')
    window.dispatchEvent(new CustomEvent('studio:localechange', { detail: { locale: next } }));
  return locale;
}

export function initLocaleSwitch() {
  if (initialized || typeof document === 'undefined') return;
  initialized = true;
  collectStaticStrings();
  const switcher = document.getElementById('localeSwitch');
  switcher?.addEventListener('click', () => setLocale(locale === 'ru' ? 'en' : 'ru'));
  applyLocale(locale);
}

if (typeof document !== 'undefined') initLocaleSwitch();
