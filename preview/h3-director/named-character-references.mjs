// Resolve a named replacement to a verified franchise identity before queueing.
// Ambiguous names and missing requested outfits must be handled in the library.
import { nameKey } from './prompt-warnings.mjs?v=20261004-contract-1';

const REQUEST = /(?:замен(?:и|ить|яем|яется|яет)|подмен(?:и|ить)|replace|replaces)[^\n.!?]{0,100}?(?:на|with)\s+(?:(?:персонажа|героя|кота|человека|character)\s+)?([А-ЯЁA-Z][А-ЯЁа-яёA-Za-z-]{2,})/giu;
const LOOKS = [
  [/хэллоуин|halloween/iu, 'Хэллоуин'],
  [/рождеств|christmas|santa|санта/iu, 'Рождество'],
  [/супергеро|superhero/iu, 'Супергерой'],
];

export function namedReplacementRequest(prompt) {
  const text = String(prompt || '');
  const names = [...new Set([...text.matchAll(REQUEST)].map(match => match[1].toLocaleLowerCase('ru')))];
  const looks = LOOKS.filter(([pattern]) => pattern.test(text)).map(([, label]) => label);
  // Several looks are ambiguous only for a hero whose outfit is still to be picked (resolveNamedReplacements).
  return { names, look: looks.length === 1 ? looks[0] : '', looks };
}

export function resolveNamedReplacements(prompt, entries, attached = []) {
  const { names, look, looks } = namedReplacementRequest(prompt);
  const result = [];
  for (const name of names) {
    const requestedKey = nameKey(name);
    const alreadyHasIdentity = attached.some(ref => nameKey(ref.owner) === requestedKey
      && ['Лицо героя', 'Персонаж целиком'].includes(ref.role));
    // A full-body picture or a costume of this hero is already attached: it is the look (two heroes in two
    // costumes in one swap must not ask «выбери один костюм»).
    if (attached.some(ref => nameKey(ref.owner) === requestedKey && ['Персонаж целиком', 'Костюм'].includes(ref.role))) continue;
    if (looks.length > 1) throw new Error(`Для ${name} указано несколько образов: ${looks.join(', ')}. Выбери один костюм.`);
    if (alreadyHasIdentity && !look) continue;
    let candidates = entries.filter(({ asset, info }) => asset.kind === 'image' && !info.hidden
      && nameKey(info.characterName) === requestedKey && info.franchise);
    const franchises = [...new Set(candidates.map(({ info }) => info.franchise))];
    const mentioned = franchises.filter(franchise => prompt.toLocaleLowerCase('ru').includes(franchise.toLocaleLowerCase('ru')));
    if (mentioned.length === 1) candidates = candidates.filter(({ info }) => info.franchise === mentioned[0]);
    else if (franchises.length > 1) throw new Error(`Для ${name} есть несколько франшиз: ${franchises.join(', ')}. Укажи франшизу в тексте или выбери персонажа в библиотеке.`);
    if (!candidates.length) throw new Error(`В библиотеке нет подтверждённого портрета ${name}. Добавь его в раздел персонажа.`);
    const role = entry => entry.info.preferredRole || entry.asset.role;
    const identity = candidates.find(entry => role(entry) === 'Лицо героя' && !entry.info.look && /без костюма|портрет и тело/iu.test(`${entry.info.displayName || ''} ${entry.asset.original_name || ''}`))
      || candidates.find(entry => role(entry) === 'Лицо героя' && !entry.info.look)
      || candidates.find(entry => role(entry) === 'Персонаж целиком' && !entry.info.look);
    if (!identity) throw new Error(`У ${name} нет отдельного портрета или тела. Костюм не заменяет внешность героя.`);
    const selected = alreadyHasIdentity ? [] : [identity];
    if (look) {
      const outfit = candidates.find(entry => role(entry) === 'Костюм' && entry.info.look === look);
      if (!outfit) throw new Error(`Для ${name} нет проверенного костюма «${look}». Выбери образ в библиотеке или загрузи референс.`);
      if (!attached.some(ref => ref.id === outfit.asset.asset_id)) selected.push(outfit);
    }
    result.push(...selected);
  }
  return [...new Map(result.map(entry => [entry.asset.asset_id, entry])).values()];
}
