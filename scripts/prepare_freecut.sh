#!/bin/bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
source_dir="$repo_root/tools/freecut-upstream"
revision=5ceeeb388eef5423640b7db157706274bf1b8165
if [ ! -d "$source_dir" ]; then
 git clone https://github.com/walterlow/freecut.git "$source_dir"
 git -C "$source_dir" checkout "$revision"
fi
if [ "$(git -C "$source_dir" rev-parse HEAD)" != "$revision" ]; then
 echo 'FreeCut revision differs; inspect checkout before preparing.' >&2
 exit 1
fi
python3 "$repo_root/tools/freecut-adapter/prepare.py"
cd "$source_dir"
npm ci --no-audit --no-fund
npm run build
