// Explain the native Ref2VA choices without promising frame-exact pose transfer.
export function videoWorkflowState(references, mode, shotDuration, canRestore = false) {
  const videos = references.filter(ref => ref.kind === 'video' && ref.role !== 'Продолжение');
  if (!videos.length) return null;
  if (videos.length > 1) return {
    tone: 'info', title: 'Несколько видео-референсов',
    detail: 'Назначь роль каждому видео и свяжи исполнителей с героями. Роли определяют, что модель берёт из каждого файла.',
    actions: [],
  };
  const role = videos[0].role || '';
  let state;
  if (!role || role.startsWith('Авто')) state = {
    tone: 'warning', title: 'Что взять из видео?',
    detail: 'Выбери перенос движения без камеры или вместе с камерой. H3 использует видео как референс; точное покадровое повторение не гарантируется.',
    actions: [['motion', 'Движение'], ['motion-camera', 'Движение + камера'], ['source-scene', 'Вся сцена']],
  };
  else if (role === 'Движение' && mode === 'replace') state = {
    tone: 'warning', title: 'Для движения без камеры нужен «Реализм»',
    detail: 'Фото задают лицо и костюм, видео — движение, а описание — камеру. Совпадение внешности и движений проверь в готовом ролике. «Замена героя» на текущем сервере требует видео вместе с камерой.',
    actions: [['realistic', 'Выбрать «Реализм»']],
  };
  else if (role === 'Движение') state = {
    tone: 'ready', title: 'Видео даёт движение, камера остаётся твоей',
    detail: 'Передаются порядок действий и ритм. Это референс H3, не покадровое копирование поз.', actions: [],
  };
  else if (role === 'Движение и камера' && (videos[0].performers?.pairs?.length || 0) > 1) state = {
    tone: 'warning', title: 'Экспериментальный перенос нескольких героев',
    detail: 'Каждый герой получает движение своей пары, а внешность и костюм — из своих фото. В «Связать героев» укажи также относительный рост. Проверь весь ролик: при перекрытии модель может поменять героев местами или продублировать одного.', actions: [],
  };
  else if (role === 'Движение и камера') state = {
    tone: 'ready', title: 'Исполнение и камера — из видео, герой — из фото',
    detail: mode === 'replace'
      ? 'Фото задают героя и костюм; видео — исполнение и камеру. Локация и свет создаются заново. Проверь в готовом MP4 личности, масштаб и отсутствие чужой графики.'
      : 'Видео задаёт действия и камеру. Внешность и костюм — из фото героев; локация и свет создаются заново или берутся из назначенных фото. Чужие знаки и интерфейс в результат не входят.', actions: [['motion-template', 'Шаблон «Движение и камера»']],
  };
  else if (role === 'Исходное видео' && mode === 'replace' && videos[0].performers?.pairs?.length) state = {
    tone: 'ready', title: 'Замена в исходной сцене — проверенная схема',
    detail: 'Так сделаны все принятые замены. Каждому герою — одно фото «Персонаж целиком»; в описании распиши шоты: когда и где появляется каждый исполнитель и что он делает. Перед показом просмотри весь MP4: не осталось ли исходных персонажей.', actions: [],
  };
  else if (role === 'Исходное видео') state = {
    tone: 'info', title: 'Видео задаёт исходную сцену',
    detail: 'Сцена и её участники сохраняются. Для замены героя в этой сцене свяжи героев с исполнителями. Чтобы перенести только движение в новую сцену, выбери «Движение и камера».', actions: [],
  };
  else state = {
    tone: 'info', title: 'Видео используется по назначенной роли',
    detail: 'Выбранная роль определяет, какие свойства видео передаются модели.', actions: [],
  };
  if (['Движение', 'Движение и камера'].includes(role)) {
    state.actions = [...state.actions.filter(([action]) => action !== 'motion-template'),
      ['motion-template-source', 'Движение + камера'],
      ['motion-template-scene', 'Движение + камера · моя локация'],
      ['motion-template-only', 'Только движение']];
  }
  // Every in-scene swap sent with a motion role left the source performers in.
  if (role === 'Движение и камера' && mode === 'replace') {
    state.detail += ' Если герои должны остаться в исходной сцене с её участниками, выбери «Вся сцена»: все принятые замены сделаны так, а замены в сцене с этой ролью оставляли исходных персонажей.';
    state.actions.push(['source-scene', 'Вся сцена']);
  }
  if (['Движение', 'Движение и камера'].includes(role) && !references.some(ref => ref.kind === 'image' && ref.role === 'Персонаж целиком')) {
    state.detail += ' Для устойчивой фигуры и рук добавь фото героя в полный рост: портрет задаёт лицо, но не все пропорции тела.';
  }
  if (canRestore) state.actions.push(['motion-template-restore', 'Вернуть исходный текст']);
  const sourceDuration = Number(videos[0].sourceDuration);
  if (Number.isFinite(sourceDuration) && sourceDuration > Number(shotDuration) + 0.5) {
    const format = value => Number(value).toFixed(1).replace('.', ',');
    return {
      ...state, tone: 'warning',
      detail: `Исходник ${format(sourceDuration)} с, кадр ${format(shotDuration)} с: GPU получит только первые ${format(shotDuration)} с видео. Весь танец в один шот H3 не поместится. ${state.detail}`,
      actions: sourceDuration >= 16 && sourceDuration <= 180
        ? [...state.actions, ['long-scene', 'Разложить видео на шоты']] : state.actions,
    };
  }
  return state;
}

export function renderVideoWorkflow(element, state) {
  element.replaceChildren();
  element.hidden = !state;
  if (!state) return;
  element.dataset.tone = state.tone;
  const title = document.createElement('strong');
  title.textContent = state.title;
  const detail = document.createElement('p');
  detail.textContent = state.detail;
  element.append(title, detail);
  if (state.actions.length) {
    const actions = document.createElement('div');
    actions.className = 'video-workflow-actions';
    for (const [action, label] of state.actions) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.videoAction = action;
      button.textContent = label;
      actions.append(button);
    }
    element.append(actions);
  }
}
