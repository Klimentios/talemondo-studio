// The library uses the existing asset handlers in app.mjs in both contexts.
export const assetsWorkspace = new URLSearchParams(window.location.search).get('view') === 'assets';

if (assetsWorkspace) {
  document.body.classList.add('assets-view');
  document.title = 'TaleMondo — Библиотека';
  const navigation = document.querySelector('#headerLibrary');
  navigation?.classList.add('active');
  navigation?.setAttribute('aria-current', 'page');
  const dialog = document.querySelector('#libraryDialog');
  if (dialog && !dialog.open) dialog.show();
  const title = document.querySelector('#libraryTitle');
  if (title) title.textContent = 'Библиотека';
  const description = document.querySelector('.library-header p');
  if (description) description.textContent = 'Персонажи, локации, исходные файлы и готовые материалы. Всё хранится на Mac.';
  const back = document.querySelector('#closeLibrary');
  if (back) {
    back.textContent = 'К созданию';
    back.setAttribute('aria-label', 'Вернуться к созданию видео');
  }
  const grid = document.querySelector('#libraryGrid');
  if (grid) grid.textContent = 'Загружаем файлы с Mac…';
}

export function showAssetLibrary(dialog) {
  if (dialog.open) return;
  if (assetsWorkspace) dialog.show();
  else dialog.showModal();
}

export function closeAssetLibrary(dialog) {
  if (assetsWorkspace) window.location.assign('/');
  else dialog.close();
}
