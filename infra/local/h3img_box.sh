#!/bin/bash
# Runs INSIDE a throwaway build container of infra/h3_image_build.sh (privileged, h3-local:base).
# Mounts the image tree as an overlay and puts it at the paths a server has:
#   /workspace, /.uv, /etc/supervisor/conf.d, /opt/supervisor-scripts  ->  /h3img/<same>
#
#   h3img_box.sh build  BASE   read-write: upper = /img/build (the new image layer)
#   h3img_box.sh verify BASE   read-write: lower = /img/build + BASE, upper = /img/verify (discarded)
#   h3img_box.sh pack   BASE   read-only:  lower = /img/build + BASE
# BASE = the old snapshot tree: /lower (existing h3root volume, mounted read-only — never written)
# or /img/base (a fresh extraction inside the build volume). Then sleeps; work runs via docker exec.
set -euo pipefail
mode="$1" base="$2"
mkdir -p /h3img /img/build /img/work-build /img/verify /img/work-verify
case "$mode" in
  build)  mount -t overlay overlay -o "lowerdir=$base,upperdir=/img/build,workdir=/img/work-build" /h3img ;;
  verify) rm -rf /img/verify /img/work-verify; mkdir -p /img/verify /img/work-verify
          mount -t overlay overlay -o "lowerdir=/img/build:$base,upperdir=/img/verify,workdir=/img/work-verify" /h3img ;;
  pack)   mount -t overlay overlay -o "ro,lowerdir=/img/build:$base" /h3img ;;
  *) echo "mode: build|verify|pack" >&2; exit 2 ;;
esac
for d in workspace .uv etc/supervisor/conf.d opt/supervisor-scripts; do
  mkdir -p "/$d"
  [[ -d "/h3img/$d" ]] || { [[ "$mode" != pack ]] && mkdir -p "/h3img/$d"; }
  mount --bind "/h3img/$d" "/$d"
done
echo "h3img $mode ready"
exec sleep infinity
