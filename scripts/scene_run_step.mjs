// One step of a scene run on the Mac (local_studio/scene_runs.py): the same chainStep as «Собрать сцену» in the tab.
// stdin: { cuts, jobs: [job], board, references, options }  →  stdout: the step (storyboard-model.mjs chainStep).
import { chainStep, jobProgress, nativeContinuationSource } from '../preview/h3-director/storyboard-model.mjs';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { cuts, jobs = [], board, references = [], options = {} } = JSON.parse(input);
const jobsById = new Map(jobs.map((job) => [job.job_id, job]));
const step = chainStep(cuts, jobsById, board, references, options);
if (step.action === 'wait') step.progress = jobProgress(jobsById.get(cuts[step.index].shotJobId));
if (step.action === 'enqueue' && board.motionContext && step.plan.startFrame?.source === 'previous') {
  try { step.continuationFrom = nativeContinuationSource(step.plan, cuts[step.index - 1]); }
  catch (error) { Object.assign(step, { action: 'stop', message: `Шот ${step.index + 1}: ${error.message}` }); }
}
process.stdout.write(JSON.stringify(step));
