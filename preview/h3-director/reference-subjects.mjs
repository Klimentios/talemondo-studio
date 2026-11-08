// Materialize explicit pasted hero bindings for the official H3 validator.
// Keep authored descriptions intact; never derive owners from upload order.
import { subjectNames } from './reference-owners.mjs?v=20261029-scene-run-2';

const escape = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export function bindExplicitSubjects(prompt, references) {
  const text = String(prompt || '');
  const header = /^subject_definitions:[ \t]*\r?\n/m.exec(text);
  if (!header) return text;
  const offset = header.index + header[0].length;
  const definitions = text.slice(offset).split(/^\w[\w -]*:\s*$/m)[0];
  if (/<Subject\s+\d+>\s+is\b/i.test(definitions)) return text;
  const names = subjectNames(text);
  const additions = [];
  for (const [index, name] of names.entries()) {
    if (!new RegExp(`^\\s*${escape(name)}\\s+is\\b`, 'im').test(definitions)) continue;
    const owned = references.filter(ref => ref.kind === 'image' && ref.owner === name
      && ['Лицо героя', 'Персонаж целиком', 'Костюм'].includes(ref.role));
    if (!owned.length) continue;
    // Every named source must already be explicit in the user's own text.
    if (!owned.every(ref => new RegExp(`^\\s*<Picture ${ref.number}>[^\\n]*\\bfor\\s+${escape(name)}(?=[\\s.,:;!?]|$)`, 'im').test(definitions))) continue;
    const attributes = owned.map(ref => {
      const role = ref.role === 'Костюм' ? 'costume and carried props' : ref.role === 'Лицо героя' ? 'face and identity' : 'full-body appearance and silhouette';
      return `whose ${role} come from <Picture ${ref.number}>`;
    });
    additions.push(`<Subject ${index + 1}> is ${name}, ${attributes.join(' and ')}.`);
  }
  return additions.length ? text.slice(0, offset) + additions.join('\n') + '\n' + text.slice(offset) : text;
}
