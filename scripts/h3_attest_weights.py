#!/usr/bin/env python3
"""Verify explicitly selected weights.tsv files once; never installs or restarts services."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.model_inventory import attest_weights, write_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--weights-tsv', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    expected = {}
    for number, line in enumerate(args.weights_tsv.read_text().splitlines(), 1):
        fields = line.split('\t')
        if len(fields) < 3 or len(fields[2]) != 64 or any(c not in '0123456789abcdef' for c in fields[2]):
            parser.error(f'Неверная строка weights.tsv: {number}')
        if fields[0] in expected:
            parser.error(f'Повторный путь веса: {fields[0]}')
        expected[fields[0]] = fields[2]
    if not expected:
        parser.error('Пустой список весов')
    inventory = attest_weights(args.root, expected)
    write_inventory(args.out, inventory)
    print(f"Verified {len(expected)} selected files; disk inventory saved to {args.out}")


if __name__ == '__main__':
    main()
