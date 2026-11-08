// «Монтаж» (editor/index.html) embeds the FreeCut editor, a separate optional service (scripts/prepare_freecut.sh,
// tools/freecut-adapter/serve.mjs). A fresh install does not have it: instead of a browser error page inside the
// frame, say so and show how to install it. A reachable server (any answer, even opaque) loads the frame as before.
const frame = document.querySelector('iframe[data-freecut]');
if (frame) {
  const url = frame.dataset.freecut;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 2500);
  const ready = fetch(url, { mode: 'no-cors', signal: controller.signal })
    .then(() => { frame.src = url; })
    .catch(() => {
      const box = document.createElement('section');
      box.className = 'k-panel editor-missing';
      box.innerHTML = `<h2>Монтажный редактор не запущен</h2>
        <p>«Монтаж» открывает FreeCut — отдельный редактор. Установить его один раз и запустить (в папке Studio):</p>
        <pre data-no-i18n>bash scripts/prepare_freecut.sh
node tools/freecut-adapter/serve.mjs</pre>
        <p>Монтажный лист сцен и экспорт в Premiere, DaVinci Resolve и Final Cut работают и без него — раздел
        «Видео студия» → «Сборка».</p>
        <a class="k-btn" href="/?view=scenes">Открыть «Видео студию»</a>`;
      frame.replaceWith(box);
    })
    .finally(() => clearTimeout(timer));
  window.studioBoot?.hold(ready);
}
