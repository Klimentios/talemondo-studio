import { draftChecklist, newAudioProject, timelineRows } from './audio-studio-model.mjs?v=20261004-5';
import { MUSIC_PRESETS, VOICE_PRESETS, MIX_PRESETS } from './audio-studio-presets.mjs?v=20261004-2';
import { placeUploadedAudio, projectForWorkflow } from './audio-studio-workflow.mjs?v=20261004-5';
import { errorText, jobErrorText } from './api-error.mjs?v=20261008-1';
import { jobTitle } from './job-title.mjs?v=20261008-1';
import { featureAvailability } from './gpu-availability.mjs?v=20261008-audit-fixes-1';

const $ = (selector) => document.querySelector(selector);
let project = newAudioProject();
let assets = [];
let jobs = [];
let saveTimer;
let saving = Promise.resolve();
let apiReady = false;
let audioHealth = null;
let audioCapabilities = { audio_mix: false, tts: false, lip_sync: false,
  music_generation: false, music_controls: false, effects_generation: false };
let submitting = false;
let workflow = 'speech';

const musicConfig = document.createElement('div');
musicConfig.className = 'music-config';
musicConfig.innerHTML = `<label>Темп, BPM <span>пусто — модель выберет сама</span><input id="musicBpm" type="number" min="30" max="300" step="1" placeholder="Авто"></label>
  <label>Тональность <span>например, Am или C Major</span><input id="musicKeyscale" type="text" maxlength="20" placeholder="Авто"></label>
  <label>Размер <select id="musicTimesignature"><option value="">Авто</option><option value="2">2/4</option><option value="3">3/4</option><option value="4">4/4</option><option value="6">6/8</option></select></label>
  <label>Seed <span>−1 — случайный</span><input id="musicSeed" type="number" min="-1" max="2147483647" step="1"></label>`;
$('#musicLyrics').closest('label').after(musicConfig);


function setWorkflow(next) {
  workflow = next;
  document.body.dataset.workflow = next;
  document.querySelectorAll('[data-workflow]').forEach((button) => {
    if (button.matches('button')) {
      const active = button.dataset.workflow === next;
      button.toggleAttribute('aria-current', active);
    }
  });
  const stage = {
    speech: ['Голос для твоей сцены', 'Напиши реплики, выбери голос и проверь расположение на дорожке.'],
    music: ['Сочини свой звук', 'Опиши стиль и добавь собственный текст песни. Результат сохранится отдельно.'],
    mix: ['Собери финальный звук', 'Совмести речь, музыку, эффекты и исходную дорожку в новом файле.'],
    voice: ['Новый голос для видео', 'Выбери ролик, впиши реплики и прикрепи образец нового голоса. Исходный звук останется отдельным файлом.'],
    translate: ['Озвучка перевода', 'Впиши готовый перевод реплик и выбери язык. Новый голос заменит исходную звуковую дорожку.'],
  }[next];
  $('#stageTitle').textContent = stage[0];
  $('#stageDescription').textContent = stage[1];
  $('#audioUploadLabel').textContent = { speech: 'Добавить образец голоса', music: 'Добавить готовую музыку', mix: 'Добавить звуковой файл', voice: 'Добавить образец нового голоса', translate: 'Добавить образец голоса для перевода' }[next];
  const upload = $('.shared-audio-upload');
  if (next === 'speech' || next === 'voice' || next === 'translate') $('.script-panel .panel-heading').after(upload);
  else if (next === 'music' || next === 'mix') $('.sound-panel .panel-heading').after(upload);
  else $('#modelStatus').after(upload);
  $('#duration').parentElement.firstChild.textContent = next === 'music' ? 'Длительность музыки, сек' : 'Длина проекта, сек';
  $('#soundHeading').textContent = next === 'mix' ? 'Музыка и баланс дорожек' : 'Создать музыку';
  $('#soundDescription').textContent = next === 'mix' ? 'Выбери музыку, настрой громкость и добавь эффекты.' : 'Опиши звучание и при желании добавь текст песни.';
  $('#musicDetails').open = next === 'music' || Boolean(project.mix.music_prompt?.trim());
  $('.mode-picker').hidden = ['music', 'voice', 'translate'].includes(next);
  $('.script-panel .panel-heading p').textContent = next === 'voice' ? 'Впиши слова исходной речи и время начала каждой реплики. Для нового тембра выбери аудиообразец.' : next === 'translate' ? 'Впиши переведённые реплики, укажи язык и время начала. Перевод текста выполняется вручную.' : 'Каждая реплика получает своего героя, подачу и место на дорожке.';
  $('#videoSourcePanel').hidden = next === 'music' || (project.mode !== 'video' && !['voice', 'translate'].includes(next));
  $('#outputFormatRow').hidden = ['music', 'voice', 'translate'].includes(next) || project.mode === 'video';
  $('#lipSyncRow').hidden = ['music', 'voice', 'translate'].includes(next) || project.mode !== 'video';
  $('#lipTargetRow').hidden = ['music', 'voice', 'translate'].includes(next) || project.mode !== 'video' || !project.lip_sync;
  $('#musicStage').hidden = next !== 'music';
  renderTaskSummary();
  renderTimeline();
  renderAvailability();
}

