import { createProject } from './model.mjs?v=20261106-refmatch';
import { normalizeSceneProject, saveActiveScene, switchScene, captureReferenceState, restoreReferenceState } from './scene-project-model.mjs?v=20261031-render-state-1';

// The approved idea cards keep the director's notes and four H3 prompts together.
// Extract only fenced prompts; never send the full card as a single generation.
export function promptsFromIdea(body) {
  const section = String(body || '').split(/ГОТОВЫЕ ПРОМПТЫ H3[^\n]*\n/i)[1];
  if (!section) return [];
  const matches = [...section.matchAll(/^##\s+(\d{2})\s*[—-]\s*([^\n]+)\n\s*```text\n([\s\S]*?)\n```/gm)];
  if (matches.length !== 4 || matches.some((match, index) => Number(match[1]) !== index + 1 || !match[3].trim()))
    throw new Error('Для 40-секундной сцены нужны четыре последовательных H3-промпта.');
  return matches.map((match,index) => ({title:match[2].trim(),prompt:match[3].trim(),duration:10,
    transition:index && (/weak_reference|not a literal opening frame/i.test(match[3])) ? 'cut' : 'continue'}));
}

export function validateFranchisePacket(packet) {
  if (!packet || packet.version !== 1 || !/^[\w-]{1,100}$/.test(packet.id || '') || !packet.franchise_id || !packet.board_id
      || typeof packet.title !== 'string' || !packet.title.trim() || packet.title.length > 160
      || typeof packet.prompt !== 'string' || !packet.prompt.trim() || packet.prompt.length > 20000
      || !Array.isArray(packet.references) || packet.references.length > 12) throw new Error('Неверный пакет сцены или превышен объём материалов (до 12).');
  if (packet.shots !== undefined && (!Array.isArray(packet.shots) || packet.shots.length !== 4 ||
      packet.shots.some(shot => !shot?.title?.trim() || !shot?.prompt?.trim() || shot.duration !== 10 ||
        (shot.transition && !['cut','continue'].includes(shot.transition)))))
    throw new Error('40-секундная сцена должна содержать четыре промпта по 10 секунд.');
  const seen = new Set(), counts = { image: 0, video: 0, audio: 0 };
  for (const ref of packet.references) {
    if (!ref || typeof ref.id !== 'string' || !ref.id || !Object.hasOwn(counts, ref.kind) || seen.has(ref.id)
      || typeof ref.role !== 'string' || typeof ref.owner !== 'string' || ref.owner.length > 80) throw new Error('Проверь материалы и владельцев: один файл должен иметь одну однозначную привязку.');
    seen.add(ref.id); counts[ref.kind]++;
    if (ref.number !== counts[ref.kind]) throw new Error('Номера материалов не согласованы с их порядком.');
  }
  if (counts.video > 3 || counts.audio > 3) throw new Error('Для сцены доступны до трёх видео и трёх аудио.');
  return packet;
}
export function importFranchisePacket(source, packet) {
  validateFranchisePacket(packet);
  const project = JSON.parse(JSON.stringify(source));
  saveActiveScene(project);
  normalizeSceneProject(project);
  const id = `franchise-${packet.id}`;
  if (project.scenes.some(scene => scene.id === id)) { switchScene(project, id); return project; }
  project.sharedReferenceState ||= captureReferenceState(project);
  const counts = {image:0,video:0,audio:0};packet.references.forEach(ref=>counts[ref.kind]++);
  const referenceState = {...captureReferenceState(createProject()), references:JSON.parse(JSON.stringify(packet.references)),referenceCounters:counts,
    colorMatch:null,guideFrames:null,useVideoAudio:false};
  const cuts = packet.shots?.map((shot,index) => ({id:`${id}-shot-${index+1}`,duration:10,prompt:shot.prompt,
    startFrame:index && shot.transition !== 'cut' ? {source:'previous',asset_id:null} : null,
    ...(shot.transition === 'cut' ? {colorMatch:false} : {}),sceneShot:{title:shot.title}}))
    || [{id:`${id}-shot-1`,duration:5,prompt:packet.prompt}];
  const scene = {id,title:packet.title,cuts,sceneStyle:'',seed:'',storyboard:{linkPrevious:true,colorSource:'previous'},sceneDirector:{},
    referenceState,franchiseSource:{packet_id:packet.id,franchise_id:packet.franchise_id,board_id:packet.board_id,scene_id:packet.scene_id}};
  project.scenes.push(scene);
  switchScene(project,id);
  restoreReferenceState(project,scene);
  return project;
}
