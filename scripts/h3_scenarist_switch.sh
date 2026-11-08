#!/usr/bin/env bash
# Switch the active Scenarist/Director model on THIS GPU server, without reinstalling.
#   bash /workspace/h3/scripts/h3_scenarist_switch.sh --profile default|qwen3.8-27b-fp8|qwen3.6-27b-fp8 [--force]
# From the Mac: bash scripts/h3_scenarist_profile.sh --host root@HOST --port PORT --profile ...
#
# Only when ComfyUI's queue is empty and no plan is in flight (holds the planning lock, so no job
# can start meanwhile). Rewrites the h3-qwen-director and h3-api environments, restarts
# h3-qwen-director, waits for /health, runs a short request plus two sleep/wake cycles and leaves
# it asleep. Any failure restores both configs and restarts the previous profile.
# Env: H3_QWEN_GPU_UTIL (27B: default 0.45), H3_QWEN_SLEEP_LEVEL (1; 2 = reload from disk on wake).
set -euo pipefail
profile=''; force=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) profile="${2:?}"; shift 2 ;;
    --force) force=1; shift ;;
    *) sed -n '2,10p' "$0" >&2; exit 2 ;;
  esac
done
root="${H3_ROOT:-/workspace/h3}"
bundle="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
template="$bundle/deploy/h3-qwen-director.conf"
render_conf="$bundle/scripts/h3_scenarist_conf.sh"
service=/etc/supervisor/conf.d/h3-qwen-director.conf
api=/etc/supervisor/conf.d/h3-api.conf
util="${H3_QWEN_GPU_UTIL:-0.45}"
level="${H3_QWEN_SLEEP_LEVEL:-1}"
case "$profile" in
  default) model=Qwen/Qwen3-VL-8B-Instruct; venv="$root/qwen-venv"; snapshot='' ;;
  qwen3.8-27b-fp8) model=Qwen/Qwen3.8-27B-FP8; venv="$root/qwen-next-venv"
    snapshot="$root/hf-cache/hub/models--Qwen--Qwen3.8-27B-FP8/snapshots/017b9c7af6b5689d5dd426a76e0bc077eb5ca20a" ;;
  qwen3.6-27b-fp8) model=Qwen/Qwen3.6-27B-FP8; venv="$root/qwen-next-venv"
    snapshot="$root/hf-cache/hub/models--Qwen--Qwen3.6-27B-FP8/snapshots/e89b16ebf1988b3d6befa7de50abc2d76f26eb09" ;;
  *) echo "--profile default|qwen3.8-27b-fp8|qwen3.6-27b-fp8" >&2; exit 2 ;;
esac
[[ -f "$template" && -x "$render_conf" && -f "$service" ]] || { echo "Missing $template, $render_conf or $service" >&2; exit 2; }
[[ -x "$venv/bin/vllm" ]] || { echo "$venv has no vLLM: install it first (install_h3_scenarist_next.sh)" >&2; exit 2; }
[[ -z "$snapshot" || -f "$snapshot/model.safetensors.index.json" ]] || {
  echo "Pinned snapshot missing: $snapshot (install_h3_scenarist_next.sh; H3_SCENARIST_27B=0 skipped it?)" >&2; exit 2;
}
if [[ "$profile" != default ]]; then
  total_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d ' ')"
  python3 - "$total_mib" "$util" <<'PY'
import sys
total_gb, util = int(sys.argv[1]) / 1024, float(sys.argv[2])
if total_gb * util < 40:
    raise SystemExit(f"H3_QWEN_GPU_UTIL={util}: {total_gb * util:.0f} of {total_gb:.0f} GB, the 27B profile needs ~40 GB "
                     f"(H3_QWEN_GPU_UTIL>={40 / total_gb:.2f}) — keep the default profile")
if util > 0.6:
    raise SystemExit(f"H3_QWEN_GPU_UTIL={util} leaves too little for the next H3 render")
PY
fi

# No plan in flight and no render submission while we switch (app/qwen_lifecycle.py locks).
exec 9>>/tmp/h3-qwen-plan.lock
flock -n 9 || { echo "A Director/Scenarist request is running; retry in a minute" >&2; exit 7; }
if [[ $force != 1 ]]; then
  python3 - <<'PY' || { echo "ComfyUI queue is busy: switch after the render (or --force)" >&2; exit 7; }
import json, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:18188/queue", timeout=10) as response:
    queue = json.load(response)
sys.exit(1 if queue.get("queue_running") or queue.get("queue_pending") else 0)
PY
fi