function renderMusicStage() {
  const prompt = project.mix.music_prompt?.trim();
  const lyrics = project.mix.music_lyrics?.trim();
  const asset = assets.find((item) => item.asset_id === project.mix.music_asset_id);
  $('#musicStageTitle').textContent = asset ? asset.original_name || 'Выбранная музыка' :
    prompt ? (lyrics ? 'Песня с твоим текстом' : 'Инструментальная композиция') : 'Твоя композиция начнётся здесь';
  $('#musicStagePrompt').textContent = asset ? 'Готовая дорожка из медиатеки. Прослушай её перед сведением.' :
    prompt ? `${prompt}${lyrics ? ' · Текст песни добавлен' : ''}` :
      'Опиши стиль, добавь текст песни и укажи длительность.';
  const preview = $('#musicPreview');
  preview.hidden = !asset;
  const src = asset ? `/api/assets/${encodeURIComponent(asset.asset_id)}/file` : '';
  if (preview.getAttribute('src') !== src) {
    if (src) preview.setAttribute('src', src);
    else preview.removeAttribute('src');
    preview.load();
  }
}

function renderTaskSummary() {
  const task = projectForWorkflow(project, workflow);
  const speechCount = task.lines.filter((line) => line.text.trim()).length;
  const music = task.mix.music_prompt.trim() ? 'Создание музыки' :
    task.mix.music_asset_id ? 'Готовая музыка' : '';
  const parts = [
    `${Math.round(Number(task.duration) || 0)} сек`,
    task.mode === 'video' ? 'MP4 с видео' : task.output_format.toUpperCase(),
    ...(speechCount ? [`Реплики: ${speechCount}`] : []),
    ...(music ? [music] : []),
    ...(task.mix.effects_asset_id ? ['Эффект'] : []),
  ];
  $('#taskSummary').replaceChildren(...parts.map((value) => {
    const chip = document.createElement('span');
    chip.textContent = value;
    return chip;
  }));
}

async function request(path, options = {}) {
  const response = await fetch(path, { cache: 'no-store', ...options });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try { message = errorText(await response.json(), response.status); } catch { /* keep status */ }
    throw new Error(message);
  }
  return response.json();
}

function status(message, bad = false) {
  $('#saveStatus').textContent = message;
  $('#saveStatus').style.color = bad ? '#eda6a9' : '#bcb0bd';
}

function markChanged() {
  status('Есть несохранённые изменения');
  clearTimeout(saveTimer);
  if (apiReady) saveTimer = setTimeout(saveProject, 900);
  renderTimeline();
  renderMusicStage();
  renderTaskSummary();
  renderAvailability();
}

function option(value, label) {
  const item = document.createElement('option');
  item.value = value;
  item.textContent = label;
  return item;
}

function presetButton(preset) {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.preset = preset.id;
  button.textContent = preset.label;
  return button;
}

$('#musicPresets').append(...MUSIC_PRESETS.map(presetButton));
$('#mixPresets').append(...MIX_PRESETS.map(presetButton));

function fillAssetSelect(select, blank, kind = 'audio') {
  select.replaceChildren(option('', blank));
  assets.filter((asset) => asset.kind === kind).forEach((asset) =>
    select.append(option(asset.asset_id, asset.original_name || asset.asset_id)));
}

