import test from 'node:test';
import assert from 'node:assert/strict';
import { draftChips, draftFromEvent, isDraftTool, sceneChips, sceneCuts, sceneFromEvent, sceneLines, sceneSeconds, screenContext, unknownDirectorPresets } from './agent-draft-model.mjs';
import { DIRECTOR_PRESETS } from './director-presets.mjs';
import { settingsForShot } from './scene-queue.mjs';

test('a composer_draft tool event carries a draft, other tools do not', () => {
  const draft = { prompt: '<Picture 1> ALEX turns', duration: 10, aspect_ratio: '9:16', quality: 0.9,
    references: [{ asset_id: 'a', role: 'Лицо героя' }] };
  assert.equal(isDraftTool('mcp__h3-studio__composer_draft'), true);
  assert.deepEqual(draftFromEvent({ kind: 'tool', name: 'composer_draft', status: 'done', draft }), draft);
  assert.equal(draftFromEvent({ kind: 'tool', name: 'composer_draft', status: 'error', draft }), null);
  assert.equal(draftFromEvent({ kind: 'tool', name: 'composer_draft', status: 'started', draft }), null);
  assert.equal(draftFromEvent({ kind: 'tool', name: 'assets_list', status: 'done', draft }), null);
  assert.equal(draftFromEvent({ kind: 'tool', name: 'composer_draft', status: 'done', draft: { prompt: ' ' } }), null);
  assert.deepEqual(draftChips(draft), ['10 с', '9:16', '0,90 Мп', '1 реф. · заменить текущие']);
  assert.match(draftChips({ ...draft, replace_references: false }).at(-1), /добавить/);
});

test('the screen context names reference tags and stays small', () => {
  const context = screenContext({ prompt: 'x'.repeat(9000), duration: '5', aspect: '16:9', quality: '0.9',
    references: [{ id: 'a', kind: 'image', number: 2, role: 'Костюм', owner: 'ALEX', name: 'cyber.png' }],
    cuts: [{ id: 1 }, { id: 2 }], scene: 'Сцена 01' });
  assert.equal(context.prompt.length, 6000);
  assert.deepEqual(context.settings, { duration: 5, aspect_ratio: '16:9', quality: 0.9 });
  assert.equal(context.references[0].tag, '<Picture 2>');
  assert.equal(context.board.length, 2);
});

test('a scene draft becomes board cuts with keyframe text, camera and seams', () => {
  const shot = { title: 'Hook', beat: 'Нова замечает шорох', prompt: ' <Picture 1> NOVA freezes ', first_frame: 'NOVA on the sofa, screen left',
    duration: 4, start_frame: 'new_keyframe', framing: 'close', lens_mm: 85, camera_motion: 'dolly', transition_in: 'cut',
    state_before: 'calm', state_after: 'alert' };
  const scene = { title: 'S', shots: [{ ...shot, start_frame: 'previous_end' }, { ...shot, title: 'Leap', duration: 6, start_frame: 'previous_end', transition_in: 'cut_on_action' }] };
  assert.equal(sceneFromEvent({ kind: 'tool', name: 'mcp__h3-studio__scene_draft', status: 'done', draft: scene }), scene);
  assert.equal(sceneFromEvent({ kind: 'tool', name: 'scene_draft', status: 'error', draft: scene }), null);
  assert.equal(sceneSeconds(scene), 10);
  const cuts = sceneCuts(scene, [{ id: 'cut-4' }]);
  assert.deepEqual(cuts.map((cut) => cut.id), ['cut-5', 'cut-6']);
  assert.deepEqual(cuts.map((cut) => cut.startFrame.source), ['keyframe', 'previous']);  // shot 1 never continues
  assert.equal(cuts[0].prompt, '<Picture 1> NOVA freezes');
  assert.equal(cuts[0].sceneShot.first_frame, 'NOVA on the sofa, screen left');
  assert.equal(cuts[0].sceneShot.action, '', 'opening image is never appended as video action');
  assert.equal(cuts[1].sceneShot.transition_in, 'cut_on_action');
  const staged = sceneCuts({ shots: [{ ...shot, camera_direction: 'forward', camera_speed: 'fast',
    staging_line: 'NOVA is screen left 1.2 m from the camera, in close-up, looking toward screen right.' }] })[0].sceneShot;
  assert.equal(staged.staging_line, 'NOVA is screen left 1.2 m from the camera, in close-up, looking toward screen right.');
  assert.equal(staged.camera_direction, 'forward');
  assert.equal(staged.camera_speed, 'fast');
  assert.equal('staging_line' in cuts[0].sceneShot, false, 'no staging: no empty field');
  assert.match(sceneLines(scene)[1].meta, /← cut_on_action · с последнего кадра/);
});

