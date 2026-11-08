// Read duration before submitting a motion reference to the queue.
export function probeReferenceVideoDuration(source) {
  if (!source) return Promise.resolve(null);
  return new Promise((resolve) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    let settled = false;
    const finish = (duration) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      video.removeAttribute('src');
      video.load();
      resolve(Number.isFinite(duration) && duration > 0 ? duration : null);
    };
    const timer = setTimeout(() => finish(null), 8000);
    video.addEventListener('loadedmetadata', () => finish(video.duration), { once: true });
    video.addEventListener('error', () => finish(null), { once: true });
    video.src = source;
    video.load();
  });
}