function populateSources() {
  const select = $('#sourceSelect');
  select.replaceChildren(option('', 'Выбери ролик'));
  jobs.filter((job) => job.state === 'completed').forEach((job) => {
    const label = `${jobTitle(job, 45)} · #${job.job_id.slice(0, 8)}`;
    select.append(option(`job:${job.job_id}`, label));
  });
  assets.filter((asset) => asset.kind === 'video').forEach((asset) =>
    select.append(option(`asset:${asset.asset_id}`, asset.original_name || asset.asset_id)));
  select.value = project.source.kind === 'none' ? '' : `${project.source.kind}:${project.source.id}`;
  fillAssetSelect($('#musicAsset'), 'Без музыки');
  fillAssetSelect($('#effectsAsset'), 'Без эффекта');
  $('#musicAsset').value = project.mix.music_asset_id;
  $('#effectsAsset').value = project.mix.effects_asset_id;
  renderSourcePreview();
  renderAudioLibrary();
}

function renderAudioLibrary() {
  const list = $('#audioLibrary');
  list.replaceChildren();
  const query = $('#audioLibrarySearch').value.trim().toLocaleLowerCase('ru');
  const matches = assets.filter((asset) => asset.kind === 'audio' &&
    (asset.original_name || '').toLocaleLowerCase('ru').includes(query));
  if (!matches.length) {
    const empty = document.createElement('p');
    empty.className = 'field-help';
    empty.textContent = assets.some((asset) => asset.kind === 'audio') ?
      'По этому запросу аудио не найдено.' : 'Аудиофайлов пока нет. Добавь запись слева.';
    list.append(empty);
    return;
  }
  matches.forEach((asset) => {
    const row = document.createElement('div');
    row.className = 'library-audio';
    const name = document.createElement('strong');
    name.textContent = asset.original_name || asset.asset_id;
    const player = document.createElement('audio');
    player.controls = true;
    player.preload = 'none';
    player.src = `/api/assets/${encodeURIComponent(asset.asset_id)}/file`;
    const use = document.createElement('button');
    use.type = 'button';
    use.textContent = 'В музыку';
    use.addEventListener('click', () => {
      project.mix.music_asset_id = asset.asset_id;
      project.mix.music_prompt = '';
      project.mix.music_lyrics = '';
      render();
      markChanged();
    });
    const effects = document.createElement('button');
    effects.type = 'button';
    effects.textContent = 'В эффекты';
    effects.addEventListener('click', () => {
      project.mix.effects_asset_id = asset.asset_id;
      render();
      markChanged();
    });
    const actions = document.createElement('div');
    actions.className = 'library-actions';
    actions.append(use, effects);
    row.append(name, player, actions);
    list.append(row);
  });
}

function renderSourcePreview() {
  const box = $('#sourcePreview');
  box.replaceChildren();
  const { kind, id } = project.source;
  if (kind === 'none' || !id) {
    const message = document.createElement('span');
    message.textContent = 'После выбора ролик появится здесь.';
    box.append(message);
    return;
  }
  const video = document.createElement('video');
  video.controls = true;
  video.playsInline = true;
  video.preload = 'metadata';
  video.src = kind === 'job' ? `/api/jobs/${encodeURIComponent(id)}/video` : `/api/assets/${encodeURIComponent(id)}/file`;
  video.addEventListener('loadedmetadata', () => {
    if (Number.isFinite(video.duration) && video.duration > 0 && video.duration <= 3600) {
      project.duration = Math.ceil(video.duration);
      $('#duration').value = project.duration;
      markChanged();
    }
  }, { once: true });
  box.append(video);
}

function labeledInput(labelText, field, value, type = 'text', attrs = {}) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const input = document.createElement(type === 'textarea' ? 'textarea' : 'input');
  if (type !== 'textarea') input.type = type;
  input.dataset.field = field;
  input.value = value ?? '';
  for (const [key, entry] of Object.entries(attrs)) input.setAttribute(key, entry);
  label.append(input);
  return label;
}

function labeledSelect(labelText, field, choices, current) {
  const label = document.createElement('label');
  label.textContent = labelText;
  const select = document.createElement('select');
  select.dataset.field = field;
  choices.forEach(([value, caption]) => select.append(option(value, caption)));
  select.value = current || '';
  label.append(select);
  return label;
}

