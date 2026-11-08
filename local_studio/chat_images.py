"""Durable handoff to a ChatGPT MCP client. No API generation, Codex subprocess or automatic retry."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .franchises import FranchiseStore
from .franchise_profile import load_profile
from .scene_kit import get_scene_kit


class ImageRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=100)
    shot_id: str = Field(min_length=1, max_length=150)
    purpose: Literal['frame', 'fantasy_world'] = 'frame'
    notes: str = Field(default='', max_length=2000)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def image_context(store, request: ImageRequest):
    project, revision = store.load_draft_snapshot()
    scene = next((s for s in (project or {}).get('scenes', []) if s.get('id') == request.scene_id), None)
    if not scene:
        raise HTTPException(404, 'Сцена не найдена в сохранённом черновике')
    cut = next((c for c in scene.get('cuts', []) if c.get('id') == request.shot_id), None)
    if not cut:
        raise HTTPException(404, 'Шот не найден')
    director = scene.get('sceneDirector') or {}
    bindings = {value for value in [(scene.get('franchiseSource') or {}).get('franchise_id'),
                                   (director.get('agentScene') or {}).get('franchise_id'),
                                   (cut.get('productionSpec') or {}).get('franchise_id')] if value}
    if len(bindings) > 1:
        raise HTTPException(409, 'Связи франшизы сцены расходятся: агент должен исправить её пакет')
    fid = next(iter(bindings), '')
    if not fid:
        raise HTTPException(422, 'Агент ещё не привязал паспорт к этой сцене. Дождись применения подготовленной сцены на холст.')
    profile, _ = load_profile(FranchiseStore(store.root), fid)
    kit = get_scene_kit(store, fid)
    world = (scene.get('storyboard') or {}).get('fantasyWorld') or {}
    shot = cut.get('sceneShot') or {}
    first_frame = ((cut.get('productionSpec') or {}).get('shot') or {}).get('first_frame_description') or shot.get('first_frame') or shot.get('state_before')
    summary = re.search(r'\[reference generation\]\s*([^\n]+)', str(cut.get('prompt') or ''), re.I)
    if request.purpose == 'fantasy_world':
        image_brief = world.get('description') or first_frame or (summary.group(1) if summary else cut.get('prompt', ''))
    else:
        image_brief = first_frame or (summary.group(1) if summary else cut.get('prompt', ''))
    if not str(image_brief).strip():
        raise HTTPException(422, 'Агент ещё не подготовил описание кадра этого шота')
    saved_refs = (scene.get('referenceState') or {}).get('references', scene.get('references', []))
    refs = [r for r in saved_refs if r.get('kind') == 'image' and
            (request.purpose == 'fantasy_world' or r.get('id') not in (cut.get('offRefs') or []))]
    if request.purpose == 'fantasy_world':
        refs = [r for r in refs if r.get('role') == 'Окружение' and r.get('id') != world.get('refId')]
    elif cut.get('fantasy') is True:
        if not world.get('assetId'):
            raise HTTPException(422, 'Сначала создай единый мир фантазии')
        if not any(r.get('id') == world['assetId'] for r in refs):
            raise HTTPException(422, 'Мир фантазии не привязан к шоту')
        refs = [r for r in refs if r.get('role') != 'Окружение' or r.get('id') == world['assetId']]
    references = []
    for ref in refs:
        asset = store.get_asset(ref['id'])
        if not asset:
            raise HTTPException(422, 'Отсутствует файл референса ' + ref['id'])
        references.append({**ref, 'sha256': asset['sha256'], 'asset_id': asset['asset_id']})
    fields = ('id', 'sceneShot', 'productionSpec', 'offRefs', 'fantasy', 'startFrame', 'director')
    profile_data = profile.model_dump()
    # The kit already carries character sheets and place descriptions. Do not repeat those roles,
    # whole video IR prompts, foreign-franchise catalogs and database allow-lists in an image handoff.
    visual_profile = {k: profile_data[k] for k in ('look', 'camera', 'director_defaults', 'life', 'notes') if k in profile_data}
    visual_kit = {k: kit[k] for k in ('franchise_id', 'franchise', 'mode', 'style', 'aspect_ratio', 'language',
                                     'cast', 'places', 'props', 'life', 'fingerprint') if k in kit}
    packet = {'scene_id': request.scene_id, 'shot_id': request.shot_id, 'franchise_id': fid,
              'purpose': request.purpose, 'notes': request.notes, 'title': scene.get('title'),
              'image_brief': image_brief,
              'story': director.get('idea') or '', 'scene_style': scene.get('sceneStyle'),
              'aspect': (scene.get('renderState') or {}).get('aspect') or scene.get('aspect') or profile.look.aspect_ratio,
              'shot': {k: cut[k] for k in fields if k in cut},
              'shot_prompt_sha256': digest(cut.get('prompt') or ''),
              'shot_order': [{'id': c.get('id'), 'description': {k: v for k, v in (c.get('sceneShot') or {}).items()
                              if k in ('title', 'purpose', 'first_frame', 'state_before', 'state_after', 'characters', 'location')}}
                             for c in scene.get('cuts', [])],
              'fantasy_world': {k: world[k] for k in ('description', 'baseId', 'assetId', 'refId') if k in world},
              'references': references, 'franchise_profile': visual_profile, 'franchise_kit': visual_kit,
              'instructions': ('Use your built-in ImageGen, not Codex or a paid API. Inspect the supplied images with view_image. '
                               'Preserve franchise medium, identities, anatomy, costume and relative scale. '
                               'The story and adjacent shots provide context, not a montage. '
                               + ('Draw ONE empty fantasy world, with no characters; transform the real place into the described world. '
                                  if request.purpose == 'fantasy_world' else
                                  'Draw ONE opening frame of this shot, before its later actions, with only its named cast and place. ')
                               + 'If references or visual standards are missing, report failure; do not invent them. '
                               'This request does not authorize a video render or approve a story.')}
    if len(json.dumps(packet, ensure_ascii=False, indent=1)) > 45000:
        raise HTTPException(422, 'Контекст превышает лимит MCP; он не был обрезан')
    return {**packet, 'fingerprint': digest(packet), 'draft_revision': revision}


class ChatImages:
    def __init__(self, store):
        self.store = store
        self.path = store.root / 'chat-image-requests.sqlite3'
        with closing(self.db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, data TEXT NOT NULL)')

    def db(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def public(row):
        return {k: v for k, v in row.items() if k not in ('claim_token', 'context')}

    def get(self, db, rid):
        row = db.execute('SELECT data FROM requests WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Запрос картинки не найден')
        return json.loads(row[0])

    def save(self, db, row):
        row['updated_at'] = time.time()
        db.execute('UPDATE requests SET data=? WHERE id=?', (json.dumps(row, ensure_ascii=False), row['id']))

    def refresh(self, db, row):
        if row['state'] == 'waiting_chatgpt':
            try:
                current = image_context(self.store, ImageRequest(**{k: row[k] for k in ('scene_id', 'shot_id', 'purpose', 'notes')}))
                stale = current['fingerprint'] != row['fingerprint']
            except HTTPException:
                stale = True
            if stale:
                row.update(state='stale', error='Контекст изменился до начала генерации')
                self.save(db, row)
        return row

    def create(self, request):
        context = image_context(self.store, request)
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT data FROM requests WHERE fingerprint=?', (context['fingerprint'],)).fetchone()
            if prior:
                prior = json.loads(prior[0])
                if prior['state'] != 'failed':
                    return self.public(prior)
                # The same context after a reported failure is a new request by the user, not an automatic retry.
                db.execute('DELETE FROM requests WHERE id=?', (prior['id'],))
            row = {'id': secrets.token_hex(16), 'fingerprint': context['fingerprint'], 'scene_id': request.scene_id,
                   'shot_id': request.shot_id, 'purpose': request.purpose, 'notes': request.notes,
                   'franchise_id': context['franchise_id'], 'state': 'waiting_chatgpt', 'context': context,
                   'created_at': time.time(), 'updated_at': time.time()}
            db.execute('INSERT INTO requests VALUES (?,?,?)', (row['id'], row['fingerprint'], json.dumps(row, ensure_ascii=False)))
            return self.public(row)

    def claim(self, rid, claim_id):
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = self.get(db, rid)
            if row['state'] == 'waiting_chatgpt':
                current = image_context(self.store, ImageRequest(**{k: row[k] for k in ('scene_id', 'shot_id', 'purpose', 'notes')}))
                if current['fingerprint'] != row['fingerprint']:
                    raise HTTPException(409, 'Контекст изменился: не генерируй по старому запросу')
            if row['state'] != 'waiting_chatgpt' and not (row['state'] == 'claimed' and row['claim_id'] == claim_id):
                raise HTTPException(409, 'Запрос уже взят другим исполнителем или завершён; не повторяй генерацию')
            row.update(state='claimed', claim_id=claim_id, claim_token=row.get('claim_token') or secrets.token_hex(24))
            self.save(db, row)
            return row

    def finish(self, rid, token, asset_id, error=''):
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = self.get(db, rid)
            if not secrets.compare_digest(row.get('claim_token', ''), token) or not token:
                raise HTTPException(403, 'Неверный токен задания')
            if row['state'] in ('completed', 'stale', 'failed'):
                if row.get('asset_id') == asset_id and row.get('error', '') == error:
                    return self.public(row)
                raise HTTPException(409, 'Результат уже записан')
            if row['state'] != 'claimed':
                raise HTTPException(409, 'Запрос не выполняется')
            if error:
                row.update(state='failed', error=error)
            else:
                asset = self.store.get_asset(asset_id)
                if not asset or asset['kind'] != 'image':
                    raise HTTPException(422, 'Результат должен быть картинкой из библиотеки Studio')
                from PIL import Image, UnidentifiedImageError
                try:
                    path = Path(asset['path'])
                    if hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
                        raise ValueError('hash mismatch')
                    with Image.open(path) as image:
                        image.verify()
                except (OSError, ValueError, UnidentifiedImageError):
                    raise HTTPException(422, 'Файл результата повреждён или его хеш изменился') from None
                try:
                    current = image_context(self.store, ImageRequest(**{k: row[k] for k in ('scene_id', 'shot_id', 'purpose', 'notes')}))
                    stale = current['fingerprint'] != row['fingerprint']
                except HTTPException:
                    stale = True
                row.update(state='stale' if stale else 'completed', asset_id=asset_id,
                           output_sha256=asset['sha256'], accepted=False)
            self.save(db, row)
            return self.public(row)


class Claim(BaseModel):
    claim_id: str = Field(min_length=1, max_length=150)


class Finish(BaseModel):
    claim_token: str = Field(min_length=1, max_length=100)
    asset_id: str = Field(default='', max_length=100)
    error: str = Field(default='', max_length=1000)


def make_router(ctx):
    router = APIRouter(prefix='/api/chat-images')
    def queue():
        return ChatImages(ctx.store)

    @router.post('/context')
    def context(request: ImageRequest):
        return image_context(ctx.store, request)

    @router.post('', status_code=201)
    def create(request: ImageRequest):
        return queue().create(request)

    @router.get('')
    def listing(scene_id: str = '', franchise_id: str = ''):
        q = queue()
        with closing(q.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows = [json.loads(r[0]) for r in db.execute('SELECT data FROM requests ORDER BY rowid DESC LIMIT 200')]
            return [q.public(q.refresh(db, r)) for r in rows if (not scene_id or r['scene_id'] == scene_id) and
                    (not franchise_id or r['franchise_id'] == franchise_id)]

    @router.get('/{rid}')
    def get(rid: str):
        q = queue()
        with closing(q.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            return q.public(q.refresh(db, q.get(db, rid)))

    @router.post('/{rid}/claim')
    def claim(rid: str, request: Claim):
        return queue().claim(rid, request.claim_id)

    @router.post('/{rid}/result')
    def finish(rid: str, request: Finish):
        return queue().finish(rid, request.claim_token, request.asset_id, request.error)

    return router
