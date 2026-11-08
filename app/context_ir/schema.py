from __future__ import annotations

from app.fingerprints import json_sha256 as digest
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.h3_ir import CHARACTER_ROLES, MOTION_VIDEO_ROLES, STRUCTURE_VIDEO_ROLES, Reference, build_skeleton, render_ir, shot_starts, validate_ir

VERSION = 'context-ir-2'




class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Asset(Strict):
    asset_id: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['image', 'video'] = 'image'
    number: int = Field(ge=1, le=9)
    role: str = Field(min_length=1, max_length=100)
    owner: str = Field(default='', max_length=80)
    duration_seconds: float | None = Field(default=None, ge=2, le=15.1)
    performer: str = Field(default='', max_length=300)


class Snapshot(Strict):
    brief: str = Field(min_length=3, max_length=16000)
    references: list[Asset] = Field(min_length=1, max_length=12)
    duration: int = Field(default=10, ge=4, le=15)
    shots: int = Field(default=1, ge=1, le=6)
    settings: dict = Field(default_factory=dict)
    required_lines: list[str] = Field(default_factory=list, max_length=20)
    preserve: str = Field(default='', max_length=4000)
    change: str = Field(default='', max_length=4000)
    variation_id: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def bindings(self):
        for kind, limit in (('image', 9), ('video', 3)):
            group = [a for a in self.references if a.kind == kind]
            if len(group) > limit or [a.number for a in group] != list(range(1, len(group) + 1)):
                raise ValueError(f'Номера {kind} должны идти подряд; лимит {limit}')
        videos = [a for a in self.references if a.kind == 'video']
        if any(a.duration_seconds is None for a in videos) or sum(a.duration_seconds or 0 for a in videos) > 15.1:
            raise ValueError('Видео-референсы должны иметь проверенную длительность, суммарно до 15 секунд')
        if len({a.asset_id for a in self.references}) != len(self.references):
            raise ValueError('Повторный asset_id')
        allowed = set(CHARACTER_ROLES) | {'Окружение', 'Предмет', 'Стиль', 'Свет', 'Цвет', 'Композиция', 'Кадр сцены'}
        owners = {a.owner.casefold() for a in self.references if a.role in {'Лицо героя', 'Персонаж целиком'} and a.owner}
        for a in self.references:
            if a.owner != a.owner.strip() or any(c in a.owner for c in '\n\r<>'):
                raise ValueError('Имя владельца должно быть одной строкой без меток H3')
            if a.owner and a.role not in set(CHARACTER_ROLES) | set(MOTION_VIDEO_ROLES) | {'Исходное видео'}:
                raise ValueError('Владелец назначается только фото героя или костюма')
            roles = allowed if a.kind == 'image' else set(MOTION_VIDEO_ROLES) | set(STRUCTURE_VIDEO_ROLES)
            if a.role not in roles:
                raise ValueError(f'Назначь явную поддержанную роль {a.kind} {a.number}')
            if a.kind == 'video' and a.owner and a.owner.casefold() not in owners:
                raise ValueError('Владелец видео должен иметь фото лица или полного роста')
            if a.kind == 'video' and a.role in MOTION_VIDEO_ROLES and len(owners) > 1 and not a.owner:
                raise ValueError('При нескольких героях назначь владельца видео движения')
            if a.kind == 'video' and a.owner and len(owners) > 1 and not a.performer.strip():
                raise ValueError('Укажи исполнителя в видео, например человек слева')
            if a.role in CHARACTER_ROLES and not a.owner.strip():
                raise ValueError(f'Назначь владельца фото {a.number}')
            if a.role == 'Костюм' and a.owner.casefold() not in owners:
                raise ValueError('Костюм должен принадлежать герою с фото лица или полного роста')
        if any(not line.strip() or len(line) > 1000 for line in self.required_lines):
            raise ValueError('Обязательные реплики должны быть непустыми и не длиннее 1000 символов')
        return self

    def cache_input(self):
        data = self.model_dump()
        data['settings'].pop('seed', None)
        return data


class Evidence(Strict):
    asset_id: str
    observation: str = Field(min_length=1, max_length=2000)
    status: Literal['observed', 'inferred', 'unknown']


class Beat(Strict):
    initial_state: str = Field(min_length=1, max_length=1500)
    action: str = Field(min_length=1, max_length=3000)
    final_state: str = Field(min_length=1, max_length=1500)
    description: str = Field(min_length=1, max_length=8000)