function renderLines() {
  const container = $('#lines');
  container.replaceChildren();
  if (!project.lines.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-lines';
    empty.textContent = 'Пока нет реплик. Добавь первую, чтобы собрать голосовую дорожку.';
    container.append(empty);
  }
  project.lines.forEach((line, index) => {
    const card = document.createElement('article');
    card.className = 'line-card';
    card.dataset.id = line.id;
    const top = document.createElement('div');
    top.className = 'line-top';
    const title = document.createElement('strong');
    title.textContent = `РЕПЛИКА ${String(index + 1).padStart(2, '0')}`;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.dataset.remove = line.id;
    remove.setAttribute('aria-label', `Удалить реплику ${index + 1}`);
    remove.textContent = '×';
    top.append(title, remove);
    const grid = document.createElement('div');
    grid.className = 'line-grid';
    grid.append(labeledInput('Кто говорит', 'speaker', line.speaker, 'text', { maxlength: '80', placeholder: 'Имя героя' }),
      labeledInput('Начало, сек', 'start', line.start, 'number', { min: '0', max: '3600', step: '0.1' }));
    const text = labeledInput('Текст реплики', 'text', line.text, 'textarea', { maxlength: '1200', placeholder: 'Что произносит герой…' });
    text.className = 'full';
    grid.append(text);
    const options = document.createElement('div');
    options.className = 'line-options';
    options.append(labeledSelect('Источник голоса', 'voice_mode', [['preset', 'Базовый голос и подача'], ['reference', 'По аудиообразцу']], line.voice_mode),
      labeledInput('Манера и эмоция', 'direction', line.direction, 'text', { maxlength: '160', placeholder: 'Тихо, напряжённо, с паузой…' }),
      labeledInput('Описание голоса', 'voice_description', line.voice_description, 'text', { maxlength: '200', placeholder: 'Тембр, возраст, акцент…' }),
      labeledSelect('Язык реплики', 'language', [['ru', 'Русский'], ['en', 'English'], ['other', 'Другой язык по тексту']], line.language));
    if (line.voice_mode === 'reference') {
      const reference = labeledSelect('Голосовой образец', 'voice_asset_id',
        [['', 'Выбери аудиообразец'], ...assets.filter((asset) => asset.kind === 'audio').map((asset) => [asset.asset_id, asset.original_name])],
        line.voice_asset_id);
      options.append(reference);
    }
    const delivery = document.createElement('div');
    delivery.className = 'preset-group voice-presets';
    const caption = document.createElement('span');
    caption.textContent = 'Манера речи';
    delivery.append(caption, ...VOICE_PRESETS.map(presetButton));
    card.append(top, grid, options, delivery);
    container.append(card);
  });
}

