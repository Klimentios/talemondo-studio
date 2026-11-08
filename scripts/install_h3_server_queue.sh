#!/usr/bin/env bash
# Run on GPU after app/ is copied. Adds intake only; does not restart API/ComfyUI.
set -euo pipefail
root="${H3_ROOT:-/workspace/h3}"
cat > /etc/supervisor/conf.d/h3-server-queue.conf <<CFG
[program:h3-server-queue]
directory=$root/api
command=$root/api-venv/bin/python -m uvicorn app.server_queue_service:app --host 127.0.0.1 --port 18191
environment=H3_SERVER_QUEUE_ENABLED="1",H3_SERVER_QUEUE_DIR="$root/api-data/server-queue"
autostart=true
autorestart=true
startsecs=3
startretries=10
stopwaitsecs=30
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/h3-server-queue.log
stderr_logfile=/var/log/h3-server-queue-error.log
stdout_logfile_maxbytes=20MB
stderr_logfile_maxbytes=20MB
stdout_logfile_backups=2
stderr_logfile_backups=2
CFG
if [[ "${H3_IMAGE_BUILD:-0}" != 1 ]]; then
  supervisorctl reread
  supervisorctl update h3-server-queue
fi
