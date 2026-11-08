#!/usr/bin/env bash
# Подключает навыки из этой папки в ~/.codex/skills и ~/.claude/skills симлинками.
# Существующую не-симлинк папку с тем же именем переносит в Корзину (macOS `trash`).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
for home_dir in "$HOME/.codex/skills" "$HOME/.claude/skills"; do
  mkdir -p "$home_dir"
  for skill in "$here"/*/; do
    name="$(basename "$skill")"
    target="$home_dir/$name"
    if [ -L "$target" ]; then rm "$target"; elif [ -e "$target" ]; then trash "$target"; fi
    ln -s "${skill%/}" "$target"
    echo "$target -> ${skill%/}"
  done
done