function renderTimeline() {
  const duration = Math.max(1, Number(project.duration) || 15);
  const format = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.round(seconds % 60)).padStart(2, '0')}`;
  $('#rulerMid').textContent = format(duration / 2);
  $('#rulerEnd').textContent = format(duration);
  const tracks = $('#tracks');
  tracks.replaceChildren();
  const rows = timelineRows(projectForWorkflow(project, workflow));
  if (!rows.length) {
    const empty = document.createElement('p');
    empty.className = 'field-help';
    empty.textContent = 'Дорожки появятся после выбора видео, реплики или музыки.';
    tracks.append(empty);
  }
  rows.forEach((row) => {
    const track = document.createElement('div');
    track.className = 'track';
    const name = document.createElement('span');
    name.className = 'track-name';
    name.textContent = row.name;
    const lane = document.createElement('div');
    lane.className = 'track-lane';
    const block = document.createElement('span');
    block.className = `track-block ${row.type}`;
    block.textContent = row.name;
    block.style.left = `${Math.min(100, row.start / duration * 100)}%`;
    block.style.width = `${Math.max(0, Math.min(row.length, duration - row.start) / duration * 100)}%`;
    lane.append(block);
    track.append(name, lane);
    tracks.append(track);
  });
}

function renderAvailability() {
  const task = projectForWorkflow(project, workflow);
  const issues = draftChecklist(task);
  const missingContent = 'добавить речь, музыку, эффект или исходный звук';
  const contentIndex = issues.indexOf(missingContent);
  if (contentIndex >= 0) issues[contentIndex] = {
    speech: 'напиши реплику', music: 'опиши стиль или выбери готовую музыку',
    mix: missingContent, voice: 'впиши реплику для нового голоса', translate: 'впиши переведённую реплику',
  }[workflow] || missingContent;
  if (workflow === 'voice' && task.lines.some((line) => line.voice_mode !== 'reference' || !line.voice_asset_id)) issues.push('выбери голос по аудиообразцу в каждой реплике');
  const connection = featureAvailability(audioHealth, true);
  if (connection.state !== 'ready') issues.push(connection.message.replace(/\.$/, ''));
  else {
    if (!audioCapabilities.audio_mix) issues.push('сведение недоступно на сервере');
    if (task.lines.some((line) => line.text.trim()) && !audioCapabilities.tts) issues.push('синтез речи ещё не установлен');
    if (task.lip_sync && !audioCapabilities.lip_sync) issues.push('синхронизация губ ещё не установлена');
    if (task.mix.music_prompt.trim() && !audioCapabilities.music_generation) issues.push('генерация музыки ещё не установлена');
  }
  const customMusic = task.mix.music_bpm != null && task.mix.music_bpm !== '' ||
    Boolean(task.mix.music_keyscale || task.mix.music_timesignature) || (task.mix.music_seed ?? -1) !== -1;
  if (connection.state === 'ready' && task.mix.music_prompt.trim() && customMusic && !audioCapabilities.music_controls) issues.push('расширенные настройки музыки ещё не установлены на GPU');
  $('#musicStatus').textContent = audioCapabilities.music_generation && audioCapabilities.music_controls ?
    'ACE-Step готова. Стиль, текст и выбранные параметры будут переданы модели; результат сохранится отдельным WAV.' :
    audioCapabilities.music_generation ? 'ACE-Step готова; расширенные параметры станут доступны после обновления GPU.' :
    audioCapabilities.audio_mix ? 'Сервер доступен; ACE-Step пока не активирована. Описание сохранится в проекте.' :
      'Звуковой сервер недоступен. Описание сохранится в проекте.';
  if (connection.state === 'ready' && task.mix.effects_prompt.trim() && !audioCapabilities.effects_generation) issues.push('генерация эффектов ещё не установлена');
  $('#availability').textContent = issues.length ? `Перед сборкой: ${issues.join(', ')}.` :
    'Готово к постановке в очередь. Исходные файлы сохранятся отдельно.';
  $('#renderAudio').disabled = !apiReady || submitting || issues.length > 0;
  $('#renderAudio').textContent = workflow === 'music' && task.mix.music_asset_id ? 'Собрать дорожку' :
    { speech: 'Создать речь', music: 'Создать музыку', mix: 'Свести дорожки', voice: 'Сменить голос', translate: 'Озвучить перевод' }[workflow] || 'Собрать результат';
  $('#modelStatus').replaceChildren(...[
    ['Сведение', audioCapabilities.audio_mix],
    ['Речь', audioCapabilities.tts],
    ['Музыка', audioCapabilities.music_generation],
  ].map(([label, ready]) => {
    const feature = featureAvailability(audioHealth, ready);
    const chip = document.createElement('span');
    chip.className = feature.state === 'ready' ? 'ready' : 'unavailable';
    chip.textContent = `${label}: ${feature.label}`;
    chip.title = feature.message;
    return chip;
  }));
}

function showAudioJobs(items) {
  const box = $('#audioJobs');
  box.replaceChildren();
  const labels = { queued_local: 'В очереди Mac', uploading: 'Отправляем файлы',
    needs_reconciliation: 'Нужна сверка', queued_gpu: 'В очереди GPU', running: 'Собираем',
    downloading: 'Скачиваем результат', completed: 'Готово', failed: 'Ошибка', cancelled: 'Отменено' };
  items.slice(0, 20).forEach((job) => {
    const row = document.createElement('div');
    row.className = `audio-job${job.state === 'failed' ? ' error' : ''}`;
    const description = document.createElement('div');
    const state = document.createElement('strong');
    state.textContent = labels[job.state] || job.state;
    const detail = document.createElement('small');
    detail.textContent = ` · ${job.audio_id.slice(0, 8)}${job.error ? ` · ${jobErrorText(job.error)}` : ''}`;
    if (job.error) detail.title = job.error;
    description.append(state, detail);
    row.append(description);
    if (['queued_local', 'uploading', 'needs_reconciliation', 'queued_gpu', 'running', 'downloading'].includes(job.state)) {
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.textContent = 'Отменить';
      cancel.addEventListener('click', async () => {
        cancel.disabled = true;
        try {
          const response = await fetch(`/api/audio/jobs/${encodeURIComponent(job.audio_id)}/cancel`, { method: 'POST' });
          if (!response.ok) throw new Error(errorText(await response.json().catch(() => ({})), response.status));
          state.textContent = 'Отменено';
          cancel.remove();
        } catch (error) { cancel.disabled = false; state.textContent = `${labels[job.state] || job.state} · ${error.message}`; }
      });
      row.append(cancel);
    }
    if (job.state === 'completed') {
      const media = document.createElement(job.output_path?.toLowerCase().endsWith('.mp4') ? 'video' : 'audio');
      media.controls = true;
      media.preload = 'none';
      media.src = `/api/audio/jobs/${encodeURIComponent(job.audio_id)}/file`;
      media.className = 'result-preview';
      row.append(media);
      const link = document.createElement('a');
      link.href = `/api/audio/jobs/${encodeURIComponent(job.audio_id)}/file`;
      link.textContent = 'Скачать';
      row.append(link);
      if (job.original_audio_path) {
        const original = document.createElement('a');
        original.href = `/api/audio/jobs/${encodeURIComponent(job.audio_id)}/original-audio`;
        original.textContent = 'Исходный звук';
        row.append(original);
      }
      if (job.generated_music_path) {
        const music = document.createElement('a');
        music.href = `/api/audio/jobs/${encodeURIComponent(job.audio_id)}/generated-music`;
        music.textContent = 'Музыка WAV';
        row.append(music);
      }
    }
    box.append(row);
  });
}

async function refreshAudioStatus() {
  try {
    const [health, items] = await Promise.all([request('/api/health'), request('/api/audio/jobs')]);
    audioHealth = health;
    audioCapabilities = health.gpu_reachable ? await request('/api/audio/capabilities') :
      { audio_mix: false, tts: false, lip_sync: false, music_generation: false, music_controls: false, effects_generation: false };
    showAudioJobs(items);
  } catch {
    audioHealth = null;
    audioCapabilities = { audio_mix: false, tts: false, lip_sync: false,
      music_generation: false, music_controls: false, effects_generation: false };
  }
  renderAvailability();
}

function render() {
  document.body.dataset.mode = project.mode;
  $('#projectTitle').value = project.title;
  document.querySelectorAll('.mode-picker button').forEach((button) => button.classList.toggle('selected', button.dataset.mode === project.mode));
  $('#videoSourcePanel').hidden = workflow === 'music' || (project.mode !== 'video' && !['voice', 'translate'].includes(workflow));
  $('#sourceLevel').disabled = project.mode !== 'video';
  $('#lipSyncRow').hidden = ['music', 'voice', 'translate'].includes(workflow) || project.mode !== 'video';
  $('#lipTargetRow').hidden = ['music', 'voice', 'translate'].includes(workflow) || project.mode !== 'video' || !project.lip_sync;
  $('#outputFormatRow').hidden = ['music', 'voice', 'translate'].includes(workflow) || project.mode === 'video';
  $('#lipSync').checked = project.lip_sync;
  $('#lipTarget').value = project.lip_sync_target;
  $('#duration').value = project.duration;
  $('#outputFormat').value = project.output_format;
  for (const [key, id] of [['source_level','sourceLevel'], ['speech_level','speechLevel'], ['music_level','musicLevel'], ['effects_level','effectsLevel']]) {
    $(`#${id}`).value = project.mix[key];
    $(`#${id}Value`).textContent = `${Math.round(project.mix[key] * 100)}%`;
  }
  $('#ducking').checked = project.mix.ducking;
  $('#musicPrompt').value = project.mix.music_prompt;
  $('#musicLyrics').value = project.mix.music_lyrics || '';
  $('#musicBpm').value = project.mix.music_bpm ?? '';
  $('#musicKeyscale').value = project.mix.music_keyscale || '';
  $('#musicTimesignature').value = String(project.mix.music_timesignature || '');
  $('#musicSeed').value = project.mix.music_seed ?? -1;
  $('#effectsPrompt').value = project.mix.effects_prompt;
  populateSources();
  renderLines();
  renderTimeline();
  renderMusicStage();
  renderTaskSummary();
  renderAvailability();
}

