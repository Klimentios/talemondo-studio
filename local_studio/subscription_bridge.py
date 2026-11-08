"""Local durable browser connector. No model API, CLI, credential or remote endpoint calls."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import closing

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class Turn(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    chat_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    mode: str = Field(pattern=r'^(chat|image)$')
    text: str = Field(min_length=1, max_length=50000)
    attachments: list[str] = Field(default_factory=list, max_length=6)
    context: dict = Field(default_factory=dict)


class Result(BaseModel):
    token: str
    text: str = Field(default='', max_length=100000)
    images: list[str] = Field(default_factory=list, max_length=4)
    error: str = Field(default='', max_length=1500)


class Bridge:
    def __init__(self, store, hub):
        self.store, self.hub = store, hub
        self.path = store.root / 'subscription-bridge.sqlite3'
        self.seen = 0.0
        with closing(self.db()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, chat TEXT, state TEXT, data TEXT)')

    def db(self):
        return sqlite3.connect(self.path, timeout=10)

    def get(self, db, rid):
        row = db.execute('SELECT data FROM turns WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, 'Запрос не найден')
        return json.loads(row[0])

    def put(self, db, row):
        db.execute('INSERT OR REPLACE INTO turns VALUES (?,?,?,?)',
                   (row['id'], row['chat_id'], row['state'], json.dumps(row, ensure_ascii=False)))

    @staticmethod
    def public(row):
        return {k: v for k, v in row.items() if k not in {'token', 'files', 'prompt', 'fingerprint'}}

    def create(self, payload):
        fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT data FROM turns WHERE id=?', (payload.request_id,)).fetchone()
            if exists:
                row = json.loads(exists[0])
                if row['fingerprint'] != fingerprint:
                    raise HTTPException(409, 'ID уже использован для другого запроса')
                return self.public(row)
            chat = self.hub.load(payload.chat_id)
            if chat.get('run_id') or payload.chat_id in self.hub.runs or chat.get('subscription_pending'):
                raise HTTPException(409, 'В чате уже выполняется задание')
            if time.time() - self.seen > 15:
                raise HTTPException(409, 'Браузерный коннектор не подключён. Включите расширение TaleMondo Connector в Chrome.')
            files = []
            for aid in dict.fromkeys(payload.attachments):
                asset = self.store.get_asset(aid)
                if not asset or asset['kind'] != 'image' or asset['size'] > 15 * 1024 * 1024:
                    raise HTTPException(422, 'Коннектор принимает изображения до 15 МБ')
                files.append({'asset_id': aid, 'name': asset['original_name'], 'mime': asset['mime']})
            context = json.dumps(payload.context, ensure_ascii=False)
            history = '\n'.join(f"{m['role']}: {m.get('text', '')}" +
                                ''.join(f" [Studio asset_id={a['asset_id']}]" for a in m.get('attachments', []))
                                for m in chat.get('messages', [])[-12:])
            prompt = ('Отвечай по-русски. Это сообщение из TaleMondo Studio. ' +
                      ('Создай изображение встроенным генератором по заданию и прикреплённым референсам. ' if payload.mode == 'image' else '') +
                      '\nКонтекст Studio:\n' + context + '\nИстория:\n' + history + '\nЗапрос:\n' + payload.text)
            if len(prompt) > 100000:
                raise HTTPException(422, 'Контекст слишком большой для браузерного коннектора')
            row = {'id': payload.request_id, 'chat_id': payload.chat_id, 'state': 'queued', 'mode': payload.mode,
                   'prompt': prompt, 'files': files, 'token': secrets.token_hex(24), 'fingerprint': fingerprint,
                   'created_at': time.time(), 'error': ''}
            chat['subscription_pending'] = row['id']
            if not any(m.get('bridge_id') == row['id'] for m in chat['messages']):
                chat['messages'].append({'role': 'user', 'text': payload.text, 'attachments': files, 'bridge_id': row['id']})
            if chat['title'] == 'Новый чат':
                chat['title'] = payload.text[:60]
            self.hub.save(chat)
            self.put(db, row)
            return self.public(row)

    def claim(self):
        self.seen = time.time()
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            # Exactly one outstanding browser operation globally. An interrupted claim never expires/replays.
            if db.execute("SELECT 1 FROM turns WHERE state='claimed'").fetchone():
                return None
            found = db.execute("SELECT data FROM turns WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not found:
                return None
            row = json.loads(found[0])
            row['state'] = 'claimed'
            self.put(db, row)
            return row

    def image(self, encoded, rid, index):
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            raise HTTPException(422, 'Некорректное изображение') from None
        if not data or len(data) > 25 * 1024 * 1024:
            raise HTTPException(422, 'Размер изображения превышен')
        from .media import detect_media
        try:
            kind, mime = detect_media(data[:64])
        except ValueError:
            raise HTTPException(422, 'Файл не является изображением') from None
        if kind != 'image':
            raise HTTPException(422, 'Ожидается изображение')
        sha = hashlib.sha256(data).hexdigest()
        old = self.store.find_asset(sha256=sha, kind='image')
        if old:
            return old
        folder = self.store.assets_dir / sha[:2]
        folder.mkdir(exist_ok=True)
        dest = folder / sha
        if not dest.exists():
            dest.write_bytes(data)
        return self.store.add_asset(kind='image', role='Кадр сцены', original_name=f'chatgpt-{rid}-{index}.png',
                                    mime=mime, size=len(data), sha256=sha, path=dest)

    def finish(self, rid, payload):
        with closing(self.db()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = self.get(db, rid)
            if not secrets.compare_digest(row['token'], payload.token):
                raise HTTPException(403, 'Неверный токен задания')
            if row['state'] in {'completed', 'failed'}:
                return self.public(row)
            if row['state'] != 'claimed':
                raise HTTPException(409, 'Запрос не был взят коннектором')
            error = payload.error
            if not error and row['mode'] == 'image' and not payload.images:
                error = 'Чат не вернул изображение. Повторная генерация автоматически не запускается.'
            if not error and not payload.text and not payload.images:
                error = 'Чат вернул пустой ответ'
            assets = [self.image(encoded, rid, n) for n, encoded in enumerate(payload.images)] if not error else []
            chat = self.hub.load(row['chat_id'])
            if not any(m.get('bridge_result') == rid for m in chat['messages']):
                chat['messages'].append({'role': 'assistant', 'provider': 'chatgpt', 'text': payload.text,
                    'attachments': [{'asset_id': a['asset_id'], 'kind': 'image', 'name': a['original_name']} for a in assets],
                    'events': [], 'bridge_result': rid, **({'error': error} if error else {})})
            if chat.get('subscription_pending') == rid:
                chat.pop('subscription_pending', None)
            chat['subscription_context_changed'] = True
            self.hub.save(chat)
            row.update(state='failed' if error else 'completed', error=error)
            self.put(db, row)
            return self.public(row)


def make_router(store, hub):
    router = APIRouter(prefix='/api/subscription-bridge')
    bridge = Bridge(store, hub)

    @router.get('/status')
    async def status():
        return {'connected': time.time() - bridge.seen < 15, 'provider': 'chatgpt'}

    @router.post('/turns', status_code=201)
    async def create(payload: Turn):
        return bridge.create(payload)

    @router.post('/claim')
    async def claim():
        return bridge.claim()

    @router.get('/turns/{rid}')
    async def get(rid: str):
        with closing(bridge.db()) as db:
            return bridge.public(bridge.get(db, rid))

    @router.get('/turns/{rid}/files/{aid}')
    async def file(rid: str, aid: str, token: str):
        from fastapi.responses import FileResponse
        with closing(bridge.db()) as db:
            row = bridge.get(db, rid)
        if not secrets.compare_digest(token, row['token']) or not any(f['asset_id'] == aid for f in row['files']):
            raise HTTPException(403, 'Файл не относится к запросу')
        asset = store.get_asset(aid)
        return FileResponse(asset['path'], media_type=asset['mime'])

    @router.post('/turns/{rid}/result')
    async def result(rid: str, payload: Result):
        return bridge.finish(rid, payload)

    router.bridge = bridge
    return router
