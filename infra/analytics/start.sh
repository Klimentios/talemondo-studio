#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
.venv/bin/python infra/analytics/provision.py
exec /opt/homebrew/opt/grafana/bin/grafana server --config "$HOME/Library/Application Support/H3 Analytics Grafana/grafana.ini" --homepath /opt/homebrew/opt/grafana/share/grafana