$('#videoSourcePanel').classList.add('stage-source');
$('.stage-heading').after($('#videoSourcePanel'));
$('#stageJobs').append($('#audioJobs'));
$('#audioLibrarySearch').addEventListener('input', renderAudioLibrary);
document.querySelectorAll('.workflow-tabs button').forEach((button) =>
  button.addEventListener('click', () => setWorkflow(button.dataset.workflow)));
setWorkflow('speech');

function saveProject() {
  if (!apiReady) return;
  clearTimeout(saveTimer);
  const snapshot = JSON.parse(JSON.stringify(project));
  saving = saving.catch(() => {}).then(async () => {
    try {
      await request('/api/audio/draft', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: snapshot }) });
      status('Сохранено на Mac');
      return true;
    } catch (error) { status(`Не сохранено: ${error.message}`, true); return false; }
  });
  return saving;
}

async function refreshLibrary() {
  [assets, jobs] = await Promise.all([request('/api/assets'), request('/api/jobs')]);
  populateSources();
  renderLines();
}

async function upload(file, role) {
  if (!file) return;
  const data = new FormData();
  data.append('file', file);
  status('Добавляем файл в медиатеку…');
  try {
    const response = await request(`/api/assets?role=${encodeURIComponent(role)}`, { method: 'POST', body: data });
    await refreshLibrary();
    let uploadMessage = 'Файл добавлен в медиатеку';
    if (response.kind === 'video') {
      project.source = { kind: 'asset', id: response.asset_id };
      $('#sourceSelect').value = `asset:${response.asset_id}`;
      renderSourcePreview();
      uploadMessage = 'Видео добавлено в проект';
    } else if (response.kind === 'audio') {
      const destination = placeUploadedAudio(project, workflow, response.asset_id);
      render();
      uploadMessage = { music: 'Музыка добавлена в проект', voice: 'Образец привязан к реплике',
        library: 'Файл в медиатеке — выбери его в нужной дорожке' }[destination];
    }
    markChanged();
    status(uploadMessage);
  } catch (error) { status(`Файл не добавлен: ${error.message}`, true); }
}

