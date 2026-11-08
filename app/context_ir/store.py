"""Durable preparation receipts, shared implementation on Mac and GPU."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from .schema import digest

TERMINAL = {'ready', 'needs_input', 'failed', 'cancelled'}


class Receipts:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('''CREATE TABLE IF NOT EXISTS context_jobs (
                id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')))''')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def unpack(row):
        if not row:
            return None
        d = dict(row)
        d['payload'] = json.loads(d['payload'])
        d['result'] = json.loads(d['result']) if d['result'] else None
        return d

    def get(self, key):
        with self.connect() as db:
            return self.unpack(db.execute('SELECT * FROM context_jobs WHERE id=?', (key,)).fetchone())

    def accept(self, key, payload, state='queued_local'):
        h = digest(payload)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM context_jobs WHERE id=?', (key,)).fetchone()
            if old:
                if old['input_hash'] != h:
                    raise ValueError('Этот ID уже принадлежит другому снимку входов')
                return self.unpack(old), False
            db.execute('INSERT INTO context_jobs(id,input_hash,payload,state) VALUES (?,?,?,?)',
                       (key, h, json.dumps(payload, ensure_ascii=False), state))
        return self.get(key), True

    def update(self, key, state, *, result=None, error='', expected=None):
        with self.connect() as db:
            query = 'UPDATE context_jobs SET state=?,result=?,error=?,updated_at=strftime(\'%Y-%m-%d %H:%M:%f\',\'now\') WHERE id=?'
            args = [state, json.dumps(result, ensure_ascii=False) if result is not None else None, error, key]
            if expected:
                query += ' AND state IN (' + ','.join('?' for _ in expected) + ')'
                args += list(expected)
            return db.execute(query, args).rowcount == 1

    def active(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM context_jobs WHERE state NOT IN ('ready','needs_input','failed','cancelled') ORDER BY created_at,rowid").fetchall()
            return [self.unpack(r) for r in rows]

    def cached(self, cache_key):
        with self.connect() as db:
            rows = db.execute("SELECT result FROM context_jobs WHERE state='ready' ORDER BY rowid DESC LIMIT 200").fetchall()
            for row in rows:
                result = json.loads(row[0])
                if result.get('cache_key') == cache_key:
                    return result
        return None

    def recent(self):
        with self.connect() as db:
            return [self.unpack(r) for r in db.execute('SELECT * FROM context_jobs ORDER BY rowid DESC LIMIT 30')]
