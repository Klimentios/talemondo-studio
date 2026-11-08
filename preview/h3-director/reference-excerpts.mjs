// Originals stay in the project; submitted assets are immutable selected excerpts.
export function excerptWindow(ref, duration) {
  const start = Number(ref.excerptStart ?? 0);
  const length = Number(ref.excerptDuration ?? duration);
  if (!Number.isFinite(start) || start < 0 || !Number.isFinite(length) || length <= 0 || length > 15)
    throw new Error('Выбери участок длиной до 15 секунд');
  return { start, duration: length };
}
export async function prepareReferenceExcerpts(references, duration, api) {
  const result = [];
  for (const ref of references) {
    // A stand-in of a scene run (storyboard-model.mjs RUN_TOKEN): the Mac cuts that excerpt when the video exists.
    if (!['video', 'audio'].includes(ref.kind) || String(ref.id).startsWith('studio-run:')) { result.push(ref); continue; }
    const window = excerptWindow(ref, duration);
    const asset = await api(`/api/assets/${encodeURIComponent(ref.id)}/excerpt`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(window),
    });
    result.push({ ...ref, id: asset.asset_id, name: asset.original_name,
      sourceDuration: asset.excerpt.duration, excerptStart: 0, excerptDuration: asset.excerpt.duration, excerpt: asset.excerpt });
  }
  return result;
}
export function excerptButton(ref, getDuration, save, api, toast) {
  if (!['video', 'audio'].includes(ref.kind)) return null;
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'reference-excerpt-button';
  function refresh() {
    const w = excerptWindow(ref, getDuration());
    button.textContent = `✂ ${w.start.toFixed(1)}–${(w.start + w.duration).toFixed(1)} с`;
    button.setAttribute('aria-label', `Выбрать участок ${ref.name}`);
  }
  refresh();
  button.onclick = async () => {
    button.disabled = true;
    try {
      const info = await api(`/api/assets/${encodeURIComponent(ref.id)}/excerpt-info`);
      const total = info.duration, limit = Math.min(15, Number(getDuration()));
      let start = Math.min(Number(ref.excerptStart || 0), Math.max(0, total - 0.1));
      let end = Math.min(total, start + Number(ref.excerptDuration ?? getDuration()));
      let previewing = false;
      const dialog = document.createElement('dialog'); dialog.className = 'reference-excerpt-dialog';
      const heading = document.createElement('h3'); heading.textContent = 'Участок референса';
      const name = document.createElement('p'); name.textContent = ref.name;
      const media = document.createElement(ref.kind); media.controls = true; media.preload = 'metadata';
      media.src = `/api/assets/${encodeURIComponent(ref.id)}/file`;
      const fields = document.createElement('div'); fields.className = 'excerpt-fields';
      const track = document.createElement('div'); track.className = 'excerpt-track';
      const inputs = {}, sliders = {};
      for (const [key, text] of [['start','Начало'], ['end','Конец']]) {
        const label = document.createElement('label'); label.textContent = text + ', сек';
        const input = document.createElement('input'); input.type = 'number'; input.min = '0'; input.max = String(total); input.step = '0.1';
        input.setAttribute('aria-label', text + ' участка, секунды'); label.append(input); fields.append(label); inputs[key] = input;
        const range = document.createElement('input'); range.type = 'range'; range.min = '0'; range.max = String(total); range.step = '0.1';
        range.setAttribute('aria-label', text + ' участка на шкале'); range.className = 'excerpt-' + key;
        track.append(range); sliders[key] = range;
      }
      const note = document.createElement('p'); note.setAttribute('role', 'status');
      const apply = document.createElement('button'); apply.type = 'button'; apply.textContent = 'Использовать участок';
      function paint(syncInputs = true) {
        if (syncInputs) { inputs.start.value = String(start); inputs.end.value = String(end); }
        sliders.start.value = String(start); sliders.end.value = String(end);
        track.style.setProperty('--from', `${100 * start / total}%`); track.style.setProperty('--to', `${100 * end / total}%`);
        const length = end - start;
        apply.disabled = length < 0.099 || length > limit + 0.001 || start < 0 || end > total;
        note.textContent = `${start.toFixed(1)}–${end.toFixed(1)} с · ${length.toFixed(1)} с из ${total.toFixed(1)} с.` + (apply.disabled ? ` Выбери участок от 0.1 до ${limit} секунд.` : ' Будет вырезан и отправлен только этот участок.');
      }
      function update(key, value, seek = true) {
        const n = Number(value); if (!Number.isFinite(n)) return;
        previewing = false;
        if (key === 'start') { start = Math.max(0, Math.min(n, total - 0.1)); if (start >= end) end = Math.min(total, start + 0.1); }
        else { end = Math.min(total, Math.max(n, 0.1)); if (end <= start) start = Math.max(0, end - 0.1); }
        paint(); if (seek && media.readyState) media.currentTime = key === 'start' ? start : end;
      }
      for (const key of ['start','end']) {
        // Keep incomplete typed decimals intact until change; sliders update immediately.
        inputs[key].oninput = () => { const n = Number(inputs[key].value); if (Number.isFinite(n)) { if(key==='start')start=n;else end=n; previewing=false;paint(false); } };
        inputs[key].onchange = () => update(key, inputs[key].value);
        inputs[key].onblur = () => update(key, inputs[key].value);
        sliders[key].oninput = () => update(key, sliders[key].value);
      }
      media.onloadedmetadata = () => { media.currentTime = start; };
      media.ontimeupdate = () => { if (previewing && media.currentTime >= end) { media.pause(); previewing = false; } };
      const actions = document.createElement('div'); actions.className = 'excerpt-actions';
      for (const [key, text] of [['start','Начало с текущего кадра'], ['end','Конец с текущего кадра']]) {
        const here = document.createElement('button'); here.type = 'button'; here.textContent = text; here.onclick = () => update(key, media.currentTime, false); actions.append(here);
      }
      const preview = document.createElement('button'); preview.type = 'button'; preview.textContent = '▶ Проверить участок';
      preview.onclick = () => { previewing = true; media.currentTime = start; media.play().catch(e => {note.textContent=e.message;}); };
      const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Отмена';
      apply.onclick = async () => {
        update('start', inputs.start.value, false); update('end', inputs.end.value, false);
        if (apply.disabled) return;
        apply.disabled = true; note.textContent = 'Вырезаю выбранный участок…';
        try {
          const asset = await api(`/api/assets/${encodeURIComponent(ref.id)}/excerpt`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start,duration:end-start})});
          Object.assign(ref, {excerptStart:start,excerptDuration:end-start,preparedExcerptId:asset.asset_id});
          await save(); refresh(); dialog.close();
        } catch(e) {note.textContent=e.message;apply.disabled=false;}
      };
      close.onclick = () => dialog.close();
      dialog.addEventListener('close', () => {media.pause();media.removeAttribute('src');media.load();dialog.remove();}, {once:true});
      dialog.append(heading,name,media,fields,track,note,actions,preview,apply,close);
      document.body.append(dialog);paint();dialog.showModal();
    } catch(error) {toast(error.message);} finally {button.disabled=false;}
  };
  return button;
}