document.querySelectorAll('.mode-picker button').forEach((button) => button.addEventListener('click', () => {
  project.mode = button.dataset.mode;
  if (project.mode === 'audio') project.lip_sync = false;
  render(); markChanged();
}));
$('#sourceSelect').addEventListener('change', (event) => {
  const [kind, id] = event.target.value ? event.target.value.split(':', 2) : ['none', ''];
  project.source = { kind, id };
  renderSourcePreview(); markChanged();
});
$('#videoUpload').addEventListener('change', (event) => upload(event.target.files[0], 'Исходное видео'));
$('#audioUpload').addEventListener('change', (event) => upload(event.target.files[0], 'Аудио для звукового проекта'));
$('#addLine').addEventListener('click', () => {
  if (project.lines.length >= 40) { status('Не больше 40 реплик в одном проекте', true); return; }
  project.lines.push({ id: crypto.randomUUID(), speaker: '', text: '', start: 0, voice_mode: 'preset',
    voice_asset_id: '', voice_description: '', direction: '', language: 'ru' });
  renderLines(); markChanged();
});
$('#lines').addEventListener('click', (event) => {
  const presetId = event.target.closest('.voice-presets [data-preset]')?.dataset.preset;
  if (presetId) {
    const line = project.lines.find((item) => item.id === event.target.closest('.line-card')?.dataset.id);
    const preset = VOICE_PRESETS.find((item) => item.id === presetId);
    if (line && preset) { line.direction = preset.direction; renderLines(); markChanged(); }
    return;
  }
  const id = event.target.closest('[data-remove]')?.dataset.remove;
  if (!id) return;
  project.lines = project.lines.filter((line) => line.id !== id);
  renderLines(); markChanged();
});
$('#musicPresets').addEventListener('click', (event) => {
  const preset = MUSIC_PRESETS.find((item) => item.id === event.target.closest('[data-preset]')?.dataset.preset);
  if (!preset) return;
  project.mix.music_prompt = preset.prompt;
  project.mix.music_asset_id = '';
  $('#musicPrompt').value = preset.prompt;
  $('#musicAsset').value = '';
  markChanged();
});
$('#mixPresets').addEventListener('click', (event) => {
  const preset = MIX_PRESETS.find((item) => item.id === event.target.closest('[data-preset]')?.dataset.preset);
  if (!preset) return;
  for (const key of ['source_level', 'speech_level', 'music_level', 'effects_level', 'ducking']) {
    project.mix[key] = preset[key];
  }
  render(); markChanged();
});
function onLineChange(event) {
  const control = event.target.closest('[data-field]');
  if (!control) return;
  const line = project.lines.find((item) => item.id === control.closest('.line-card')?.dataset.id);
  if (!line) return;
  line[control.dataset.field] = control.dataset.field === 'start' ? Number(control.value) || 0 : control.value;
  if (control.dataset.field === 'voice_mode') renderLines();
  markChanged();
}
$('#lines').addEventListener('input', onLineChange);
$('#lines').addEventListener('change', onLineChange);
$('#projectTitle').addEventListener('input', (event) => { project.title = event.target.value; markChanged(); });
$('#duration').addEventListener('input', (event) => { project.duration = Number(event.target.value) || 15; markChanged(); });
$('#lipSync').addEventListener('change', (event) => { project.lip_sync = event.target.checked; $('#lipTargetRow').hidden = !project.lip_sync; markChanged(); });
$('#lipTarget').addEventListener('input', (event) => { project.lip_sync_target = event.target.value; markChanged(); });
$('#outputFormat').addEventListener('change', (event) => { project.output_format = event.target.value; markChanged(); });
for (const [key, id] of [['source_level','sourceLevel'], ['speech_level','speechLevel'], ['music_level','musicLevel'], ['effects_level','effectsLevel']]) {
  $(`#${id}`).addEventListener('input', (event) => { project.mix[key] = Number(event.target.value); $(`#${id}Value`).textContent = `${Math.round(project.mix[key] * 100)}%`; markChanged(); });
}
for (const [key, id] of [['music_asset_id','musicAsset'], ['effects_asset_id','effectsAsset'], ['music_prompt','musicPrompt'], ['music_lyrics','musicLyrics'], ['effects_prompt','effectsPrompt']]) {
  $(`#${id}`).addEventListener('input', (event) => {
    project.mix[key] = event.target.value;
    if (key === 'music_asset_id' && event.target.value) {
      project.mix.music_prompt = '';
      $('#musicPrompt').value = '';
      project.mix.music_lyrics = '';
      $('#musicLyrics').value = '';
    }
    if ((key === 'music_prompt' || key === 'music_lyrics') && event.target.value.trim()) {
      project.mix.music_asset_id = '';
      $('#musicAsset').value = '';
    }
    markChanged();
  });
}
for (const [key, id] of [['music_bpm','musicBpm'], ['music_keyscale','musicKeyscale'], ['music_timesignature','musicTimesignature'], ['music_seed','musicSeed']]) {
  $(`#${id}`).addEventListener('input', (event) => {
    project.mix[key] = key === 'music_bpm' ? (event.target.value === '' ? null : Number(event.target.value)) :
      key === 'music_seed' ? (event.target.value === '' ? -1 : Number(event.target.value)) : event.target.value;
    markChanged();
  });
}
$('#ducking').addEventListener('change', (event) => { project.mix.ducking = event.target.checked; markChanged(); });
$('#saveProject').addEventListener('click', saveProject);
$('#renderAudio').addEventListener('click', async () => {
  if (submitting || $('#renderAudio').disabled) return;
  submitting = true;
  renderAvailability();
  try {
    if (await saveProject() !== true) throw new Error('сначала сохрани проект');
    const receipt = await request('/api/audio/jobs', { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project: projectForWorkflow(project, workflow) }) });
    status(`Задача ${receipt.audio_id.slice(0, 8)} поставлена в очередь`);
    await refreshAudioStatus();
  } catch (error) { status(`Не удалось поставить задачу: ${error.message}`, true); }
  finally { submitting = false; renderAvailability(); }
});
$('#exportPlan').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(project, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'studio-audio-project.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function bootstrap() {
  render();
  try {
    const [draft, library, jobList] = await Promise.all([request('/api/audio/draft'), request('/api/assets'), request('/api/jobs')]);
    apiReady = true;
    assets = library;
    jobs = jobList;
    if (draft.project?.version === 1) project = draft.project;
    const selectedSource = new URLSearchParams(location.search).get('source');
    if (selectedSource?.startsWith('job:')) {
      const selectedJob = jobs.find((job) => job.job_id === selectedSource.slice(4) && job.state === 'completed');
      if (selectedJob) {
        project.mode = 'video';
        project.source = { kind: 'job', id: selectedJob.job_id };
      }
    }
    render();
    await refreshAudioStatus();
    $('#connectionStatus').textContent = 'Проект хранится на Mac';
    status(draft.project ? 'Сохранённый проект открыт' : 'Новый проект готов к заполнению');
    if (selectedSource && project.source.kind === 'job') markChanged();
  } catch {
    $('#connectionStatus').textContent = 'Локальная Studio недоступна';
    status('Подключи локальную Studio, чтобы сохранять проект', true);
  }
}
bootstrap();
setInterval(refreshAudioStatus, 15000);
