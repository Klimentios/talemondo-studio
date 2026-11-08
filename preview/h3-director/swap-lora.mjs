// Character Swap LoRA version for hero replacement (local_studio/store.py SWAP_LORAS).
// V1 is the accepted recipe; V1.5 (akatz, 17.10.2026) is an A/B option until a render wins.
export const swapLoras = { v1: 'character_swap', 'v1.5': 'character_swap_v15' };

export function swapLoraSetting(project) {
  // The user picks V1.5 in the form by hand: that choice is the A/B request local_studio/store.py requires.
  return project.mode === 'replace' && project.swapLora === 'v1.5'
    ? { swap_lora: 'v1.5', ab_user_request: 'Studio: пользователь выбрал Character Swap V1.5 в форме' } : {};
}

export function swapLoraAdapter(project) {
  return swapLoras[project.swapLora] || swapLoras.v1;
}

export function bindSwapLora(select, getProject, scheduleDraft) {
  select.addEventListener('change', () => {
    getProject().swapLora = select.value in swapLoras ? select.value : 'v1';
    scheduleDraft();
  });
}
