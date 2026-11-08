#!/bin/bash
# Separate intake tunnel follows the current main tunnel endpoint on each reconnect.
set -u
while true; do
  endpoint=$(/usr/bin/python3 - <<'PY'
import pathlib,re
p=pathlib.Path.home()/'Library/Application Support/MiniMax H3/tunnel.sh'
s=p.read_text()
h=re.search(r'^SERVER_HOST="([A-Za-z0-9.-]+)"$',s,re.M)
n=re.search(r'^SERVER_PORT="(\d+)"$',s,re.M)
k=re.search(r'^SSH_KEY="([^"\n]+)"$',s,re.M)
if h and n and k: print(h[1],n[1],k[1],sep='\t')
PY
  )
  IFS=$'\t' read -r server_host server_port server_key <<< "$endpoint"
  if [[ -n "$server_host" && -n "$server_port" ]]; then
    /usr/bin/ssh -N -i "$server_key" \
      -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes \
      -o "UserKnownHostsFile=$HOME/.ssh/known_hosts_h3_${server_host}_${server_port}" \
      -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
      -o ConnectTimeout=8 -L 127.0.0.1:18191:127.0.0.1:18191 \
      -p "$server_port" "root@$server_host"
  fi
  sleep 5
done
