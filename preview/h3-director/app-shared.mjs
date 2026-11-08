// Small helpers shared by every part of the main Studio page: DOM lookup, toast, API calls, role lists.
// Split out of app.mjs (the composition root); values several modules write live in app-state.mjs.
import { appState } from './app-state.mjs?v=20261028-split-2';
import { STOCK_CATALOG } from './stock-library.mjs?v=20261005-4';
import { effectiveStyleAdapter } from './model.mjs?v=20261106-refmatch';

let toastTimer;

// `project` is persisted draft data; these maps are session caches/selections.
export const files = new Map();
export const expandedJobDetails = new Set();
export const allAssets = new Map();
export const librarySelected = new Set();

export const $ = (selector) => document.querySelector(selector);
export const libraryDefaults = { image: 'Изображения', video: 'Видео', audio: 'Аудио' };
export const libraryTypes = [['all', 'Все'], ['characters', 'Персонажи'], ['locations', 'Локации'], ['clothes', 'Одежда'], ['props', 'Предметы'], ['motion', 'Движение'], ['styles', 'Стиль'], ['other', 'Другое']];
export const stockByFilename = new Map(STOCK_CATALOG.map((item) => [`${item.id}.jpg`, item]));
export const modeDescriptions = {
  replace: 'Герой берётся из фото. Видео «Движение и камера» даёт действие и ход камеры; это референс, а не покадровая замена.',
  realistic: 'Реалистичный ролик. Видео «Движение» даёт действие и ритм, камера задаётся текстом; лицо героя берётся из фото.',
  animation3d: 'Фиксируем форму персонажа, материал и объёмный стиль во всех сценах.',
  animation2d: 'Держим линию, плоские заливки и внешний вид персонажа от CUT к CUT.',
};
export const imageRoles = ['Авто — определить по содержимому', 'Лицо героя', 'Персонаж целиком', 'Костюм', 'Предмет', 'Стиль', 'Свет', 'Цвет', 'Окружение', 'Кадр сцены', 'Композиция'];
export const videoRoles = ['Авто — определить по содержимому', 'Исходное видео', 'Движение', 'Движение и камера', 'Актёрская игра', 'Камера', 'Ритм сцены', 'Продолжение'];
export const audioRoles = ['Авто — определить по содержимому', 'Голос', 'Музыка', 'Звуковой эффект', 'Атмосфера'];
export const referenceLimits = { image: 9, video: 3, audio: 3 };

export function resolvedProjectStyle() {
  return effectiveStyleAdapter(
    appState.project.mode,
    appState.project.styleAdapter,
    appState.project.references.some((ref) => ref.kind === 'image'),
    appState.project.references.some((ref) => ref.kind === 'video'),
  );
}

export function showToast(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 3300);
}

export async function apiJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = Array.isArray(detail)
      ? detail.map((item) => item?.msg || item?.message || JSON.stringify(item)).join('; ')
      : typeof detail === 'object' && detail ? (detail.message || JSON.stringify(detail)) : detail;
    throw new Error(message || `HTTP ${response.status}`);
  }
  return payload;
}
