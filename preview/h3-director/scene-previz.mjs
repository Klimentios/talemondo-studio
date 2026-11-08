// «Превиз постановки» in an agent's scene card: the floor plan the agent staged (scene_draft.staging) computed by
// local_studio/staging.py — top-down plan with every camera's angle of view, and per shot what the lens sees.
// Read-only: it never changes the scene or the canvas. The SVG is shown as an image, never injected as markup.

const SEVERITY = { error: 'Ошибка', warning: 'Внимание', tip: 'Заметка' };

/** Per shot: its staging line and the findings about it, in shot order. */
export function stagingRows(result) {
  return (result.shots || []).map((shot) => ({
    shot: shot.shot,
    line: shot.staging_line,
    findings: (result.findings || []).filter((f) => f.shot === shot.shot)
      .map((f) => `${SEVERITY[f.severity] || f.severity}: ${f.message}`),
  }));
}

export function svgSource(svg) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

/** A closed <details>; the plan is computed the first time it is opened. */
export function scenePreviz(scene, el) {
  if (!scene.staging) return null;
  const body = el('div', { className: 'ad-previz' }, el('small', {}, 'Открой, чтобы рассчитать план.'));
  const box = el('details', { className: 'ad-previz-box' }, el('summary', {}, 'Превиз постановки'), body);
  let loaded = false;
  box.addEventListener('toggle', async () => {
    if (!box.open || loaded) return;
    loaded = true;
    body.replaceChildren(el('small', {}, 'Считаю план…'));
    try {
      const response = await fetch('/api/staging/compile', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(scene.staging) });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'План постановки не принят');
      body.replaceChildren(
        el('img', { src: svgSource(result.previz_svg), alt: 'План сцены сверху: герои, ось и камеры', className: 'ad-previz-plan' }),
        el('ol', {}, ...stagingRows(result).map((row) => el('li', {}, el('p', {}, row.line),
          ...row.findings.map((text) => el('small', { className: 'ad-previz-finding' }, text))))));
    } catch (error) {
      loaded = false;
      body.replaceChildren(el('small', {}, error.message));
    }
  });
  return box;
}
