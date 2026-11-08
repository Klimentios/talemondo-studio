// Embedded ComfyUI editor (comfyui.html). ?wf=<Studio workflow id> puts that workflow into ComfyUI's Studio inbox
// and opens it; «⇪ Воркфлоу в Studio» in the editor sends it back to the same workflow (h3-studio-bridge).
const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, { headers: options.body ? { 'Content-Type': 'application/json' } : {}, ...options });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(data?.detail || `HTTP ${response.status}`);
  return data;
}

function status(text, tone = '') {
  $('wfStatus').textContent = text;
  $('wfStatus').dataset.tone = tone;
}

function empty(html) {
  $('cfFrame').hidden = true;
  $('cfEmpty').hidden = false;
  $('cfEmpty').innerHTML = html;
}

function show(url) {
  $('cfFrame').src = url;
  $('cfFrame').hidden = false;
  $('cfEmpty').hidden = true;
  $('cfTab').href = url;
  $('cfTab').hidden = false;
}

async function start() {
  const wf = new URLSearchParams(location.search).get('wf');
  if (wf) $('cfBack').href = `/workflows.html?id=${encodeURIComponent(wf)}`;
  const info = await api('/api/comfy/info');
  if (!info.url) return empty('<h2>ComfyUI не подключён</h2><p class="wf-hint">Поднимите GPU-сервер или задайте адрес ComfyUI.</p><div class="wf-run"><a class="k-btn k-btn--primary" href="/setup.html">Первый запуск</a></div>');
  if (!info.reachable) return empty(`<h2>ComfyUI не отвечает</h2><p class="wf-hint">${esc(info.url)} недоступен: сервер выключен или туннель не поднят.</p><div class="wf-run"><a class="k-btn k-btn--primary" href="/?view=compute">GPU и модели</a></div>`);
  if (info.bridge === 'outdated') status('На GPU старый мост Studio: кнопка «⇪ Воркфлоу в Studio» появится после обновления расширений.', 'bad');
  if (!wf) return show(info.url);
  status('Передаю воркфлоу в ComfyUI…');
  try {
    const opened = await api(`/api/workflows/${encodeURIComponent(wf)}/open-in-comfy`, { method: 'POST' });
    show(opened.url);
    status('Изменения вернутся в Studio кнопкой «⇪ Воркфлоу в Studio» (справа сверху в ComfyUI).', 'good');
  } catch (error) {
    status(error.message, 'bad');
    show(info.url);
  }
}

window.studioBoot?.hold(start().catch((error) => empty(`<p class="wf-error">${esc(error.message)}</p>`)));
