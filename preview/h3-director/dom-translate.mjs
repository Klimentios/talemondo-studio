// English for every Studio page. The markup, the modules and the server keep writing Russian; in English mode
// this module translates what reaches the page — text, title/placeholder/aria-label/alt, the <title>, and the
// confirm/alert/prompt dialogs — from one dictionary, /i18n/en.json (Russian phrase → English). A text node is
// matched whole first, then phrase by phrase (longest phrase first), so «Сохранена ревизия 4: …» becomes
// "Saved revision 4: …". If any Russian is left after that, the node is the user's own text (a prompt, a scene
// name, a chat message) and stays exactly as written. Inputs, textareas, code and [data-no-i18n] are never touched.
// Loaded by studio-rail.mjs, which every page imports first; switching the language reloads the page.
import { getLocale } from './locale.mjs?v=20261108-i18n-1';

const CYR = /[А-Яа-яЁё]/;
const EDGE = /^[\s·,:;—–\-|/]+|[\s·,:;—–\-|/]+$/g;
const SKIP = 'script,style,textarea,input,select option[data-raw],[contenteditable=""],[contenteditable="true"],[data-no-i18n]';
const CODE = 'code,pre'; // a whole known phrase only (setup hints are shown as <code>); never pieces of real code
const ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
const exact = new Map();
const byWord = new Map();
const firstWord = (text) => (text.match(/^[^\s]+/) || [''])[0].replace(/[.,:;!?)»"”]+$/, '');

export function loadDictionary(dict) {
  for (const [ru, en] of Object.entries(dict)) {
    if (!ru || typeof en !== 'string' || !en) continue;
    exact.set(ru, en);
    const key = ru.replace(EDGE, '');
    if (!key || !CYR.test(key)) continue;
    exact.set(key, en.replace(EDGE, '') || en);
    const word = firstWord(key);
    if (!byWord.has(word)) byWord.set(word, []);
    byWord.get(word).push([key, en.replace(EDGE, '') || en]);
  }
  for (const list of byWord.values()) list.sort((a, b) => b[0].length - a[0].length);
}

// Russian text → English, or null when some Russian would remain (the user's own words).
export function translateText(source) {
  if (!source || !CYR.test(source)) return null;
  // HTML shows a line break inside text as a space; the dictionary keys have single spaces too.
  const text = source.replace(/[ \t]*\n\s*/g, ' ');
  const trimmed = text.trim();
  const whole = exact.get(trimmed) ?? exact.get(trimmed.replace(EDGE, ''));
  if (whole !== undefined) {
    const lead = text.slice(0, text.indexOf(trimmed));
    const tail = text.slice(text.indexOf(trimmed) + trimmed.length);
    if (exact.has(trimmed)) return lead + whole + tail;
    const inner = trimmed.replace(EDGE, '');
    const at = trimmed.indexOf(inner);
    return lead + trimmed.slice(0, at) + whole + trimmed.slice(at + inner.length) + tail;
  }
  let out = '';
  let i = 0;
  while (i < text.length) {
    if (!CYR.test(text[i]) && !(text[i] === '«' && CYR.test(text[i + 1] || ''))) { out += text[i]; i += 1; continue; }
    const start = i > 0 && /[«"(]/.test(text[i - 1]) ? i : i;
    const word = firstWord(text.slice(start));
    let hit = null;
    for (const [ru, en] of byWord.get(word) || []) {
      if (text.startsWith(ru, start)) { hit = [ru, en]; break; }
    }
    if (!hit) {
      // a word glued to punctuation («Сцена», Сцена:) may be indexed without it
      const bare = word.replace(/^[«"(]+/, '');
      for (const [ru, en] of byWord.get(bare) || []) {
        const at = start + word.indexOf(bare);
        if (text.startsWith(ru, at)) { out += text.slice(start, at); hit = [ru, en]; i = at; break; }
      }
    }
    if (!hit) return null;
    out += hit[1];
    i += hit[0].length;
  }
  return CYR.test(out) ? null : out.replace(/«([^«»]*)»/g, '“$1”');
}

function wholeOnly(value) {
  const flat = value.replace(/[ \t]*\n\s*/g, ' ').trim();
  const english = exact.get(flat);
  return english === undefined ? null : value.replace(value.trim(), english);
}

const originals = new WeakMap(); // text node → the Russian it had; element → {attr: Russian}
const ours = new WeakMap();      // text node → the English this module wrote

function textNode(node) {
  const parent = node.parentElement;
  if (!parent || parent.closest(SKIP)) return;
  const value = node.nodeValue;
  if (ours.get(node) === value) return;
  const english = parent.closest(CODE) ? wholeOnly(value) : translateText(value);
  if (english === null) return;
  originals.set(node, value);
  ours.set(node, english);
  node.nodeValue = english;
}

function element(el) {
  if (el.closest(SKIP) && !el.matches('input,textarea,select')) return;
  for (const attr of ATTRS) {
    const value = el.getAttribute(attr);
    if (!value || !CYR.test(value)) continue;
    const english = translateText(value);
    if (english === null) continue;
    const saved = originals.get(el) || {};
    saved[attr] = value;
    originals.set(el, saved);
    el.setAttribute(attr, english);
  }
}

function walk(root) {
  if (root.nodeType === Node.TEXT_NODE) { textNode(root); return; }
  if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
  if (root.nodeType === Node.ELEMENT_NODE) element(root);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeType === Node.TEXT_NODE) textNode(node);
    else element(node);
  }
}

let started = false;
export async function startEnglish(source = '/i18n/en.json?v=20261108-i18n-1') {
  if (started || typeof document === 'undefined') return;
  started = true;
  try {
    loadDictionary(await (await fetch(source)).json());
  } catch { return; } // no dictionary: the page stays Russian rather than half-translated
  walk(document.documentElement);
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === 'characterData') textNode(record.target);
      else if (record.type === 'attributes') element(record.target);
      else record.addedNodes.forEach(walk);
    }
  }).observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  for (const name of ['alert', 'confirm', 'prompt']) {
    const native = window[name].bind(window);
    window[name] = (message, ...rest) => native(typeof message === 'string' ? (translateText(message) ?? message) : message, ...rest);
  }
}

if (typeof document !== 'undefined' && getLocale() === 'en') {
  document.documentElement.lang = 'en';
  const ready = startEnglish();
  window.studioBoot?.hold(ready); // the first paint waits for English, so Russian never flashes
  addEventListener('studio:localechange', () => location.reload());
} else if (typeof document !== 'undefined') {
  addEventListener('studio:localechange', () => location.reload());
}
