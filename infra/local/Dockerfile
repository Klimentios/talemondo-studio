# Local H3 stand: the server software snapshot on the same OS, without GPU and without weights.
# The snapshot itself lives in the docker volume "h3root" (see infra/h3_local.sh); this image only
# supplies the OS layer the snapshot expects (vastai/base-image:cuda-12.8.1-auto is Ubuntu 24.04).
FROM --platform=linux/amd64 ubuntu:24.04
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      python3 python3-venv ffmpeg supervisor curl ca-certificates git zstd libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*
# The 2026-10-04 snapshot's venv is --system-site-packages and relies on ~100 packages of the Vast base
# image's system Python; install the same versions (no deps: the lock is complete). Servers deployed
# after that get a self-contained venv (vendor/minimax-h3-local-studio/assets/comfy-venv-requirements.txt)
# and do not need this layer.
COPY system-requirements.txt /tmp/system-requirements.txt
RUN python3 -m venv /opt/pip && /opt/pip/bin/pip install -q --no-deps --disable-pip-version-check \
      --target /usr/local/lib/python3.12/dist-packages -r /tmp/system-requirements.txt \
 && rm -rf /opt/pip /root/.cache
