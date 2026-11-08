import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stagingRows, svgSource } from './scene-previz.mjs';

test('each shot keeps its staging line and only its own findings', () => {
  const rows = stagingRows({
    shots: [{ shot: 1, staging_line: 'NOVA: screen-left.' }, { shot: 2, staging_line: 'ALEX: centre frame.' }],
    findings: [{ shot: 2, severity: 'error', message: 'NOVA не попадает в кадр' }, { shot: 2, severity: 'tip', message: 'край' }],
  });
  assert.deepEqual(rows, [{ shot: 1, line: 'NOVA: screen-left.', findings: [] },
    { shot: 2, line: 'ALEX: centre frame.', findings: ['Ошибка: NOVA не попадает в кадр', 'Заметка: край'] }]);
});

test('the plan is an image source, not markup', () => {
  assert.equal(svgSource('<svg><text>a&b</text></svg>'), 'data:image/svg+xml;charset=utf-8,%3Csvg%3E%3Ctext%3Ea%26b%3C%2Ftext%3E%3C%2Fsvg%3E');
});
