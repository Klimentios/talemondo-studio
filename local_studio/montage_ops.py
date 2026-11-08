"""Small, revision-bound timeline edits. Indices refer to the result of preceding operations.

Inspired by VideoSOS 2's documented timeline operations; implemented against our EDL, no upstream code copied.
"""
from copy import deepcopy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Operation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['addClip', 'removeClip', 'moveClip', 'trimClip', 'setTransition']
    index: int = Field(ge=0, strict=True)
    to: int | None = Field(default=None, ge=0, strict=True)
    clip: dict | None = None
    source_in: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    source_out: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    transition: str | None = Field(default=None, max_length=40)
    seconds: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)


class TimelineEdit(BaseModel):
    revision: str = Field(min_length=1, max_length=64)
    ops: list[Operation] = Field(min_length=1, max_length=100)
    note: str = Field(min_length=1, max_length=600)


def apply_ops(sheet: dict, ops: list[Operation]) -> dict:
    result = deepcopy(sheet)
    clips = result.setdefault('clips', [])
    for number, op in enumerate(ops, 1):
        def fail(message):
            raise ValueError(f'Операция {number} ({op.op}): {message}')
        if op.op == 'addClip':
            if op.index > len(clips) or not op.clip:
                fail('нужен clip и позиция от 0 до количества клипов')
            clips.insert(op.index, deepcopy(op.clip))
            continue
        if op.index >= len(clips):
            fail('клип с таким индексом отсутствует')
        if op.op == 'removeClip':
            clips.pop(op.index)
        elif op.op == 'moveClip':
            if op.to is None or op.to >= len(clips):
                fail('нужна конечная позиция to в списке клипов')
            clips.insert(op.to, clips.pop(op.index))
        elif op.op == 'trimClip':
            if op.source_in is None and op.source_out is None:
                fail('нужны source_in или source_out в секундах исходника')
            if op.source_in is not None:
                clips[op.index]['in'] = op.source_in
            if op.source_out is not None:
                clips[op.index]['out'] = op.source_out
            clip = clips[op.index]
            if clip.get('out') is not None and clip['out'] <= clip.get('in', 0):
                fail('конец должен быть позже начала')
        elif op.op == 'setTransition':
            if not op.transition:
                fail('нужен transition из набора EDL')
            clips[op.index]['transition_in'] = op.transition
            clips[op.index].pop('transition_effect', None)
            clips[op.index].pop('transition_duration', None)
            if op.seconds is not None:
                clips[op.index]['transition_duration'] = op.seconds
    return result
