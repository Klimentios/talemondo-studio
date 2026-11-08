// Guided access to the existing, explicitly experimental H3 replacement path.
// The panel never submits a job: the user reviews the generated prompt and starts it in Create.
// Swap parts stay ≤10 s like local_studio/video_swap.MAX_PART_S: a 15 s part samples about 2.2× longer than 10 s.
const SWAP_PART_SECONDS = 10;
export function installReplacementWorkspace({ panel, trigger, getState, openLibrary, pair, applyTemplate, planLongVideo }) {
  const $ = selector => panel.querySelector(selector);
  const render = () => {
    const { references, duration } = getState();
    const videos = references.filter(ref => ref.kind === 'video' && ref.role !== 'Продолжение');
    const heroes = [...new Set(references.filter(ref => ref.kind === 'image' &&
      ['Лицо героя', 'Персонаж целиком'].includes(ref.role) && ref.owner?.trim()).map(ref => ref.owner.trim()))];
    const video = videos.length === 1 ? videos[0] : null;
    const sourceDuration = Number(video?.sourceDuration) || 0;
    $('#replacementSource').textContent = video
      ? `${video.name} · ${sourceDuration ? `${sourceDuration.toFixed(1)} с` : 'длительность уточняется'}`
      : 'Добавь исходное видео из библиотеки с ролью «Исходное видео» — так сделаны все принятые замены.';
    $('#replacementHeroes').textContent = heroes.length
      ? heroes.join(', ') : 'Добавь каждому герою одно фото «Персонаж целиком» (лицо и полный костюм); укажи имя владельца.';
    $('#replacementPairs').textContent = video?.performers?.pairs?.length
      ? video.performers.pairs.map(item => `${item.performer} → ${item.owner}`).join(' · ')
      : 'Свяжи каждого героя с конкретным исполнителем исходного видео.';
    $('#replacementCoverage').textContent = sourceDuration > SWAP_PART_SECONDS
      ? `Исходник ${sourceDuration.toFixed(1)} с: дели на части до ${SWAP_PART_SECONDS} с по склейкам (часть 15 с считается в 2,2 раза дольше). Разложи видео на шоты; затем проверь и запусти каждый шот в «Сюжете». Автоматическая сборка полного MP4 пока недоступна.`
      : video ? `Текущий кадр: ${duration} с. Сверь его с длительностью исходника перед отправкой.` : 'Полный хронометраж появится после выбора видео.';
    $('#replacementPair').disabled = !video || !heroes.length;
    $('#replacementTemplate').disabled = !video || !heroes.length || (heroes.length > 1 && video.performers?.pairs?.length !== heroes.length);
    $('#replacementPlan').hidden = !(sourceDuration > SWAP_PART_SECONDS && sourceDuration <= 180);
  };
  trigger.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    trigger.setAttribute('aria-expanded', String(!panel.hidden));
    if (!panel.hidden) { render(); panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); }
  });
  $('#replacementAdd').addEventListener('click', openLibrary);
  $('#replacementPair').addEventListener('click', pair);
  $('#replacementTemplate').addEventListener('click', applyTemplate);
  $('#replacementPlan').addEventListener('click', planLongVideo);
  return { render: () => { if (!panel.hidden) render(); } };
}
