// Source-backed methods are proposals, not render-verified film emulations.
export function installLookEditor(container, { profile, references, say }) {
  const section = document.createElement('fieldset');
  const legend = document.createElement('legend'); legend.textContent = 'Визуальный профиль кадра и видео'; section.append(legend);
  let selected = profile ? structuredClone(profile) : null;
  const select = document.createElement('select'); select.className = 'k-field';
  select.add(new Option('Без отдельного профиля', ''));
  const entries = new Map();
  if (selected) { select.add(new Option(selected.label, selected.id)); select.value = selected.id; }
  const note = document.createElement('p');
  note.textContent = 'Профиль задаёт метод постановки. Подтверждённого сходства с фильмом без просмотренных образцов и приёмки рендера нет.';
  const field = (label, value) => {
    const wrapper = document.createElement('label'); wrapper.textContent = label;
    const input = document.createElement('textarea'); input.className = 'k-field'; input.rows = 3; input.maxLength = 1500;
    input.value = value || ''; wrapper.append(input); section.append(wrapper); return input;
  };
  section.append(select, note);
  const frame = field('Постановка опорного изображения', selected?.frame_direction);
  const video = field('Развитие визуального языка в видео', selected?.video_direction);
  const refLabel = document.createElement('label'); refLabel.textContent = 'Назначенные референсы стиля';
  const refs = document.createElement('select'); refs.multiple = true; refs.className = 'k-field';
  for (const ref of references.filter(item => item.kind === 'image' && item.role === 'Стиль'))
    refs.add(new Option(ref.name || ref.id, ref.id, false, selected?.reference_asset_ids.includes(ref.id)));
  for (const id of selected?.reference_asset_ids || []) if (![...refs.options].some(option => option.value === id))
    refs.add(new Option(`${id} · не назначен как «Стиль» в сцене`, id, false, true));
  const sources = document.createElement('div');
  refLabel.append(refs); section.append(refLabel, sources); container.append(section);
  const show = () => {
    frame.value = selected?.frame_direction || ''; video.value = selected?.video_direction || '';
    frame.disabled = video.disabled = refs.disabled = !selected;
    for (const option of refs.options) option.selected = Boolean(selected?.reference_asset_ids.includes(option.value));
    sources.replaceChildren();
    for (const url of selected?.sources || []) {
      const line = document.createElement('p');
      if (/^https?:\/\//.test(url)) { const link = document.createElement('a'); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = url; line.append(link); }
      else line.textContent = url;
      sources.append(line);
    }
  };
  show();
  select.onchange = () => { selected = select.value ? structuredClone(entries.get(select.value)?.visual_profile || profile) : null; show(); };
  fetch('/api/visual-profiles').then(async response => {
    if (!response.ok) throw new Error('Каталог визуальных профилей недоступен');
    for (const entry of await response.json()) {
      const p = entry.visual_profile; entries.set(p.id, entry);
      if (![...select.options].some(option => option.value === p.id)) select.add(new Option(`${p.label} · метод, не проверен рендером`, p.id));
    }
  }).catch(error => say(error.message));
  return { read() {
    if (!selected) return null;
    const next = { ...selected, frame_direction: frame.value, video_direction: video.value,
      reference_asset_ids: [...refs.selectedOptions].map(option => option.value) };
    if (profile?.id === next.id && JSON.stringify(next) !== JSON.stringify(profile)) next.revision = profile.revision + 1;
    return next;
  } };
}
