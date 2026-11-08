#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
REVISION="770d7220fe79fdf0fdaa84bdce047b90363fc1b5"

if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Создай проектную .venv на Python 3.11+ перед установкой ReShot" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Нужен uv для установки в проектную .venv без системного pip" >&2
  exit 1
fi
uv pip install --python "$VENV/bin/python" "reshot[pose] @ git+https://github.com/maosika-ai/reshot.git@$REVISION"
"$VENV/bin/reshot" --version
