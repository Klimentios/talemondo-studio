export function cutWithJob(cut, job) {
  if (job.state !== 'completed') throw new Error('В сюжет можно добавить только готовый ролик');
  const jobId = job.job_id;
  const takes = [...(cut.takes || (cut.shotJobId ? [{ id: cut.shotJobId, jobId: cut.shotJobId }] : []))];
  if (!takes.some((take) => (take.jobId || take.id) === jobId)) takes.push({ id: jobId, jobId, createdAt: job.created_at || new Date().toISOString() });
  let duration = cut.duration;
  try {
    const seconds = Number(JSON.parse(job.settings_json || '{}').duration);
    if (Number.isInteger(seconds) && seconds >= 4 && seconds <= 15) duration = seconds;
  } catch { /* Preserve the current shot duration when legacy job metadata is unavailable. */ }
  return { ...cut, prompt: cut.prompt?.trim() || job.original_prompt || '', duration,
    shotJobId: jobId, selectedTakeId: jobId, takes, shotQc: null, shotQcPending: false, shotQcError: '' };
}

export async function sendJobToStory(job, studio) {
  if (job.state !== 'completed') throw new Error('В сюжет можно добавить только готовый ролик');
  let target = studio.cuts().find((cut) => cut.prompt?.trim() === job.original_prompt?.trim());
  if (!target) target = studio.cuts().find((cut) => !cut.prompt?.trim() && !cut.shotJobId);
  // A finished clip is its own shot: it did not start from the previous one's last frame.
  if (!target) target = studio.addShot({ link: false });
  studio.editCuts((cuts) => cuts.map((cut) => cut.id === target.id ? cutWithJob(cut, job) : cut));
  await studio.saveDraft();
  return target.id;
}
