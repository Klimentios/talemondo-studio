#!/usr/bin/env python3
"""Install only selected, pinned optional H3 assets on the GPU. Default is a download plan.

Example (on GPU): python scripts/h3_install_render_assets.py --root /workspace/h3 \
    --profiles int8_full --adapters orb360,wushu_r2v_v7 --apply
Uses the existing private HF curl config; never modifies BF16 weights or restarts services.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import urllib.request

_BASE = Path(__file__).resolve().parents[1]
ASSET_PATH = _BASE / 'app' / 'render_assets.json'
if not ASSET_PATH.is_file():
    ASSET_PATH = _BASE / 'api' / 'app' / 'render_assets.json'


def selection(manifest: dict, profiles: str, adapters: str) -> list[tuple[str, dict]]:
    selected = []
    for group, values, category in [('profiles',profiles,'diffusion_models'), ('adapters',adapters,'loras')]:
        for name in dict.fromkeys(filter(None,(s.strip() for s in values.split(',')))):
            if name not in manifest[group]:
                raise ValueError(f'Unknown {group}: {name}')
            if group == 'profiles' and name == 'bf16':
                raise ValueError('Base BF16 is installed by the normal Studio installer, not this helper')
            asset = manifest[group][name]
            if (len(asset.get('revision','')) != 40 or len(asset.get('sha256','')) != 64
                    or Path(asset['filename']).name != asset['filename']):
                raise ValueError(f'Invalid immutable asset pin: {name}')
            selected.append((category,asset))
    if not selected:
        raise ValueError('Select --profiles and/or --adapters')
    return selected


def digest(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle,'sha256').hexdigest()


def ensure_idle(url: str) -> None:
    with urllib.request.urlopen(url.rstrip('/')+'/queue',timeout=15) as response:
        queue = json.load(response)
    if not isinstance(queue,dict) or 'queue_running' not in queue or 'queue_pending' not in queue:
        raise ValueError('ComfyUI queue response is unknown; installation refused')
    if queue['queue_running'] or queue['queue_pending']:
        raise ValueError('ComfyUI is busy; install selected assets after the existing queue finishes')


def install_one(root: Path, category: str, asset: dict, config: Path, comfy_url: str) -> None:
    target = root/'ComfyUI'/'models'/category/asset['filename']
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.stat().st_size == asset['bytes'] and digest(target) == asset['sha256']:
            print('VERIFIED existing:',asset['filename'],flush=True)
            return
        raise ValueError(f'Existing file differs; preserving it: {target}')
    part = target.with_name(target.name+'.part')
    url = f"https://huggingface.co/{asset['repo']}/resolve/{asset['revision']}/{asset['path']}"
    print('Downloading:',asset['filename'],flush=True)
    result = subprocess.run(['curl','--config',str(config),'--fail','--location','--retry','5',
        '--retry-delay','5','--continue-at','-','--silent','--show-error','--output',str(part),url],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=False)
    if result.returncode:
        raise ValueError(f'Download failed (curl {result.returncode}); .part preserved for resume')
    if part.stat().st_size != asset['bytes'] or digest(part) != asset['sha256']:
        raise ValueError(f'Integrity check failed; not installed: {asset["filename"]}')
    ensure_idle(comfy_url)
    part.replace(target)
    print('INSTALLED SHA256 verified:',asset['filename'],flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('/workspace/h3'))
    parser.add_argument('--profiles',default='')
    parser.add_argument('--adapters',default='')
    parser.add_argument('--hf-config',type=Path,default=Path('/root/.cache/h3-hf-curl.conf'))
    parser.add_argument('--comfy-url',default='http://127.0.0.1:18188')
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    assets = selection(json.loads(ASSET_PATH.read_text()),args.profiles,args.adapters)
    print(json.dumps({'apply':args.apply,'assets':[{'category':cat,'filename':a['filename'],
        'bytes':a['bytes'],'revision':a['revision'],'sha256':a['sha256']} for cat,a in assets],
        'total_GB':round(sum(a['bytes'] for _,a in assets)/1e9,3)},ensure_ascii=False,indent=2),flush=True)
    if not args.apply:
        return
    root = args.root.resolve()
    if not (root/'ComfyUI'/'models').is_dir():
        raise ValueError('Select an existing GPU installation; no fresh server provisioning here')
    if (not args.hf_config.is_file() or stat.S_IMODE(args.hf_config.stat().st_mode) != 0o600
            or args.hf_config.stat().st_uid != os.getuid()):
        raise ValueError('Private owned HF curl config with mode 600 is required; no anonymous fallback')
    # Serialize all optional installers; a second writer must not append to the same .part.
    with (root/'.render-assets.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ensure_idle(args.comfy_url)
        needed = sum(max(0,a['bytes'] - (root/'ComfyUI'/'models'/cat/(a['filename']+'.part')).stat().st_size)
                     if (root/'ComfyUI'/'models'/cat/(a['filename']+'.part')).exists() else a['bytes']
                     for cat,a in assets if not (root/'ComfyUI'/'models'/cat/a['filename']).exists())
        if shutil.disk_usage(root).free < needed + 5*1024**3:
            raise ValueError('Not enough free disk for selected assets plus 5 GiB reserve')
        for category,asset in assets:
            install_one(root,category,asset,args.hf_config,args.comfy_url)
    print('DONE. Existing queue/defaults/services unchanged. Refresh GPU capabilities; inference QC pending.')


if __name__ == '__main__':
    try:
        main()
    except (ValueError,OSError) as exc:
        raise SystemExit(str(exc))
