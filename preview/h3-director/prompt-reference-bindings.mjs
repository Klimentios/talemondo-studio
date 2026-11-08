import { pictureDeclarations } from './reference-declarations.mjs?v=20261014-heading-1';
// Reconcile explicit authored tags with the user's selected roles and named owners.
// Replace a permutation simultaneously so face/costume/environment never cross.
export function alignPromptPictures(prompt, references) {
  const text=String(prompt||'');
  const definitions=text.split(/^subject_definitions:\s*$/m)[1]?.split(/^\w[\w -]*:\s*$/m)[0] || text;
  const mapping=new Map();
  for(const {number,roles,owner} of pictureDeclarations(definitions)){
    const candidates=references.filter(ref=>ref.kind==='image'&&roles.includes(ref.role)&&(!owner||ref.owner===owner));
    if(candidates.length!==1)continue;
    mapping.set(number,Number(candidates[0].number));
  }
  const changed=[...mapping].filter(([from,to])=>from!==to);
  if(!changed.length)return text;
  const targets=[...mapping.values()];
  if(new Set(targets).size!==targets.length)throw new Error('Одному фото назначены несколько разных ролей в промпте. Проверь лицо, костюм и окружение.');
  const mentioned=[...text.matchAll(/<Picture (\d+)>/g)].map(m=>Number(m[1]));
  if(mentioned.some(number=>!mapping.has(number)&&targets.includes(number)))
    throw new Error('Не удалось однозначно согласовать ссылки на фотографии: укажи роль и героя для каждого источника.');
  return text.replace(/<Picture (\d+)>/g,(tag,n)=>mapping.has(Number(n))?`<Picture ${mapping.get(Number(n))}>`:tag);
}
