const form = document.querySelector("#studioForm");
const prompt = document.querySelector("#prompt");
const duration = document.querySelector("#duration");
const durationOutput = document.querySelector("#durationOutput");
const summary = document.querySelector("#summary");
const generateButton = document.querySelector("#generateButton");
const engineStatus = document.querySelector("#engineStatus");
const resultEmpty = document.querySelector("#resultEmpty");
const resultWorking = document.querySelector("#resultWorking");
const resultReady = document.querySelector("#resultReady");
const resultError = document.querySelector("#resultError");
const jobState = document.querySelector("#jobState");
const resultVideo = document.querySelector("#resultVideo");
const downloadVideo = document.querySelector("#downloadVideo");
const errorText = document.querySelector("#errorText");

const mediaLimits = { images: 9, videos: 3, audios: 3 };
Object.entries(mediaLimits).forEach(([id, limit]) => {
  const input = document.querySelector(`#${id}`);
  const counter = document.querySelector(`#${id}Count`);
  input.addEventListener("change", () => {
    if (input.files.length > limit) {
      input.value = "";
      counter.textContent = `Максимум ${limit}`;
      return;
    }
    counter.textContent = input.files.length ? `Выбрано: ${input.files.length}` : "Не выбрано";
  });
});

function selected(name) {
  return form.elements[name].value;
}

function updateSummary() {
  durationOutput.value = `${duration.value} сек`;
  const camera = selected("camera");
  const lens = `${selected("camera_focal_length_mm")}→${selected("camera_focal_length_end_mm")} mm · f/${selected("camera_aperture_f")}`;
  const focus = `${selected("focus_start_m")}→${selected("focus_end_m")} м`;
  summary.textContent = `${selected("aspect_ratio")} · native · ${selected("profile").toUpperCase()} · ${duration.value} сек${camera === "none" ? "" : ` · ${lens} · фокус ${focus}`}`;
}

function compileCameraState() {
  const motion = selected("camera");
  if (motion === "none") {
    form.elements.camera_state_json.value = "null";
    return;
  }
  const focal = Number(selected("camera_focal_length_mm"));
  const focalEnd = Number(selected("camera_focal_length_end_mm"));
  const aperture = Number(selected("camera_aperture_f"));
  const focusStart = Number(selected("focus_start_m"));
  const focusEnd = Number(selected("focus_end_m"));
  if (![focal, focalEnd, aperture, focusStart, focusEnd].every(Number.isFinite)
      || focal < 12 || focal > 135 || focalEnd < 12 || focalEnd > 135 || aperture < 1.2 || aperture > 22
      || focusStart < 0.3 || focusStart > 50 || focusEnd < 0.3 || focusEnd > 50) {
    throw new Error("Проверьте фокусное расстояние, диафрагму и дистанции фокуса");
  }
  const fov = (length) => 2 * Math.atan(24 / (2 * length)) * 180 / Math.PI;
  const end = Math.max(0, Number(duration.value) - 1 / 24);
  const orbit = { orbit_left: -35, orbit_right: 35 }[motion] || 0;
  const distance = { push_in: 2.8, pull_back: 5.5 }[motion] || 4;
  const key = (t, angle, range, lens, focus) => ({
    t, orbit: angle, elevation: 0, distance: range, target: [0, 1, 0], fov: fov(lens),
    focal_length_mm: lens, aperture_f: aperture, focus_distance_m: focus,
  });
  form.elements.camera_state_json.value = JSON.stringify({ version: 2, keys: [
    key(0, 0, 4, focal, focusStart), key(end, orbit, distance, focalEnd, focusEnd),
  ] });
}

document.querySelectorAll(".segmented").forEach((group) => {
  group.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-value]");
    if (!button) return;
    group.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
    form.elements[group.dataset.field].value = button.dataset.value;
    updateSummary();
  });
});

duration.addEventListener("input", updateSummary);
for (const field of ["camera", "camera_focal_length_mm", "camera_focal_length_end_mm", "camera_aperture_f", "focus_start_m", "focus_end_m"]) {
  form.elements[field].addEventListener("change", updateSummary);
}
document.querySelector("#insertRefs").addEventListener("click", () => {
  const addition = `subject_definitions:
- @ref1: [identity, age, wardrobe, immutable facial features]

summary:
[One clear sentence: subject, action, location, camera, mood]

retention_analysis:
- @ref1: fully_preserved
- @video1: partially_preserved [motion/camera only]
- @audio1: reference [timbre only]

detailed_description:
[Chronological beats. Describe camera path, body mechanics, secondary motion, lighting, shadows, reflections, materials and settling. Dialogue: <d>[Russian] ...</d>]

overall_soundscape:
[Diegetic ambience, Foley, spatial placement, voice]

non_diegetic_music:
none
`;
  const start = prompt.selectionStart;
  prompt.setRangeText(addition, start, prompt.selectionEnd, "end");
  prompt.focus();
});

function showResult(name) {
  [resultEmpty, resultWorking, resultReady, resultError].forEach((item) => item.classList.add("hidden"));
  ({ empty: resultEmpty, working: resultWorking, ready: resultReady, error: resultError })[name].classList.remove("hidden");
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Не удалось проверить задачу");
    if (data.status === "success") {
      const url = `${data.video_url}?t=${Date.now()}`;
      resultVideo.src = url;
      downloadVideo.href = url;
      showResult("ready");
      return;
    }
    if (data.status === "error") throw new Error(data.error || "Ошибка генерации");
    jobState.textContent = data.status === "running" ? "Видео генерируется…" : "Задача в очереди…";
    await new Promise((resolve) => setTimeout(resolve, 3000));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  generateButton.disabled = true;
  showResult("working");
  jobState.textContent = "Загружаю референсы…";
  try {
    const images = document.querySelector("#images").files.length;
    const videos = document.querySelector("#videos").files.length;
    const audios = document.querySelector("#audios").files.length;
    if (!images && !videos) throw new Error("Для Ref2VA загрузите хотя бы одно изображение или видео");
    compileCameraState();
    const response = await fetch("/api/jobs", { method: "POST", body: new FormData(form) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Не удалось запустить генерацию");
    jobState.textContent = "Задача в очереди…";
    await pollJob(data.job_id);
  } catch (error) {
    errorText.textContent = error.message;
    showResult("error");
  } finally {
    generateButton.disabled = false;
  }
});

async function checkHealth() {
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error();
    engineStatus.className = "engine-status ready";
    engineStatus.innerHTML = "<span></span> GPU готов";
  } catch {
    engineStatus.className = "engine-status error";
    engineStatus.innerHTML = "<span></span> GPU недоступен";
  }
}

updateSummary();
checkHealth();
