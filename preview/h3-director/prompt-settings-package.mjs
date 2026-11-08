const contract = typeof window === 'undefined'
  ? JSON.parse(await (await import('node:fs/promises')).readFile(new URL('./prompt-settings-contract.json', import.meta.url),'utf8'))
  : await fetch(new URL('./prompt-settings-contract.json?v=20261102-agent-prepared-1',import.meta.url)).then(r=>r.json());
const block = /```studio-settings\s*\n([\s\S]*?)\n```/g;
export function promptSettingsPackage(text, settings = {}) {
  const matches = [...String(text).matchAll(block)];
  if (!matches.length) {
    if (String(text).includes('```studio-settings')) throw new Error('Блок настроек промпта не завершён');
    return { prompt: text, settings: { ...settings }, overrides: {} };
  }
  if (matches.length !== 1) throw new Error('В промпте должен быть один блок studio-settings');
  let data; try { data=JSON.parse(matches[0][1]); } catch { throw new Error('В блоке studio-settings некорректный JSON'); }
  if(!data || data.version!==1 || !data.settings || Array.isArray(data.settings) || typeof data.settings!=='object' || Object.keys(data).some(k=>!['version','settings'].includes(k))) throw new Error('Нужен пакет version: 1 и settings');
  const unknown=Object.keys(data.settings).filter(k=>!contract.keys.includes(k));
  if(unknown.length) throw new Error(`Неподдерживаемые настройки промпта: ${unknown.join(', ')}`);
  return { prompt: String(text).replace(matches[0][0],'').trim(), settings: { ...settings, film_look: null, color_grade: null, camera_state: null, camera_optics: null, director_config: null, ...data.settings }, overrides: { film_look: null, color_grade: null, camera_state: null, camera_optics: null, director_config: null, ...data.settings } };
}