class ReferenceChange(Strict):
    asset_id: str
    instruction_quote: str = Field(min_length=3, max_length=1000)
    description: str = Field(min_length=3, max_length=1500)


class ScenePlan(Strict):
    reference_changes: list[ReferenceChange] = Field(default_factory=list, max_length=12)
    summary: str = Field(min_length=1, max_length=2000)
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    beats: list[Beat] = Field(min_length=1, max_length=6)
    overall_soundscape: str = Field(min_length=1, max_length=2000)
    non_diegetic_music: str = Field(min_length=1, max_length=2000)
    unresolved: list[str] = Field(default_factory=list, max_length=20)


def compile_plan(snapshot: Snapshot, plan: ScenePlan) -> dict:
    if len(plan.beats) != snapshot.shots:
        raise ValueError('Количество шотов в ответе отличается от задания')
    ids = [e.asset_id for e in plan.evidence]
    if len(ids) != len(set(ids)) or set(ids) != {a.asset_id for a in snapshot.references}:
        raise ValueError('Evidence должны описывать каждый прикреплённый файл ровно один раз')
    # Labels and retention are owned by code, never generated by the model.
    sk = build_skeleton([Reference(a.kind, a.number, a.role, a.owner) for a in snapshot.references],
                        use_video_audio=bool(snapshot.settings.get('use_video_audio')))
    if snapshot.settings.get('use_video_audio'):
        sk.labels.update(f'<Audio {i}>' for i in range(1, 1 + sum(a.kind == 'video' for a in snapshot.references)))
    retention = list(sk.retention_lines)
    for ref in snapshot.references:
        if ref.kind == 'video' and ref.owner and ref.performer:
            retention.append(f'<Video {ref.number}> (motion of {ref.owner}): attribute_transfer - '
                             f'the performer identified as {ref.performer} provides the movement for {ref.owner}; '
                             'the target retains appearance from its own pictures.')
    requested = snapshot.brief + '\n' + snapshot.change
    for change in plan.reference_changes:
        ref = next((a for a in snapshot.references if a.asset_id == change.asset_id), None)
        if ref is None or change.instruction_quote not in requested:
            raise ValueError('Изменение референса не подтверждено исходной инструкцией')
        tag = f"<{'Picture' if ref.kind == 'image' else 'Video'} {ref.number}>"
        subjects = [line.split(' is ', 1)[0] for line in sk.subject_lines if tag in line and line.startswith('<Subject')]
        targets = subjects or [tag]
        for target in targets:
            retention = [line for line in retention if not line.startswith(target + ' ')]
            retention.append(f'{target} (appears in [SHOTS]): partially_preserved - {change.description}')
    durations = [snapshot.duration / snapshot.shots] * snapshot.shots
    headers = shot_starts(durations)
    shots = ', '.join(f'[Shot {i + 1}]' for i in range(snapshot.shots))
    description = '\n\n'.join(f'{header}\n{b.initial_state}\n{b.action}\n{b.description}\n{b.final_state}'
                                for header, b in zip(headers, plan.beats))
    prompt = render_ir({
        'subject_definitions': '\n'.join(sk.subject_lines),
        'summary': '[' + ' + '.join(sk.task_types or ['reference generation']) + '] ' + plan.summary,
        'retention_analysis': '\n'.join(retention).replace('[SHOTS]', shots) or 'The scene follows the written brief.',
        'detailed_description': description,
        'overall_soundscape': plan.overall_soundscape,
        'non_diegetic_music': plan.non_diegetic_music,
    })
    problems = validate_ir(prompt, sk, headers)
    problems += [f'Пропущена обязательная реплика: {line}' for line in snapshot.required_lines if line not in prompt]
    problems += [f'Материал не прочитан: {e.asset_id}' for e in plan.evidence if e.status == 'unknown']
    problems += [f'Требует уточнения: {p}' for p in plan.unresolved]
    if any(a.kind == 'video' for a in snapshot.references):
        problems.append('совет: Видео проанализировано по выборке кадров; точность переноса движения проверяется по ролику.')
    if snapshot.settings.get('use_video_audio'):
        problems.append('совет: Звук исходного видео передаётся в H3, но Context-IR его не прослушивает.')
    return {'version': VERSION, 'context': plan.model_dump(), 'prompt': prompt,
            'compiled_prompt_hash': digest(prompt), 'problems': problems,
            'bindings': [a.model_dump() for a in snapshot.references], 'shot_headers': headers}


class VideoFrame(Strict):
    time_seconds: float = Field(ge=0, le=15.1)
    jpeg_base64: str = Field(min_length=1, max_length=3_000_000)
