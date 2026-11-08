// Small UI fixes shared by every Studio page (loaded by studio-rail.mjs).
// Native <input type=file> shows «Choose File / No file chosen» in the browser's language and style;
// wrap each one in a Studio button with a Russian label and the chosen file name.
function pickLabel(input) {
  const files = [...(input.files || [])];
  if (!files.length) return input.multiple ? 'Файлы не выбраны' : 'Файл не выбран';
  return files.length === 1 ? files[0].name : `Выбрано файлов: ${files.length}`;
}

function enhance(input) {
  if (input.dataset.studioFile || input.hidden || input.closest('.studio-file, .library-file-drop, .refine-upload')) return;
  // Inputs already hidden behind a styled label (opacity 0, display none) keep their own look.
  const style = getComputedStyle(input);
  if (style.display === 'none' || Number(style.opacity) === 0) return;
  input.dataset.studioFile = '1';
  const wrap = document.createElement('span');
  wrap.className = 'studio-file';
  const button = document.createElement('span');
  button.className = 'studio-file-button';
  button.textContent = input.multiple ? 'Выбрать файлы' : 'Выбрать файл';
  const name = document.createElement('span');
  name.className = 'studio-file-name';
  name.textContent = pickLabel(input);
  input.replaceWith(wrap);
  wrap.append(input, button, name);
  input.addEventListener('change', () => { name.textContent = pickLabel(input); });
}

function scan(root = document) {
  root.querySelectorAll?.('input[type=file]').forEach(enhance);
}

const style = document.createElement('style');
style.textContent = `
.studio-file { position: relative; display: flex !important; align-items: center; gap: 10px; min-width: 0; max-width: 100%; min-height: 40px; padding: 5px 12px 5px 5px; box-sizing: border-box; border: 1px solid #2c333b; border-radius: 9px; background: #191e24; cursor: pointer; }
.studio-file:hover { border-color: #4a545d; }
.studio-file:focus-within { outline: 2px solid #d9ff39; outline-offset: 2px; }
.studio-file input[type=file] { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }
.studio-file-button { flex: none; padding: 6px 12px; border-radius: 7px; background: #2a3238; color: #f2f3ef; font-size: 12.5px; font-weight: 600; }
.studio-file-name { min-width: 0; overflow: hidden; color: #9aa1a7; font-size: 12.5px; text-overflow: ellipsis; white-space: nowrap; }`;
document.head.append(style);

scan();
new MutationObserver((records) => {
  for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) scan(node.matches?.('input[type=file]') ? node.parentNode : node);
}).observe(document.body, { childList: true, subtree: true });