backup="$root/backup/scenarist-switch-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
cp -a "$service" "$backup/"
[[ ! -f "$api" ]] || cp -a "$api" "$backup/"
previous="$( { grep -o 'H3_SCENARIST_PROFILE="[^"]*"' "$service" || true; } | cut -d'"' -f2)"
echo "Scenarist: ${previous:-default} -> $profile ($model); backup $backup"

apply_profile() {  # every step checked: called in an `if`, where set -e does not apply
  bash "$render_conf" "$profile" "$root" "$template" "$util" "$level" > "$service.new" || return 1
  grep -q '^\[program:h3-qwen-director\]' "$service.new" || return 1
  mv "$service.new" "$service" || return 1
  if [[ -f "$api" ]]; then
    sed -i -e 's/,H3_SCENARIST_PROFILE="[^"]*"//' -e 's/,H3_QWEN_SLEEP_LEVEL="[^"]*"//' \
           -e 's/,H3_QWEN_START_TIMEOUT="[^"]*"//' "$api" || return 1
    # The GPU API sends the profile's request extras (enable_thinking false) and waits longer
    # for a 27B first start. Default: the keys are absent, exactly as the overlay writes h3-api.conf.
    if [[ "$profile" != default ]]; then
      sed -i "s|^environment=\(.*\)$|environment=\1,H3_SCENARIST_PROFILE=\"$profile\",H3_QWEN_SLEEP_LEVEL=\"$level\",H3_QWEN_START_TIMEOUT=\"900\"|" "$api" || return 1
    fi
  fi
  supervisorctl reread || return 1
  supervisorctl update || return 1  # restarts h3-api when its environment changed (queue is empty)
  supervisorctl restart h3-qwen-director || return 1
}

smoke() {  # $1 model, $2 thinking kwargs (1/0), $3 sleep level, $4 load timeout
  python3 - "$@" <<'PY'
import json, subprocess, sys, time, urllib.request
model, no_thinking, level, load_timeout = sys.argv[1], sys.argv[2] == "1", int(sys.argv[3]), int(sys.argv[4])
root = "http://127.0.0.1:8000"
def call(path, payload=None, timeout=60):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(root + path, data=data, headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body.strip() else None
def sleeping():
    value = call("/is_sleeping")
    return value if isinstance(value, bool) else value["is_sleeping"]
def wait(condition, seconds, what):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(3)
    raise SystemExit(f"{what} timed out; see /var/log/h3-qwen-director.err.log")
def wake():
    if level == 2:
        call("/wake_up?tags=weights", {})
        call("/collective_rpc", {"method": "reload_weights"}, timeout=600)
        call("/wake_up?tags=kv_cache", {})
    else:
        call("/wake_up", {})
    wait(lambda: not sleeping(), 180, "wake_up")
def ask():
    body = {"model": model, "temperature": 0, "max_tokens": 40,
            "messages": [{"role": "user", "content": "Ответь одним словом: столица Франции?"}]}
    if no_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    content = (call("/v1/chat/completions", body, timeout=180)["choices"][0]["message"].get("content") or "").strip()
    if not content or "<think>" in content or "</think>" in content:
        raise SystemExit(f"Unexpected answer (thinking not disabled?): {content!r}")
    return content
def healthy():
    urllib.request.urlopen(root + "/health", timeout=5).close()
    return any(m.get("id") == model for m in call("/v1/models").get("data", []))
wait(healthy, load_timeout, "Model load (/health + /v1/models)")
if sleeping():
    wake()
first = ask()
for cycle in range(2):  # the hybrid-model sleep/wake path (vLLM issue #48310)
    call(f"/sleep?level={level}", {})
    wait(sleeping, 180, "sleep")
    wake()
    again = ask()
    if again != first:
        raise SystemExit(f"Answer changed after sleep/wake cycle {cycle + 1}: {first!r} -> {again!r}")
call(f"/sleep?level={level}", {})
wait(sleeping, 180, "sleep")
used = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                      capture_output=True, text=True).stdout.strip()
print(f"OK {model}: answer {first!r}, stable over 2 sleep/wake cycles (level {level}); asleep, GPU used {used} MiB")
PY
}

no_thinking=1; timeout=1500
[[ "$profile" != default ]] || { no_thinking=0; timeout=600; level=1; }
if apply_profile && smoke "$model" "$no_thinking" "$level" "$timeout"; then
  echo "Scenarist profile $profile active. /api/capabilities -> scenarist_v1 shows it."
  exit 0
fi
echo "Switch to $profile FAILED — restoring ${previous:-default} from $backup" >&2
cp -a "$backup/$(basename "$service")" "$service"
[[ ! -f "$backup/$(basename "$api")" ]] || cp -a "$backup/$(basename "$api")" "$api"
supervisorctl reread || true
supervisorctl update || true
supervisorctl restart h3-qwen-director || true
case "${previous:-default}" in
  default) smoke Qwen/Qwen3-VL-8B-Instruct 0 1 600 || echo "Previous profile did not come back: check supervisorctl status" >&2 ;;
  *) echo "Previous profile ${previous} restarted; check /var/log/h3-qwen-director.err.log" >&2 ;;
esac
exit 1
