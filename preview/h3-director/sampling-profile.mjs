// This setting selects actual ComfyUI sampler and scheduler inputs, never prompt text.
export const samplingProfiles = ['standard', 'motion_ab', 'workflow_euler', 'ref_beta'];

export function samplingProfile(value) {
  if (!samplingProfiles.includes(value)) throw new Error('Неизвестный профиль сэмплинга');
  return value;
}

export function bindSamplingProfile(select, getProject, scheduleDraft) {
  select.addEventListener('change', () => {
    getProject().samplingProfile = samplingProfile(select.value);
    scheduleDraft();
  });
}
