// Values that several Studio modules write: the open project and the current selections.
// Read and write them as appState.name; values written by a single module are exported by that module instead.
export const appState = {
  project: undefined,
  mentionState: null,
  selectedResult: null,
  gpuReachable: false,
  selectedCutId: null,
};
