#!/usr/bin/env python3
"""Instrument only the audited FBCache revision, preserving all cache decisions."""
import argparse
import hashlib
from pathlib import Path
import shutil
import os
import tempfile

SHA = '76ceae9eb347ea3c432103d0bd3e66cf53c398414b1c9af3d9f0be37bdfd5d41'
IMPORT = '\nimport time\nfrom ._studio_metrics import record as _record_cache_metrics, register_route\nregister_route()\n'
OLD = '''        logging.info("MiniMax H3 FBCache enabled: %s", preset)
        try:
            return executor(*args, **kwargs)
        finally:
            logging.info("MiniMax H3 FBCache: %s", cache.summary())
            cache.reset()'''
NEW = '''        logging.info("MiniMax H3 FBCache enabled: %s", preset)
        started = time.monotonic()
        completed = False
        try:
            result = executor(*args, **kwargs)
            completed = True
            return result
        finally:
            _record_cache_metrics(cache, preset, started, completed)
            logging.info("MiniMax H3 FBCache: %s", cache.summary())
            cache.reset()'''


def patched(source):
    original = source.replace(IMPORT, '').replace(NEW, OLD)
    if hashlib.sha256(original.encode()).hexdigest() != SHA or original.count(OLD) != 1:
        raise ValueError('FBCache source differs from audited revision; refusing instrumentation')
    return original.replace('import logging\n', 'import logging\n' + IMPORT, 1).replace(OLD, NEW, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache_directory', type=Path)
    args = parser.parse_args()
    nodes = args.cache_directory / 'nodes.py'
    result = patched(nodes.read_text())
    helper = Path(__file__).resolve().parents[1] / 'app/comfy_nodes/h3_cache_metrics/runtime.py'
    shutil.copyfile(helper, args.cache_directory / '_studio_metrics.py')
    with tempfile.NamedTemporaryFile(mode='w', dir=args.cache_directory, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(result)
    try:
        temporary.chmod(nodes.stat().st_mode & 0o777)
        os.replace(temporary, nodes)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
