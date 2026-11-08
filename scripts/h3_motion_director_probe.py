#!/usr/bin/env python3
"""Read-only compatibility probe for the separately licensed Motion Director node."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

NODE = "MiniMaxH3MotionDirector"
REQUIRED = {"model", "video_vae", "audio_vae", "clip", "task_type", "global_prompt", "timeline_data",
            "frame_rate", "width", "height", "total_frames"}
OPTIONAL = {"motion_context_enabled", "audio_context_enabled", "context_length", "steps"}


def probe(info: dict) -> dict:
    node = info.get(NODE) or {}
    fields = node.get("input") or {}
    required = fields.get("required") or {}
    optional = fields.get("optional") or {}
    missing = sorted(REQUIRED - set(required))
    unavailable = sorted(OPTIONAL - set(optional))
    task = required.get("task_type") or []
    choices = task[0] if isinstance(task, list) and task else []
    mixed = any("mixed" in str(choice).lower() for choice in choices) if isinstance(choices, list) else False
    return {"installed": bool(node), "api_compatible": bool(node) and not missing and mixed,
            "mixed_mode": mixed, "missing_required": missing, "missing_optional": unavailable,
            "node": NODE, "expected_upstream_version": "v1.2.0", "expected_upstream_commit": "0e6b5913bbce44932a82fa9eed79a26c66c578d8",
            "version_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("object_info", help="ComfyUI /object_info URL or a saved JSON file")
    args = parser.parse_args()
    try:
        if args.object_info.startswith(("http://", "https://")):
            with urllib.request.urlopen(args.object_info, timeout=15) as response:
                info = json.load(response)
        else:
            with open(args.object_info, encoding="utf-8") as handle:
                info = json.load(handle)
        result = probe(info)
    except (OSError, ValueError) as exc:
        print(json.dumps({"api_compatible": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["api_compatible"] else 1


if __name__ == "__main__":
    sys.exit(main())
