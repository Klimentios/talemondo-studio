#!/usr/bin/env python3
"""Build a local plugin marketplace for the existing Studio MCP server; optionally install via official CLI."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path


def build(repo: Path, root: Path):
    plugin = root / 'plugins' / 'h3-image-companion'
    plugin.mkdir(parents=True, exist_ok=True)
    manifests = {
        plugin / 'plugin.json': {
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json',
            'name': 'h3-image-companion', 'version': '0.1.0',
            'description': 'Launch a ChatGPT conversation for a Studio image request and return its generated file.',
            'extensions': {'com.openai': {'requires_local_executor': True, 'interface': {
                'displayName': 'STUDIO Image Companion', 'shortDescription': 'Картинки ChatGPT для сцен Studio',
                'developerName': 'STUDIO', 'category': 'Productivity', 'capabilities': ['Read', 'Write']}}}},
        plugin / 'mcp.json': {
            '$schema': 'https://agent-plugins.org/schemas/1.0.0/mcp.schema.json',
            'mcpServers': {'images': {'type': 'stdio', 'command': 'python3',
                'args': [str(repo / 'scripts' / 'h3_studio_mcp.py')],
                'env': {'H3_IMAGE_COMPANION_ONLY': '1', 'H3_STUDIO_URL': 'http://127.0.0.1:18187'}}}},
        root / '.agents' / 'plugins' / 'marketplace.json': {
            'name': 'studio-local', 'interface': {'displayName': 'STUDIO · Local'},
            'plugins': [{'name': 'h3-image-companion', 'source': {'source': 'local', 'path': './plugins/h3-image-companion'},
                         'policy': {'installation': 'AVAILABLE', 'authentication': 'ON_INSTALL'}, 'category': 'Productivity'}]}}
    for path, content in manifests.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n')
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    root = build(repo, repo / 'work' / 'chatgpt-companion-marketplace')
    print('Prepared local plugin:', root)
    if args.install:
        codex = shutil.which('codex')
        if not codex:
            raise SystemExit('Codex CLI not found; use the ChatGPT plugin install UI with this local marketplace.')
        subprocess.run([codex, 'plugin', 'marketplace', 'add', str(root), '--json'], check=True)
        subprocess.run([codex, 'plugin', 'add', 'h3-image-companion@studio-local', '--json'], check=True)
        print('Installed. Refresh ChatGPT plugins before opening the companion.')


if __name__ == '__main__':
    main()
