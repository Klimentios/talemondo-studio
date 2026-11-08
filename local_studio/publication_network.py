"""Local connection journal; never infer a historical publication IP."""
import asyncio
from datetime import datetime, timezone
import httpx
from fastapi import APIRouter, HTTPException


def initialize(store):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS publication_network (id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, ip TEXT NOT NULL, country TEXT NOT NULL, country_code TEXT NOT NULL, region TEXT NOT NULL, scope TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS publication_network_links (post_id TEXT PRIMARY KEY REFERENCES posts(id), sample_id INTEGER NOT NULL REFERENCES publication_network(id), evidence TEXT NOT NULL)')


async def capture(store):
    async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
        response=await client.get('https://ipwho.is/')
        response.raise_for_status();data=response.json()
    if not data.get('success') or not data.get('ip') or not data.get('country_code'):
        raise ValueError('Не удалось определить внешний IP')
    import ipaddress
    ip=str(ipaddress.ip_address(data['ip']))
    with store.db() as db:
        cursor=db.execute('INSERT INTO publication_network(observed_at,ip,country,country_code,region,scope) VALUES(?,?,?,?,?,?)',(datetime.now(timezone.utc).isoformat(),ip,str(data.get('country',''))[:100],str(data['country_code'])[:2],str(data.get('region',''))[:100],'mac_connection'))
        return dict(db.execute('SELECT * FROM publication_network WHERE id=?',(cursor.lastrowid,)).fetchone())


async def run(root):
    from .analytics import AnalyticsStore
    store=AnalyticsStore(root);initialize(store)
    while True:
        try:await capture(store)
        except (httpx.HTTPError,ValueError):pass
        await asyncio.sleep(300)


def router(store):
    initialize(store);routes=APIRouter()
    @routes.get('/network')
    def network():
        with store.db() as db:
            row=db.execute('SELECT observed_at,country,country_code,region,scope FROM publication_network ORDER BY id DESC LIMIT 1').fetchone()
            return dict(row) if row else {'scope':'unknown'}
    @routes.post('/posts/{post_id}/publication-network')
    async def publication_network(post_id:str):
        # Call only at actual submission from this process, never on stats import.
        with store.db() as db:
            if not db.execute('SELECT 1 FROM posts WHERE id=?',(post_id,)).fetchone():raise HTTPException(404,'Ролик не найден')
        try:sample=await capture(store)
        except (httpx.HTTPError,ValueError):raise HTTPException(503,'Не удалось определить соединение') from None
        with store.db() as db:
            db.execute('INSERT INTO publication_network_links(post_id,sample_id,evidence) VALUES(?,?,?) ON CONFLICT(post_id) DO NOTHING',(post_id,sample['id'],'submission_process'))
        with store.db() as db:
            saved=db.execute('SELECT n.country,n.scope FROM publication_network_links l JOIN publication_network n ON n.id=l.sample_id WHERE l.post_id=?',(post_id,)).fetchone()
        return {'recorded':True,'country':saved['country'],'scope':saved['scope']}
    return routes
