#!/usr/bin/env python3
"""Read-only Context-IR deployment check. Does not submit analysis or render jobs."""
import argparse
import json
import sys
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--studio', default='http://127.0.0.1:18187')
    args = parser.parse_args()
    try:
        with urllib.request.urlopen(args.studio.rstrip('/') + '/api/context-ir/capabilities', timeout=8) as response:
            result = json.load(response)
        print('Mac Context-IR:', result['version'])
        print('Workflows:', ', '.join(result['workflows']))
        print('Backend:', result.get('backend', 'gpu'), '—', result['state'], '—', result['message'])
        print('GPU render / visual acceptance: not tested by this check')
        # Context-IR moved to the user's Claude/Codex subscription agents. In that mode
        # gpu_ready is intentionally false: GPU is reserved for H3 generation only.
        if result.get('backend') == 'subscription_agent':
            return 0 if result.get('preparation_requires_gpu') is False else 2
        return 0 if result.get('gpu_ready') else 2
    except (OSError, ValueError, KeyError) as exc:
        print('FAIL: Studio Context-IR API is unavailable or outdated:', str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
