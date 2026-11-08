#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Keep the bundle clean: bytecode goes to a temp dir, not into __pycache__ here.
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/h3-pycache" PYTHONDONTWRITEBYTECODE=1

fail() { echo "RELEASE CHECK FAILED: $*" >&2; exit 2; }

# 1. Required components (the whole chain: Mac Studio -> GPU API -> ComfyUI/Qwen).
for required in \
  requirements.txt app/main.py app/director.py app/graph.py app/comfy_client.py app/contract.py \
  app/job_registry.py app/camera_optics.py app/color_grade.py app/director_config.py \
  app/director_schema.json app/camera_guide_vendor/camera.py app/camera_guide_vendor/camera_prompt.py \
  app/camera_guide_vendor/nodes.py app/comfy_nodes/h3_color_grade/__init__.py \
  app/comfy_nodes/h3_color_grade/grade.py app/comfy_nodes/h3_color_grade/film.py \
  local_studio/main.py local_studio/store.py local_studio/worker.py \
  preview/h3-director/index.html preview/h3-director/app.mjs preview/h3-director/model.mjs \
  preview/h3-director/scene-queue.mjs preview/h3-director/styles.css \
  scripts/deploy_h3_new_server.sh scripts/install_h3_h200_overlay.sh \
  scripts/install_h3_qwen_director.sh scripts/run_h3_qwen_director.sh scripts/h3_runtime_watchdog.py \
  scripts/install_h3_scenarist_next.sh scripts/h3_scenarist_switch.sh scripts/h3_scenarist_conf.sh \
  deploy/scenarist-models/qwen3.8-27b-fp8.sha256 deploy/scenarist-models/qwen3.6-27b-fp8.sha256 \
  deploy/h3-qwen-director.conf deploy/h3-watchdog.conf \
  vendor/minimax-h3-local-studio/scripts/deploy_h3_local_studio.sh \
  vendor/minimax-h3-local-studio/scripts/deploy_remote_h3_local_studio.sh \
  vendor/minimax-h3-local-studio/scripts/h3_gpu_env.sh; do
  [[ -s "$required" ]] || fail "missing release component: $required"
done

# 2. Shell syntax for every script in the bundle.
while IFS= read -r -d '' script; do
  bash -n "$script" || fail "bash syntax: $script"
done < <(find scripts deploy vendor -name '*.sh' -not -path '*/legacy/*' -print0)
if command -v shellcheck >/dev/null; then
  find scripts vendor -name '*.sh' -not -path '*/legacy/*' -print0 |
    xargs -0 shellcheck -S error -x || fail "shellcheck errors"
fi

# 3. Python byte-compiles (server scripts are not covered by unit tests).
python3 -m py_compile app/*.py local_studio/*.py scripts/*.py || fail "python syntax"

# 4. Supervisor configs parse and reference the expected ports/commands.
python3 - <<'PY' || fail "supervisor configs"
import configparser, pathlib
for path in ("deploy/h3-qwen-director.conf", "deploy/h3-watchdog.conf"):
    parser = configparser.RawConfigParser()
    parser.read(path)
    section = next(s for s in parser.sections() if s.startswith("program:"))
    assert parser.get(section, "command"), path
watchdog = pathlib.Path("deploy/h3-watchdog.conf").read_text()
assert "18188" in watchdog and "18189" in watchdog and "8000" in watchdog
PY

# 5. Browser modules: every named import must be exported (a missing export
#    leaves the whole Studio page blank; unit tests do not load app.mjs).
for module in preview/h3-director/app.mjs preview/h3-director/app-*.mjs; do node --check "$module"; done
node --input-type=module - <<'JS' || fail "Studio module imports"
import { readFileSync, readdirSync } from 'node:fs';
const dir = 'preview/h3-director/';
// Read exports from the text: the browser modules touch window/document on load.
const exportsOf = (file) => {
  const text = readFileSync(dir + file, 'utf8');
  const names = new Set([...text.matchAll(/export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([\w$]+)/g)].map((m) => m[1]));
  for (const m of text.matchAll(/export\s*\{([^}]+)\}/g)) m[1].split(',').forEach((n) => names.add(n.trim().split(/\s+as\s+/).pop()));
  return names;
};
// app.mjs and its parts app-*.mjs
const source = readdirSync(dir).filter((name) => /^app(-[a-z]+)?\.mjs$/.test(name)).map((name) => readFileSync(dir + name, 'utf8')).join('\n');
for (const match of source.matchAll(/import\s*\{([^}]+)\}\s*from\s*'\.\/([^'?]+)(?:\?[^']*)?'/g)) {
  const exported = exportsOf(match[2]);
  const names = match[1].split(',').map((name) => name.trim().split(/\s+as\s+/)[0]).filter(Boolean);
  const missing = names.filter((name) => !exported.has(name));
  if (missing.length) throw new Error(`${match[2]} does not export: ${missing.join(', ')}`);
}
console.log('Studio module imports resolve');
JS
node --test preview/h3-director/model.test.mjs preview/h3-director/scene-queue.test.mjs
for test in tests/*.mjs; do [[ -e "$test" ]] && node "$test"; done

# 6. Python tests. API tests silently skip without fastapi/httpx: refuse that.
python3 -c 'import fastapi, httpx' 2>/dev/null ||
  fail "install test deps first: python3 -m pip install -r requirements.txt"
python3 -m unittest discover -s tests -q

# 7. Bundle checksums, when present.
if [[ -f SHA256SUMS ]]; then
  if command -v sha256sum >/dev/null; then sha256sum -c --quiet SHA256SUMS || fail "SHA256SUMS mismatch"
  else shasum -a 256 -c --quiet SHA256SUMS || fail "SHA256SUMS mismatch"; fi
fi

if [[ "${1:-}" == '--live' ]]; then
  python3 - <<'PY'
import json, urllib.request
from collections import Counter
for port, path in [(18187, '/api/queue'), (18189, '/api/health'), (18188, '/system_stats')]:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=10) as r:
        data = json.load(r)
    print(port, path, 'OK', data if path == '/api/queue' else '')
with urllib.request.urlopen('http://127.0.0.1:18187/api/jobs', timeout=10) as r:
    jobs = json.load(r)
print('Job states:', dict(Counter(j['state'] for j in jobs)))
PY
fi
printf '%s\n' 'Technical checks passed. Visual/audio acceptance still requires a reviewed real MP4.'
