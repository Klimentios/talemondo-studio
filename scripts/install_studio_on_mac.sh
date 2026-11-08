#!/usr/bin/env bash
# H3 Studio on this Mac: Python environment, background service on 127.0.0.1:18187, then «Первый запуск».
# Safe to run again: it updates the environment and restarts the service; data in $H3_MAC_DATA_DIR is kept.
#
#   bash scripts/install_studio_on_mac.sh            # or double-click «Установить Studio.command»
#
# Env: H3_MAC_DATA_DIR (default ~/Library/Application Support/H3 Studio), H3_STUDIO_PORT (default 18187), H3_STUDIO_LABEL,
#      H3_PYTHON (python 3.11+ to use; default: Homebrew python3.12, else python3 on PATH).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${H3_MAC_DATA_DIR:-${HOME}/Library/Application Support/H3 Studio}"
PORT="${H3_STUDIO_PORT:-18187}"
LABEL="${H3_STUDIO_LABEL:-com.minimax.h3-mac-studio}"
LAUNCH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOGS="${HOME}/Library/Logs/H3 Studio"

[[ -f "$ROOT/local_studio/main.py" && -f "$ROOT/requirements.txt" ]] || { echo "Не найдены исходники Studio: $ROOT" >&2; exit 2; }
[[ "$(uname -s)" == Darwin ]] || { echo "Этот установщик — для macOS." >&2; exit 2; }

pick_python() {
  for candidate in "${H3_PYTHON:-}" /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3 "$(command -v python3 || true)"; do
    [[ -n "$candidate" && -x "$candidate" ]] || continue
    if "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then echo "$candidate"; return 0; fi
  done
  return 1
}
PYTHON="$(pick_python)" || { echo "Нужен Python 3.11 или новее: brew install python@3.12 (или запустите «Установить Studio.command»)." >&2; exit 2; }
command -v ffmpeg >/dev/null 2>&1 || echo "Внимание: нет ffmpeg (brew install ffmpeg) — без него не работают вырезки, кадры и монтаж." >&2

mkdir -p "$DATA" "$LOGS" "$(dirname "$LAUNCH")"
chmod 700 "$DATA"
echo "== Окружение Python: $DATA/.venv"
if [[ -x "$DATA/.venv/bin/python" ]] && ! "$DATA/.venv/bin/python" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
  rm -rf "$DATA/.venv"  # an environment from an old Python cannot be upgraded in place
fi
[[ -x "$DATA/.venv/bin/python" ]] || "$PYTHON" -m venv "$DATA/.venv"
"$DATA/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DATA/.venv/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt"

echo "== Служба Studio: $LABEL на порту $PORT"
"$DATA/.venv/bin/python" - "$LAUNCH" "$ROOT" "$DATA" "$LOGS" "$PORT" "$LABEL" <<'PY'
import plistlib, sys
from pathlib import Path
launch, root, data, logs, port, label = sys.argv[1:]
payload = {
    "Label": label,
    "ProgramArguments": ["/bin/sh", "-c",
                         f'cd "{root}" && exec "{data}/.venv/bin/python" -m uvicorn local_studio.main:app '
                         f'--host 127.0.0.1 --port {port} --workers 1'],
    "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
    "StandardOutPath": f"{logs}/service.log", "StandardErrorPath": f"{logs}/service-error.log",
    "EnvironmentVariables": {"H3_MAC_DATA_DIR": data, "H3_STUDIO_SOURCE": root,
                             "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                             "H3_ENABLED_STYLE_ADAPTERS": "style3d,cartoon2d,combat,realism_people,character_swap"},
}
Path(launch).write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML))
Path(launch).chmod(0o600)
PY
launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
for _ in $(seq 1 15); do  # the previous Studio needs a moment to release its port
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
  sleep 1
done
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Порт $PORT занят другой программой. Освободите его или задайте H3_STUDIO_PORT=другой_порт." >&2
  exit 2
fi
launchctl bootstrap "gui/$(id -u)" "$LAUNCH"

for _ in $(seq 1 45); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "INSTALL_OK Studio работает: http://127.0.0.1:${PORT}/"
    [[ -n "${H3_NO_OPEN:-}" ]] || open "http://127.0.0.1:${PORT}/setup.html" 2>/dev/null || true
    exit 0
  fi
  sleep 1
done
echo "Studio не ответила за 45 с. Лог: $LOGS/service-error.log" >&2
exit 1
