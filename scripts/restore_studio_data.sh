#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$HERE/Перенос данных Studio"
DEST="${HOME}/Library/Application Support/H3 Studio"
if [[ ! -d "$SOURCE" ]]; then echo "Рядом с комплектом нет каталога «Перенос данных Studio»." >&2; exit 2; fi
if ! curl -fsS --max-time 3 http://127.0.0.1:18187/api/health >/dev/null; then echo "Перед переносом данных установите и запустите Studio." >&2; exit 2; fi
for endpoint in /api/jobs /api/audio/jobs /api/upscale/jobs; do
  if ! python3 - "$endpoint" <<'PY'
import json,sys,urllib.request
try:
 d=json.load(urllib.request.urlopen('http://127.0.0.1:18187'+sys.argv[1],timeout=5))
 active=[j for j in d if j.get('state') in {'queued_local','uploading','queued_gpu','running','downloading','needs_reconciliation','stopping'}]
 if active: raise SystemExit(1)
except Exception: raise SystemExit(1)
PY
  then
    echo "Очередь не пуста или недоступна ($endpoint); данные не менялись." >&2
    exit 2
  fi
done
BACKUP="$DEST/before-transfer-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST" "$BACKUP"
chmod 700 "$DEST" "$BACKUP"
for name in assets results editor_exports editor_projects model-staging code-backups posters storyboards studio.sqlite3 jobs.sqlite3 compute.sqlite3 context-ir.sqlite3 franchises.sqlite3 analytics.sqlite3 draft.json; do
  [[ ! -e "$DEST/$name" ]] || mv "$DEST/$name" "$BACKUP/"
  [[ ! -e "$SOURCE/$name" ]] || cp -R "$SOURCE/$name" "$DEST/$name"
done
"$DEST/.venv/bin/python" - "$DEST" <<'PY'
import sqlite3,sys
from pathlib import Path
for p in Path(sys.argv[1]).glob('*.sqlite3'):
 with sqlite3.connect(f'file:{p}?mode=ro',uri=True) as db:
  result=db.execute('PRAGMA integrity_check').fetchone()[0]
  if result!='ok': raise SystemExit(f'SQLite integrity_check failed: {p.name}: {result}')
PY
echo "RESTORE_OK Данные скопированы. Предыдущая версия сохранена в: $BACKUP"
