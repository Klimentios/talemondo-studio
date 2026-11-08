// Node view of a ComfyUI API graph for «Свои воркфлоу» (workflows.html): nodes in columns by depth, links as
// curves, click a node to inspect it. With `inspector(id, node, element)` the caller owns the side panel
// (workflow-graph-edit.mjs: schema-checked values, links, nodes and LoRAs); without it, literal values only.
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const isLink = (value) => Array.isArray(value) && value.length === 2 && typeof value[0] === 'string' && Number.isInteger(value[1]);
const W = 210, ROW = 20, HEAD = 30, GAP_X = 70, GAP_Y = 26, PAD = 24;

function layout(graph) {
  const ids = Object.keys(graph);
  const depth = {};
  const visit = (id, trail = new Set()) => {
    if (depth[id] !== undefined) return depth[id];
    if (trail.has(id)) return 0; // a cycle is ComfyUI's problem to report; draw it anyway
    trail.add(id);
    const sources = Object.values(graph[id].inputs || {}).filter(isLink).map((link) => link[0]).filter((s) => graph[s]);
    depth[id] = sources.length ? Math.max(...sources.map((s) => visit(s, trail) + 1)) : 0;
    return depth[id];
  };
  ids.forEach((id) => visit(id));
  const columns = [];
  ids.sort((a, b) => Number(a) - Number(b) || a.localeCompare(b)).forEach((id) => (columns[depth[id]] ||= []).push(id));
  const boxes = {};
  columns.forEach((column, x) => {
    let y = PAD;
    column.forEach((id) => {
      const rows = Object.keys(graph[id].inputs || {}).length || 1;
      const h = HEAD + rows * ROW + 8;
      boxes[id] = { x: PAD + x * (W + GAP_X), y, h };
      y += h + GAP_Y;
    });
  });
  const width = PAD * 2 + columns.length * (W + GAP_X);
  const height = Math.max(...Object.values(boxes).map((b) => b.y + b.h), 200) + PAD;
  return { boxes, width, height };
}

function short(value) {
  if (typeof value === 'string') return value.length > 22 ? `${value.slice(0, 21)}…` : value;
  if (typeof value === 'number') return String(Math.round(value * 1000) / 1000);
  return JSON.stringify(value).slice(0, 22);
}

export function mountGraph(container, sourceGraph, { missing = [], onChange = () => {}, inspector: custom = null, selected: initial = null, highlight = [] } = {}) {
  const graph = structuredClone(sourceGraph);
  const missingSet = new Set(missing);
  let selected = null;
  const marked = new Set(highlight);
  const { boxes, width, height } = layout(graph);
  container.innerHTML = `<div class="wfg"><div class="wfg-canvas" style="width:${width}px;height:${height}px">
      <svg class="wfg-links" width="${width}" height="${height}" aria-hidden="true"></svg></div></div>
    <aside class="wfg-inspector" aria-live="polite"><p class="wf-hint">Нажми на узел, чтобы изменить его значения.</p></aside>`;
  const canvas = container.querySelector('.wfg-canvas');
  const svg = container.querySelector('.wfg-links');
  const inspector = container.querySelector('.wfg-inspector');

  const draw = () => {
    canvas.querySelectorAll('.wfg-node').forEach((node) => node.remove());
    let paths = '';
    for (const [id, node] of Object.entries(graph)) {
      const box = boxes[id];
      const rows = Object.entries(node.inputs || {});
      const el = document.createElement('button');
      el.type = 'button';
      el.className = 'wfg-node';
      el.dataset.id = id;
      el.dataset.missing = String(missingSet.has(node.class_type));
      el.dataset.changed = String(marked.has(id));
      el.setAttribute('aria-pressed', String(selected === id));
      Object.assign(el.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${W}px`, height: `${box.h}px` });
      el.innerHTML = `<span class="wfg-title">${esc(node._meta?.title || node.class_type)}<small>#${esc(id)}</small></span>
        ${rows.map(([name, value]) => `<span class="wfg-row" data-link="${isLink(value)}"><span>${esc(name)}</span><b>${isLink(value) ? '●' : esc(short(value))}</b></span>`).join('')}`;
      el.addEventListener('click', () => select(id));
      canvas.append(el);
      rows.forEach(([, value], index) => {
        if (!isLink(value) || !boxes[value[0]]) return;
        const from = boxes[value[0]];
        const x1 = from.x + W, y1 = from.y + HEAD / 2 + value[1] * 8;
        const x2 = box.x, y2 = box.y + HEAD + index * ROW + ROW / 2;
        const bend = Math.max(40, (x2 - x1) / 2);
        paths += `<path d="M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}"/>`;
      });
    }
    svg.innerHTML = paths;
  };

  const field = (name, value) => {
    const key = esc(name);
    if (typeof value === 'boolean') return `<label class="wf-field"><span>${key}</span><input type="checkbox" data-input="${key}" ${value ? 'checked' : ''}></label>`;
    if (typeof value === 'number') return `<label class="wf-field"><span>${key}</span><input class="k-field" type="number" step="any" data-input="${key}" data-type="number" value="${esc(value)}"></label>`;
    if (typeof value === 'string' && (value.length > 50 || value.includes('\n'))) return `<label class="wf-field"><span>${key}</span><textarea class="k-field" data-input="${key}">${esc(value)}</textarea></label>`;
    if (typeof value === 'string') return `<label class="wf-field"><span>${key}</span><input class="k-field" data-input="${key}" value="${esc(value)}"></label>`;
    return `<label class="wf-field"><span>${key} (JSON)</span><input class="k-field" data-input="${key}" data-type="json" value="${esc(JSON.stringify(value))}"></label>`;
  };

  function select(id) {
    selected = id;
    canvas.querySelectorAll('.wfg-node').forEach((el) => el.setAttribute('aria-pressed', String(el.dataset.id === id)));
    const node = graph[id];
    if (custom) { custom(id, node, inspector); return; }
    const entries = Object.entries(node.inputs || {});
    const links = entries.filter(([, v]) => isLink(v));
    const literals = entries.filter(([, v]) => !isLink(v));
    inspector.innerHTML = `<h3>${esc(node._meta?.title || node.class_type)}</h3>
      <p class="wf-hint"><code>${esc(node.class_type)}</code> · узел #${esc(id)}${missingSet.has(node.class_type) ? ' · <b class="wfg-bad">нет на GPU</b>' : ''}</p>
      ${literals.map(([name, value]) => field(name, value)).join('') || '<p class="wf-hint">Нет значений: только связи.</p>'}
      ${links.length ? `<p class="wf-hint">Связи: ${links.map(([name, v]) => `${esc(name)} ← #${esc(v[0])}`).join(', ')}</p>` : ''}`;
    inspector.querySelectorAll('[data-input]').forEach((input) => input.addEventListener('change', () => {
      const name = input.dataset.input;
      let value;
      if (input.type === 'checkbox') value = input.checked;
      else if (input.dataset.type === 'number') value = Number(input.value);
      else if (input.dataset.type === 'json') {
        try { value = JSON.parse(input.value); } catch { input.setCustomValidity('Неверный JSON'); input.reportValidity(); return; }
      } else value = input.value;
      if (typeof value === 'number' && !Number.isFinite(value)) return;
      input.setCustomValidity('');
      graph[id].inputs[name] = value;
      draw();
      onChange(graph);
    }));
  }

  draw();
  if (initial && graph[initial]) select(initial);
  return { graph: () => graph, select };
}