test('a scene sets up the whole canvas: director presets per shot, render settings and camera reach the render', () => {
  const shot = { title: 'Hook', beat: 'b', prompt: '<Picture 1> NOVA', first_frame: 'f', duration: 5, start_frame: 'new_keyframe',
    framing: 'medium_close', angle: 'low', lens_mm: 35, camera_motion: 'zoom', transition_in: 'cut', emotion: 'tense',
    state_before: 'a', state_after: 'b', director: { focus: 'focus.shallow', movement: 'movement.whip_pan_right', lighting: 'lighting.neon' } };
  const scene = { title: 'S', mode: 'realistic', quality: 0.98, steps: 30,
    director: { camera: 'camera.alexa35', lighting: 'lighting.golden', color: 'color.teal_orange' },
    shots: [shot, { ...shot, director: undefined }] };
  const cuts = sceneCuts(scene);
  assert.deepEqual(cuts[0].director, { camera: 'camera.alexa35', lighting: 'lighting.neon', color: 'color.teal_orange',
    focus: 'focus.shallow', movement: 'movement.whip_pan_right' });  // the shot's own preset wins over the scene's
  assert.deepEqual(cuts[1].director, scene.director);
  assert.equal(sceneCuts({ shots: [{ ...shot, director: undefined }] })[0].director, undefined, 'no set: the form keeps its own');
  assert.deepEqual(unknownDirectorPresets(scene, DIRECTOR_PRESETS), []);
  assert.deepEqual(unknownDirectorPresets({ ...scene, director: { lighting: 'color.warm' } }, DIRECTOR_PRESETS), ['сцена: lighting color.warm']);
  assert.deepEqual(sceneChips(scene), ['2 шотов', '10 с', 'реализм', '0,98 Мп', '30 шагов', 'режиссура: 3']);
  assert.match(sceneLines(scene)[0].meta, /снизу · 35 мм · zoom/);
  // The board camera of the shot wins over its presets; a whip pan's own move is dropped for another path.
  const config = settingsForShot({ director_config: { movement: { id: 'movement.whip_pan_right',
    parameters: { movement: 'pan', camera_move: 'whip_pan_right', direction: 'right', speed: 'fast' } } } }, cuts[0]).director_config;
  assert.deepEqual(config.shot.parameters, { framing: 'medium_close', angle: 'low', emotion: 'tense', transition_in: 'cut' });
  assert.equal(config.lens.parameters.focal_length_mm, 35);
  assert.deepEqual(config.movement.parameters, { movement: 'zoom', speed: 'fast' });
});

test('an accepted recipe becomes the form settings it was proven with', async () => {
  const { recipeFormSettings, draftChips } = await import('./agent-draft-model.mjs');
  assert.deepEqual(recipeFormSettings({ width: 640, height: 1152, profile: 'bf16', duration: 11, megapixels: 0.7, steps: 24,
    cache_mode: 'safe', style_adapter: 'character_swap', ref_image_size: 'max', seed: 21919721, audio: 'original' }),
  { mode: 'replace', steps: 24, seed: 21919721, cache_mode: 'safe', ref_image_size: 'max', use_video_audio: true, quality: 0.8, aspect_ratio: '9:16' });
  // agents render at 0.8 MP by default (2026-11-04), whatever size the recipe was proven at
  assert.equal(recipeFormSettings({ width: 608, height: 1056 }).quality, 0.8);
  assert.deepEqual(draftChips({ recipe_id: 'clip-197', mode: 'replace', references: [
    { asset_id: 'v', role: 'Исходное видео', performers: [{ owner: 'NOVA', performer: 'left cat' }, { owner: 'ORION', performer: 'right cat' }] }] }),
  ['рецепт clip-197', 'замена героя', 'замена: NOVA, ORION', '1 реф. · заменить текущие']);
});
