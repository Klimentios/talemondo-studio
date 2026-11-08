// Photos for the Studio agent (agent-dock.mjs, agent-chat.mjs): 📎, paste (⌘V) or drop. Each photo goes to the
// Studio library first (POST /api/assets) and the message carries its asset_id; the agent looks at it
// (local_studio/agent_chat.py: Codex gets a photo itself, both get view_image; a video is read with asset_frames) — a look
// to match, a hero, a place, a source video to swap a performer in or to take the motion from, a voice.
export const MAX_PHOTOS = 6;

const node = (tag, props = {}, ...children) => {
  const element = document.createElement(tag);
  // aria-* are attributes, not properties.
  for (const [key, value] of Object.entries(props)) if (key.includes('-')) element.setAttribute(key, value); else element[key] = value;
  element.append(...children.filter((child) => child !== null && child !== undefined));
  return element;
};
const isMedia = (file) => /^(image|video|audio)\//.test(file.type);
// A sound has no picture: a note with its name; a photo or a video shows the library thumbnail.
const preview = (file) => file.kind === 'audio' ? node('span', { title: file.name }, '♪')
  : node('img', { src: thumb(file.asset_id), alt: file.name, loading: 'lazy' });
const thumb = (assetId) => `/api/assets/${encodeURIComponent(assetId)}/thumbnail`;

async function upload(file) {
  const body = new FormData();
  body.append('file', file, file.name || 'photo.png');
  const response = await fetch('/api/assets?role=%D0%A0%D0%B5%D1%84%D0%B5%D1%80%D0%B5%D0%BD%D1%81', { method: 'POST', body });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : `Фото не загружено: HTTP ${response.status}`);
  if (!['image', 'video', 'audio'].includes(payload.kind)) throw new Error(`${file.name || 'Файл'}: агенту можно прикрепить фото, видео или звук`);
  return payload;
}

/** The 📎 button and the tray of photos waiting to be sent. `input` gets paste, `dropZone` gets drag and drop. */
export function installAttachments({ input, dropZone, say = () => {}, className = 'ad' }) {
  const photos = [];  // { asset_id, name } | { pending: true, name }
  const tray = node('div', { className: `${className}-attachments`, hidden: true, 'aria-label': 'Фото к сообщению' });
  const picker = node('input', { type: 'file', accept: 'image/*,video/*,audio/*', multiple: true, hidden: true });
  const button = node('button', { type: 'button', className: 'k-btn k-btn--sm k-btn--ghost', title: 'Прикрепить фото, видео или звук: стиль, герой, место, исходное видео для замены героя или движения (можно вставить ⌘V или перетащить)',
    'aria-label': 'Прикрепить файл', onclick: () => picker.click() }, '📎');

  function render() {
    tray.hidden = !photos.length;
    tray.replaceChildren(...photos.map((photo, at) => node('figure', { className: photo.pending ? 'is-pending' : '', title: photo.name },
      photo.pending ? node('span', {}, '…') : preview(photo),
      node('button', { type: 'button', 'aria-label': `Убрать ${photo.name}`, disabled: Boolean(photo.pending),
        onclick: () => { photos.splice(at, 1); render(); } }, '×'))));
  }
  async function add(files) {
    const images = [...files].filter(isMedia);
    if (!images.length) { if (files.length) say('Агенту можно прикрепить фото, видео или звук'); return; }
    for (const file of images) {
      if (photos.length >= MAX_PHOTOS) { say(`Не больше ${MAX_PHOTOS} файлов в одном сообщении`); break; }
      const slot = { pending: true, name: file.name || 'фото' };
      photos.push(slot); render();
      try { say(`Загружается: ${slot.name}…`); const asset = await upload(file); Object.assign(slot, { pending: false, asset_id: asset.asset_id, kind: asset.kind }); say('Файл загружен в библиотеку Studio'); }
      catch (error) { photos.splice(photos.indexOf(slot), 1); say(error.message); }
      render();
    }
  }
  picker.addEventListener('change', () => { void add(picker.files); picker.value = ''; });
  input.addEventListener('paste', (event) => {
    const files = [...(event.clipboardData?.files || [])];
    if (files.some(isMedia)) { event.preventDefault(); void add(files); }
  });
  dropZone.addEventListener('dragover', (event) => {
    if ([...(event.dataTransfer?.items || [])].some((item) => item.kind === 'file')) { event.preventDefault(); dropZone.classList.add('is-dropping'); }
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('is-dropping'));
  dropZone.addEventListener('drop', (event) => {
    dropZone.classList.remove('is-dropping');
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault(); void add(event.dataTransfer.files);
  });
  return {
    button, tray, picker,
    busy: () => photos.some((photo) => photo.pending),
    /** Ready photos for the message ({asset_id, name}); the tray empties until restore(). */
    take() { const ready = photos.filter((photo) => !photo.pending).map(({ asset_id, name, kind }) => ({ asset_id, name, kind })); photos.length = 0; render(); return ready; },
    restore(list) { photos.push(...list); render(); },
  };
}

/** The photos a sent message carried, under its text. */
export function attachmentsView(list, className = 'ad') {
  if (!list?.length) return null;
  return node('div', { className: `${className}-sent-photos` }, ...list.map((photo) =>
    node('a', { href: `/api/assets/${encodeURIComponent(photo.asset_id)}/file`, target: '_blank', rel: 'noopener', title: photo.name },
      preview(photo))));
}
