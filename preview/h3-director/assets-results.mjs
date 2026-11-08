import { jobTitle } from './job-title.mjs?v=20261008-1';
export function renderAssetResults(root, jobs, limit = 12) {
  const completed = jobs.filter((job) => job.state === 'completed');
  root.replaceChildren();
  if (!completed.length) {
    const empty = document.createElement('p');
    empty.className = 'library-empty';
    empty.textContent = 'Готовых роликов пока нет. Они появятся здесь после генерации.';
    root.append(empty);
    return { shown: 0, total: 0 };
  }
  for (const job of completed.slice(0, limit)) {
    const card = document.createElement('article');
    card.className = 'asset-result-card';
    const video = document.createElement('video');
    video.src = `/api/jobs/${encodeURIComponent(job.job_id)}/video`;
    video.controls = true;
    video.preload = 'metadata';
    video.playsInline = true;
    video.setAttribute('aria-label', `Готовый ролик ${job.job_id.slice(0, 8)}`);
    const name = document.createElement('strong');
    name.textContent = jobTitle(job, 90);
    const actions = document.createElement('div');
    actions.className = 'asset-result-actions';
    const toStory = document.createElement('button');
    toStory.type = 'button';
    toStory.dataset.jobId = job.job_id;
    toStory.textContent = 'В сюжет';
    const download = document.createElement('a');
    download.href = `/api/jobs/${encodeURIComponent(job.job_id)}/video?download=true`;
    download.download = `h3-${job.job_id}.mp4`;
    download.textContent = 'Скачать MP4';
    actions.append(toStory, download);
    card.append(video, name, actions);
    root.append(card);
  }
  return { shown: Math.min(limit, completed.length), total: completed.length };
}
