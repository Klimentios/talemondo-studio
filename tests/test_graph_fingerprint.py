"""build_prompt output, byte for byte, over a broad option matrix (tests/graph_fingerprint_cases.py).

A failure means the ComfyUI graph changed. That is only acceptable for a deliberate change the user
approved; then regenerate with `.venv/bin/python -m tests.graph_fingerprint_cases --write`.
"""
import json

import pytest

from tests.graph_fingerprint_cases import GOLDEN, canonical, render

EXPECTED = json.loads(GOLDEN.read_text())
ACTUAL = render()


def test_case_set_is_unchanged():
    assert sorted(ACTUAL) == sorted(EXPECTED)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_graph_is_byte_identical(name):
    assert canonical(ACTUAL[name]) == canonical(EXPECTED[name])
