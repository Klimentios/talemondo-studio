#!/usr/bin/env bash
# A/B сохранённого задания на GPU-сервере: одно задание, несколько вариантов, отчёт с синхронными роликами.
#   bash scripts/h3_ab.sh --job ID            # задание Studio, через туннель 127.0.0.1:18189
#   bash scripts/h3_ab.sh --sparse            # + вариант BlockSparseAttention
#   bash scripts/h3_ab.sh --job ID --tag T    # другое задание Studio, метка прогона
#   bash scripts/h3_ab.sh report DIR --merge OLD_DIR
# Подробно: docs/guides/H3_AB_HARNESS.md. Результат: logs/ab-<дата>/index.html
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" == "report" || "${1:-}" == "run" ]]; then exec python3 scripts/h3_ab.py "$@"; fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then exec python3 scripts/h3_ab.py run --help; fi
exec python3 scripts/h3_ab.py run "$@"
