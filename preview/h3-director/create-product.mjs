// The legacy H3 diagnostics remain available, but belong with the other
// professional controls instead of becoming an unplanned fifth workspace.
const controls = document.querySelector('#createControls .controls-content');
const advanced = document.querySelector('#advanced');
if (controls && advanced) controls.append(advanced);

// On CREATE the generation parameters are part of the prompt composer. This
// keeps the full path — references, action, output settings, Generate — in one
// visual unit while retaining the same inputs and event handlers.
const requestedView = new URLSearchParams(location.search).get('view');
if (!requestedView) {
  const prompt = document.querySelector('.prompt-block');
  const generator = document.querySelector('.basic-controls');
  if (prompt && generator) prompt.append(generator);
}
