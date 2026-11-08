#!/usr/bin/env bash
# Prepare a plain cloud VM (Nebius; not a Vast container) for infra/h3_up.sh. Idempotent.
#   bash infra/h3_vm_prep.sh --host user@IP [--port 22] [--identity ~/.ssh/id_ed25519]
# Logs in as the VM's sudo user (cloud-init), then via sudo:
#   - root login by the same key only (PermitRootLogin prohibit-password): the installers run as root@;
#   - supervisor (systemd), ffmpeg, aria2, rclone, zstd, git-lfs, sqlite3;
#   - CUDA 12.8 build toolkit next to the image's CUDA 13 (XPose builds against torch cu128). Nebius pins
#     the NVIDIA repo to -1 to protect the driver; one pin file lifts it for *-12-8 packages only and a
#     dry run must show no driver package before anything is installed;
#   - /workspace on the root disk.
# Prints the h3_up.sh command to run next. Touches nothing in /workspace/h3.
set -euo pipefail
host=''; port=22; identity="$HOME/.ssh/id_ed25519"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="${2:?}"; shift 2 ;; --port) port="${2:?}"; shift 2 ;;
    --identity) identity="${2:?}"; shift 2 ;;
    *) sed -n '2,12p' "$0" >&2; exit 2 ;;
  esac
done
[[ "$host" == *@* ]] || { sed -n '2,12p' "$0" >&2; exit 2; }
ip="${host#*@}"
ssh_user=(ssh -i "$identity" -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10
  -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new "$host")

"${ssh_user[@]}" 'sudo -n true' || { echo "Стоп: у $host нет sudo без пароля" >&2; exit 3; }
"${ssh_user[@]}" 'sudo -n bash -s' <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
# A fresh VM runs cloud-init / unattended-upgrades apt first: wait for the dpkg lock instead of failing.
apt_wait="-o DPkg::Lock::Timeout=900"
echo "== root по ключу"
install -d -m 700 /root/.ssh
touch /root/.ssh/authorized_keys; chmod 600 /root/.ssh/authorized_keys
src="/home/${SUDO_USER:?}/.ssh/authorized_keys"
while IFS= read -r key; do
  [[ -n "$key" ]] && ! grep -qxF "$key" /root/.ssh/authorized_keys && printf '%s\n' "$key" >> /root/.ssh/authorized_keys
done < "$src"
echo "PermitRootLogin prohibit-password" > /etc/ssh/sshd_config.d/00-h3-root-key.conf
sshd -t && systemctl reload ssh

echo "== пакеты"
apt-get $apt_wait update -qq 2>/dev/null || apt-get $apt_wait update -qq
apt-get $apt_wait install -y -qq --no-install-recommends supervisor ffmpeg aria2 rclone zstd git-lfs sqlite3 >/dev/null
systemctl enable --now supervisor >/dev/null

echo "== CUDA 12.8 для сборки XPose"
if [[ ! -x /usr/local/cuda-12.8/bin/nvcc ]]; then
  cat > /etc/apt/preferences.d/50-h3-cuda-12-8 <<'PIN'
Package: /-12-8$/ cuda-toolkit-12-config-common cuda-toolkit-12-8-config-common cuda-toolkit-config-common
Pin: release o=NVIDIA
Pin-Priority: 500
PIN
  pkgs="cuda-minimal-build-12-8 cuda-libraries-dev-12-8"
  plan="$(apt-get $apt_wait install -s -y --no-install-recommends $pkgs 2>&1)"
  if grep -qE '^E:' <<<"$plan"; then echo "Стоп: apt не может поставить CUDA 12.8:" >&2; grep -E '^E:| : Depends' <<<"$plan" >&2; exit 4; fi
  other="$(awk '/^(Inst|Remv)/ {print $1, $2}' <<<"$plan" | grep -vE -- '^Inst (.*-12-8|cuda-toolkit(-12)?(-8)?-config-common)$' || true)"
  [[ -z "$other" ]] || { echo "Стоп: установка CUDA 12.8 затронула бы другие пакеты:" >&2; echo "$other" >&2; exit 4; }
  apt-get $apt_wait install -y -qq --no-install-recommends $pkgs >/dev/null
fi
/usr/local/cuda-12.8/bin/nvcc --version | tail -1

mkdir -p /workspace
echo "== итог"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
free -g | awk '/Mem:/ {print "RAM " $2 " ГБ"}'
df -h / | awk 'NR==2 {print "диск " $2 ", свободно " $4}'
REMOTE

ssh -i "$identity" -p "$port" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 "root@$ip" true \
  || { echo "Стоп: root@$ip по ключу не пускает" >&2; exit 3; }
echo
echo "VM готова. Дальше:"
echo "  bash infra/h3_up.sh --host root@$ip --port $port --identity $identity --stamp LATEST"
