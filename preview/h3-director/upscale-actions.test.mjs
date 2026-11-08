import test from 'node:test';
import assert from 'node:assert/strict';

class FakeNode {
  constructor(tag) { this.tag = tag; this.dataset = {}; this.children = []; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
}
globalThis.document = { createElement: (tag) => new FakeNode(tag) };
const { appendUpscaleActions } = await import('./upscale-actions.mjs');

test('a finished clip does not offer new enhancement jobs', () => {
  const actions = new FakeNode('div');
  appendUpscaleActions(actions, { job_id: 'j', state: 'completed' }, []);
  assert.deepEqual(actions.children, []);
});

test('results are listed per mode and a running pass blocks new ones', () => {
  const actions = new FakeNode('div');
  appendUpscaleActions(actions, { job_id: 'j', state: 'completed' }, [
    { upscale_id: 'a', source_job_id: 'j', state: 'running', mode: 'smooth' },
    { upscale_id: 'b', source_job_id: 'j', state: 'completed', mode: 'upscale' },
  ]);
  assert.match(actions.children[0].textContent, /Скачать апскейл 2×/);
  assert.match(actions.children[1].textContent, /плавность 48 fps: обработка/);
  const buttons = actions.children.filter((n) => n.tag === 'button');
  // A running pass blocks new passes; the only button is cancelling the running one.
  assert.equal(buttons.length, 1);
  assert.equal(buttons[0].dataset.action, 'upscale-cancel');
  assert.equal(buttons[0].dataset.upscaleId, 'a');
});
