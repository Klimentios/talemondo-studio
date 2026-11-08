// Each task sends only the layers visible for that task. The saved draft keeps
// every layer, so switching tabs never discards the user's work.
export function projectForWorkflow(project, workflow) {
  const result = structuredClone(project);
  result.lines = result.lines.filter((line) => line.text.trim());
  if (workflow === 'speech') {
    result.mix.music_asset_id = '';
    result.mix.music_prompt = '';
    result.mix.music_lyrics = '';
    result.mix.effects_asset_id = '';
    result.mix.effects_prompt = '';
  } else if (workflow === 'music') {
    result.mode = 'audio';
    result.source = { kind: 'none', id: '' };
    result.output_format = 'wav';
    result.mix.source_level = 0;
    result.lines = [];
    result.lip_sync = false;
    result.lip_sync_target = '';
    result.mix.effects_asset_id = '';
    result.mix.effects_prompt = '';
  } else if (workflow === 'voice' || workflow === 'translate') {
    result.mode = 'video';
    result.mix.source_level = 0;
    result.mix.music_asset_id = '';
    result.mix.music_prompt = '';
    result.mix.music_lyrics = '';
    result.mix.effects_asset_id = '';
    result.mix.effects_prompt = '';
    result.lip_sync = false;
    result.lip_sync_target = '';
  }
  return result;
}

// A freshly uploaded sample becomes usable immediately when its destination is
// unambiguous. Existing lyrics and style are never discarded by an upload.
export function placeUploadedAudio(project, workflow, assetId) {
  if (workflow === 'music') {
    if (project.mix.music_prompt.trim() || project.mix.music_lyrics.trim()) return 'library';
    project.mix.music_asset_id = assetId;
    return 'music';
  }
  if (['speech', 'voice', 'translate'].includes(workflow) && project.lines.length === 1) {
    project.lines[0].voice_mode = 'reference';
    project.lines[0].voice_asset_id = assetId;
    return 'voice';
  }
  return 'library';
}
