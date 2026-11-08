"""Independent local franchise boards; never writes the production draft or jobs."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from fastapi import APIRouter, HTTPException, Request
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_BYTES = 8 * 1024 * 1024
DEFAULT_NAMES: tuple[str, ...] = ()  # a fresh Studio starts without franchises; the user creates their own

class NameInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)

class FranchiseInput(NameInput):
    locale: Literal['ru', 'en'] = 'ru'  # language of the two default boards' names (the board page sends the UI language)

class BoardInput(NameInput):
    kind: str = Field(default='ideas', pattern='^(world|ideas|episode)$')

class EntityInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    type: str = Field(pattern='^(character|location|rule|idea|scene)$')
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(default='', max_length=20000)
    state: str = Field(default='draft', pattern='^(draft|approved)$')
    asset_ids: list[str] = Field(default_factory=list, max_length=100)

class BoardState(BaseModel):
    archived: bool

class EntityUpdate(EntityInput):
    revision: int = Field(ge=0, strict=True)

class FranchiseStore:
    def __init__(self, root):
        self.path = root / 'franchises.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS franchises (id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS boards (
                    id TEXT PRIMARY KEY, franchise_id TEXT NOT NULL REFERENCES franchises(id),
                    name TEXT NOT NULL, kind TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    content TEXT NOT NULL DEFAULT '{"children":[]}', previous TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, franchise_id TEXT NOT NULL REFERENCES franchises(id),
                    content TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    previous TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY);
            ''')
            if 'archived' not in {row['name'] for row in db.execute('PRAGMA table_info(boards)')}:
                db.execute('ALTER TABLE boards ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
            if not db.execute("SELECT 1 FROM migrations WHERE id='initial-franchises'").fetchone():
                for name in DEFAULT_NAMES:
                    self.create_franchise(db, name)
                db.execute("INSERT INTO migrations VALUES ('initial-franchises')")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_franchise(self, db, name, locale='ru'):
        ident = str(uuid.uuid4())
        db.execute('INSERT INTO franchises VALUES (?,?)', (ident, name))
        defaults = [('World and characters', 'world'), ('Ideas', 'ideas')] if locale == 'en' else [('Мир и персонажи', 'world'), ('Идеи', 'ideas')]
        for title, kind in defaults:
            db.execute('INSERT INTO boards(id,franchise_id,name,kind) VALUES (?,?,?,?)',
                       (str(uuid.uuid4()), ident, title, kind))
        return {'id': ident, 'name': name}


def make_router(store):
    data = FranchiseStore(store.root)
    router = APIRouter(prefix='/api/franchises')

    def board(db, fid, bid):
        row = db.execute('SELECT * FROM boards WHERE id=? AND franchise_id=?', (bid, fid)).fetchone()
        if row is None:
            raise HTTPException(404, 'Доска этой франшизы не найдена')
        return row

    def entity(db, fid, eid):
        row = db.execute('SELECT * FROM entities WHERE id=? AND franchise_id=?', (eid, fid)).fetchone()
        if row is None:
            raise HTTPException(404, 'Запись картотеки этой франшизы не найдена')
        return {'id': row['id'], 'revision': row['revision'], **json.loads(row['content'])}

    @router.get('/{fid}/entities')
    def list_entities(fid: str):
        with data.db() as db:
            return [entity(db, fid, row['id']) for row in db.execute(
                'SELECT id FROM entities WHERE franchise_id=? ORDER BY rowid', (fid,))]

    @router.post('/{fid}/entities', status_code=201)
    def create_entity(fid: str, payload: EntityInput):
        eid = str(uuid.uuid4())
        with data.db() as db:
            if not db.execute('SELECT 1 FROM franchises WHERE id=?', (fid,)).fetchone():
                raise HTTPException(404, 'Франшиза не найдена')
            db.execute('INSERT INTO entities(id,franchise_id,content) VALUES (?,?,?)',
                       (eid, fid, payload.model_dump_json()))
            return entity(db, fid, eid)

    @router.get('/{fid}/entities/{eid}')
    def get_entity(fid: str, eid: str):
        with data.db() as db:
            return entity(db, fid, eid)

    @router.put('/{fid}/entities/{eid}')
    def update_entity(fid: str, eid: str, payload: EntityUpdate):
        with data.db() as db:
            entity(db, fid, eid)
            changed = db.execute('''UPDATE entities SET previous=content,content=?,revision=revision+1,
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND franchise_id=? AND revision=?''',
                (json.dumps(payload.model_dump(exclude={'revision'}), ensure_ascii=False), eid, fid, payload.revision)).rowcount
            if not changed:
                raise HTTPException(409, 'Запись изменена в другой вкладке. Скопируй свой текст перед повторным открытием карточки.')
            return entity(db, fid, eid)

    @router.get('')
    def list_franchises():
        with data.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM franchises ORDER BY rowid')]

    @router.post('', status_code=201)
    def create_franchise(payload: FranchiseInput):
        with data.db() as db:
            return data.create_franchise(db, payload.name, payload.locale)

    @router.patch('/{fid}')
    def rename_franchise(fid: str, payload: NameInput):
        with data.db() as db:
            if db.execute('UPDATE franchises SET name=? WHERE id=?', (payload.name, fid)).rowcount != 1:
                raise HTTPException(404, 'Франшиза не найдена')
        return {'id': fid, 'name': payload.name}

    @router.get('/{fid}/boards')
    def list_boards(fid: str):
        with data.db() as db:
            if not db.execute('SELECT 1 FROM franchises WHERE id=?', (fid,)).fetchone():
                raise HTTPException(404, 'Франшиза не найдена')
            return [dict(row) for row in db.execute(
                'SELECT id,name,kind,revision,updated_at,archived FROM boards WHERE franchise_id=? ORDER BY rowid', (fid,))]

    @router.post('/{fid}/boards', status_code=201)
    def create_board(fid: str, payload: BoardInput):
        ident = str(uuid.uuid4())
        with data.db() as db:
            if not db.execute('SELECT 1 FROM franchises WHERE id=?', (fid,)).fetchone():
                raise HTTPException(404, 'Франшиза не найдена')
            db.execute('INSERT INTO boards(id,franchise_id,name,kind) VALUES (?,?,?,?)',
                       (ident, fid, payload.name, payload.kind))
        return {'id': ident, 'name': payload.name, 'kind': payload.kind, 'revision': 0}

    @router.patch('/{fid}/boards/{bid}/state')
    def set_board_state(fid: str, bid: str, payload: BoardState):
        with data.db() as db:
            board(db, fid, bid)
            db.execute('UPDATE boards SET archived=? WHERE id=? AND franchise_id=?', (int(payload.archived), bid, fid))
        return {'archived': payload.archived}

    @router.post('/{fid}/boards/{bid}/duplicate', status_code=201)
    def duplicate_board(fid: str, bid: str):
        ident = str(uuid.uuid4())
        with data.db() as db:
            source = board(db, fid, bid)
            name = source['name'][:150] + ' · копия'
            db.execute('INSERT INTO boards(id,franchise_id,name,kind,content) VALUES (?,?,?,?,?)',
                       (ident, fid, name, source['kind'], source['content']))
        return {'id': ident, 'name': name, 'kind': source['kind']}

    @router.get('/{fid}/boards/{bid}/previous')
    def previous_board(fid: str, bid: str):
        with data.db() as db:
            row = board(db, fid, bid)
            if row['previous'] is None:
                raise HTTPException(404, 'Предыдущей версии пока нет')
            return {'content': json.loads(row['previous']), 'revision': row['revision']}

    @router.get('/{fid}/boards/{bid}')
    def get_board(fid: str, bid: str):
        with data.db() as db:
            row = dict(board(db, fid, bid))
            row['content'] = json.loads(row['content'])
            # The entity is authoritative; board positions and drawings stay independent.
            for node in row['content'].get('children', []):
                meta = node.get('studio', {})
                if isinstance(meta, dict) and meta.get('entity_id'):
                    record = entity(db, fid, meta['entity_id'])
                    node['studio'] = {**record, 'entity_id': record['id'], 'entity_revision': record['revision']}
                    node['text'] = {**(node.get('text') if isinstance(node.get('text'), dict) else {}), 'children': [{'text': record['title']}]}

        row.pop('previous')
        return row

    @router.put('/{fid}/boards/{bid}')
    async def save_board(fid: str, bid: str, request: Request):
        raw = await request.body()
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, 'Доска слишком большая; используй материалы из библиотеки вместо встроенных файлов')
        try:
            payload = json.loads(raw)
            content, revision = payload['content'], payload['revision']
            if type(revision) is not int or revision < 0 or not isinstance(content, dict):
                raise ValueError()
            nodes = content.get('children')
            if not isinstance(nodes, list) or len(nodes) > 3000 or any(not isinstance(n, dict) for n in nodes):
                raise ValueError()
            for node in nodes:
                meta = node.get('studio')
                if meta is not None and not isinstance(meta, dict):
                    raise ValueError()
                if isinstance(meta, dict) and not meta.get('entity_id'):
                    node['studio'] = EntityInput.model_validate(meta).model_dump()
            name = payload.get('name')
            if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 160):
                raise ValueError()
            encoded = json.dumps(content, ensure_ascii=False, allow_nan=False)
        except (ValueError, KeyError, TypeError, ValidationError):
            raise HTTPException(422, 'Неверный формат доски')
        with data.db() as db:
            board(db, fid, bid)
            for node in nodes:
                meta = node.get('studio', {})
                if isinstance(meta, dict) and meta.get('entity_id'):
                    if (not isinstance(meta['entity_id'], str) or len(meta['entity_id']) > 80 or
                            not db.execute('SELECT 1 FROM entities WHERE id=? AND franchise_id=?', (meta['entity_id'], fid)).fetchone()):
                        raise HTTPException(422, 'Карточка ссылается на запись другой франшизы или отсутствующую запись')
            changed = db.execute('''UPDATE boards SET previous=content,content=?,revision=revision+1,
                name=COALESCE(?,name),updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND franchise_id=? AND revision=?''',
                (encoded, name.strip() if name else None, bid, fid, revision)).rowcount
            if not changed:
                raise HTTPException(409, 'Доска изменена в другой вкладке. Экспортируй свою копию перед перезагрузкой.')
        return {'revision': revision + 1}

    return router
