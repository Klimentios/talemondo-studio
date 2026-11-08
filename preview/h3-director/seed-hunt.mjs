// «🎲 Подобрать seed» on a job card (local_studio/seed_hunt.py): three small previews of the same job with
// different seeds go into the queue; each preview card says which one it is and, when ready, offers
// «▶▶ Дорендерить этот» — the picked latent finished at full size (app/h3graph/seed_hunt.py).
const state = new Map();

export function seedHuntNote(settings, en = false) {
  const hunt = settings.seed_hunt;
  if (!hunt) return '';
  if (hunt.role === 'finish') return en ? `Seed hunt · finish of preview ${hunt.index}` : `Подбор seed · дорендер превью ${hunt.index}`;
  return en ? `Seed hunt · preview ${hunt.index} (half size)` : `Подбор seed · превью ${hunt.index} (половинный размер)`;
}

async function post(url, body) {
  const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

function actionButton(parent, job, label, run) {
  const button = document.createElement('button');
  button.type = 'button';
  const done = state.get(job.job_id);
  button.textContent = done || label;
  button.disabled = Boolean(done);
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      state.set(job.job_id, await run());
      button.textContent = state.get(job.job_id);
    } catch (error) {
      button.disabled = false;
      button.textContent = `${label} · ${error.message}`;
    }
  });
  parent.append(button);
}

export function appendSeedHuntActions(parent, job, settings, en = false) {
  const hunt = settings.seed_hunt;
  const id = encodeURIComponent(job.job_id);
  if (!hunt && job.state === 'completed') {
    actionButton(parent, job, en ? '🎲 Find a better seed' : '🎲 Подобрать seed', async () => {
      await post(`/api/jobs/${id}/seed-hunt`, { count: 3 });
      return en ? '✓ 3 previews queued' : '✓ 3 превью в очереди';
    });
  } else if (hunt?.role === 'preview' && job.state === 'completed') {
    actionButton(parent, job, en ? '▶▶ Finish this one' : '▶▶ Дорендерить этот', async () => {
      await post(`/api/jobs/${id}/seed-hunt/finish`);
      return en ? '✓ Finish queued' : '✓ Дорендер в очереди';
    });
  }
}
