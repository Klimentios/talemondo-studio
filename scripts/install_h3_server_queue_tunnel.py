"""Install the queue tunnel on Mac without replacing the existing GPU tunnel."""
from pathlib import Path
import plistlib
import subprocess
import os

root = Path(__file__).resolve().parent.parent
script = Path.home() / 'Library/Application Support/MiniMax H3/server-queue-tunnel.sh'
script.parent.mkdir(parents=True, exist_ok=True)
script.write_bytes((root / 'infra/h3_server_queue_tunnel.sh').read_bytes())
script.chmod(0o700)
path = Path.home() / 'Library/LaunchAgents/com.minimax.h3-server-queue-tunnel.plist'
log = str(Path.home() / 'Library/Logs/minimax-h3-server-queue-tunnel.log')
value = {'Label': 'com.minimax.h3-server-queue-tunnel',
         'ProgramArguments': ['/bin/bash', str(script)],
         'RunAtLoad': True, 'KeepAlive': True, 'StandardOutPath': log, 'StandardErrorPath': log}
previous = plistlib.loads(path.read_bytes()) if path.exists() else None
path.write_bytes(plistlib.dumps(value))
service = f'gui/{os.getuid()}/com.minimax.h3-server-queue-tunnel'
if subprocess.run(['launchctl', 'print', service], capture_output=True).returncode:
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)], check=True)
elif previous != value:
    subprocess.run(['launchctl', 'bootout', service], check=True)
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)], check=True)
else:
    subprocess.run(['launchctl', 'kickstart', '-k', service], check=True)
