"""Local social history: explicit source, precision and measurement basis."""
import asyncio
import csv
import io
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ConfigDict, StrictFloat
from .social_public import social_url, collect_public

METRICS={'followers':'Подписчики','following':'Подписки','posts':'Публикации','views':'Просмотры','likes':'Лайки','comments':'Комментарии','shares':'Репосты','saves':'Сохранения','reach':'Охват','impressions':'Показы','watch_time_hours':'Время просмотра, ч','avg_watch_seconds':'Средний просмотр, с','retention_percent':'Удержание, %','ctr_percent':'CTR, %','subscribers_gained':'Новые подписчики','subscribers_lost':'Отписки'}

def stamp(value=None):
    try:
        at=datetime.fromisoformat(value.replace('Z','+00:00')) if value else datetime.now(timezone.utc)
        if at.tzinfo is None:at=at.replace(tzinfo=timezone.utc)
        at=at.astimezone(timezone.utc)
        if at>datetime.now(timezone.utc)+timedelta(minutes=10):raise ValueError()
        return at.isoformat()
    except (ValueError,AttributeError):raise HTTPException(422,'Неверная дата замера. Используй ISO8601; даты без зоны считаются UTC.')

def checked_metrics(metrics):
    if not metrics or set(metrics)-METRICS.keys():raise HTTPException(422,'Укажи известные метрики; недоступные значения оставляй пустыми.')
    result={}
    for key,value in metrics.items():
        if value is None:continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0 or value>1e15 or (key.endswith('_percent') and value>100):raise HTTPException(422,'Неверное значение: '+key)
        if key not in {'watch_time_hours','avg_watch_seconds','retention_percent','ctr_percent'} and value%1:raise HTTPException(422,'Количество должно быть целым: '+key)
        result[key]=value
    if not result:raise HTTPException(422,'Нужен хотя бы один показатель.')
    return result

class AccountInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    franchise_id:str
    url:str=Field(max_length=500)
    label:str=Field(default='',max_length=160)
class PostInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    url:str=Field(max_length=500)
    title:str=Field(default='',max_length=200)
    format:str=Field(default='',max_length=80)
    topic:str=Field(default='',max_length=160)
    hook:str=Field(default='',max_length=500)
    published_at:str|None=None
    duration:float|None=Field(default=None,gt=0,le=86400)
class Measurement(BaseModel):
    model_config=ConfigDict(extra='forbid')
    metrics:dict[str,StrictFloat|None]
    observed_at:str|None=None
    post_id:str=''
    basis:str=Field(default='cumulative',pattern='^(cumulative|period)$')
    precision:str=Field(default='exact',pattern='^(exact|rounded)$')
    note:str=Field(default='',max_length=1000)
class AccountState(BaseModel):
    paused:bool

class CSVInput(BaseModel):
    text:str=Field(max_length=2*1024*1024)
    preview:bool=True

class AnalyticsStore:
    def __init__(self,root):
        self.root=root;self.path=root/'analytics.sqlite3'
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY,franchise_id TEXT NOT NULL,platform TEXT NOT NULL,url TEXT UNIQUE NOT NULL,handle TEXT NOT NULL,label TEXT NOT NULL,status TEXT DEFAULT 'new',last_attempt TEXT,error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY,account_id TEXT REFERENCES accounts(id),url TEXT UNIQUE NOT NULL,handle TEXT NOT NULL,metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS measurements(id INTEGER PRIMARY KEY,account_id TEXT REFERENCES accounts(id),post_id TEXT NOT NULL DEFAULT '',observed_at TEXT NOT NULL,collected_at TEXT NOT NULL,source TEXT NOT NULL,precision TEXT NOT NULL,basis TEXT NOT NULL,metrics TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',UNIQUE(account_id,post_id,observed_at,source,basis));
            CREATE INDEX IF NOT EXISTS measurement_account_time ON measurements(account_id,observed_at);''')
            if 'paused' not in {r['name'] for r in db.execute('PRAGMA table_info(accounts)')}:db.execute('ALTER TABLE accounts ADD COLUMN paused INTEGER NOT NULL DEFAULT 0')
            if 'error' not in {r['name'] for r in db.execute('PRAGMA table_info(posts)')}:
                db.execute("ALTER TABLE posts ADD COLUMN error TEXT NOT NULL DEFAULT ''")
                db.execute('ALTER TABLE posts ADD COLUMN last_attempt TEXT')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        try:yield db;db.commit()
        except Exception:db.rollback();raise
        finally:db.close()
    def account(self,db,aid):
        row=db.execute('SELECT * FROM accounts WHERE id=?',(aid,)).fetchone()
        if not row:raise HTTPException(404,'Аккаунт не найден')
        return dict(row)
    def measure(self,db,aid,payload,source):
        self.account(db,aid)
        if payload.post_id and not db.execute('SELECT 1 FROM posts WHERE id=? AND account_id=?',(payload.post_id,aid)).fetchone():raise HTTPException(404,'Публикация этого аккаунта не найдена')
        metrics=json.dumps(checked_metrics(payload.metrics),sort_keys=True);at=stamp(payload.observed_at)
        old=db.execute('SELECT metrics,precision,note FROM measurements WHERE account_id=? AND post_id=? AND observed_at=? AND source=? AND basis=?',(aid,payload.post_id,at,source,payload.basis)).fetchone()
        if old:
            if old['metrics']!=metrics or old['precision']!=payload.precision or old['note']!=payload.note:raise HTTPException(409,'Этот замер уже существует с другими значениями. Исправь дату или источник в файле.')
            return False
        db.execute('INSERT INTO measurements(account_id,post_id,observed_at,collected_at,source,precision,basis,metrics,note) VALUES (?,?,?,?,?,?,?,?,?)',(aid,payload.post_id,at,stamp(),source,payload.precision,payload.basis,metrics,payload.note))
        return True

def make_router(store):
    data=AnalyticsStore(store.root);router=APIRouter(prefix='/api/analytics')
    from .publication_network import router as network_router
    router.include_router(network_router(data))
    from .creator_analytics import router as creator_router
    router.include_router(creator_router(data))
    @router.get('/config')
    def config():return {'metrics':METRICS,'grafana_url':'http://127.0.0.1:18210/d/studio-social/studio-social?orgId=1&kiosk','sources':['public_page','youtube_api','manual','csv','tiktok_api']}
    @router.get('/accounts')
    def accounts(franchise_id:str=''):
        with data.db() as db:
            rows=[dict(row) for row in db.execute('SELECT * FROM accounts WHERE (?=\'\' OR franchise_id=?) ORDER BY rowid',(franchise_id,franchise_id))]
            for row in rows:
                last=db.execute("SELECT * FROM measurements WHERE account_id=? AND post_id='' AND basis='cumulative' ORDER BY observed_at DESC,id DESC LIMIT 1",(row['id'],)).fetchone()
                row['latest']={**dict(last),'metrics':json.loads(last['metrics'])} if last else None
            return rows
    @router.post('/accounts',status_code=201)
    def create_account(payload:AccountInput):
        with sqlite3.connect(store.root/'franchises.sqlite3') as db:
            if not db.execute('SELECT 1 FROM franchises WHERE id=?',(payload.franchise_id,)).fetchone():raise HTTPException(404,'Франшиза не найдена')
        try:platform,url,handle=social_url(payload.url)
        except ValueError as e:raise HTTPException(422,str(e))
        ident=str(uuid.uuid4())
        with data.db() as db:
            if db.execute('SELECT 1 FROM accounts WHERE url=?' if platform=='youtube' and handle.startswith('UC') else 'SELECT 1 FROM accounts WHERE lower(url)=lower(?)',(url,)).fetchone():raise HTTPException(409,'Этот аккаунт уже добавлен')
            try:db.execute('INSERT INTO accounts(id,franchise_id,platform,url,handle,label) VALUES (?,?,?,?,?,?)',(ident,payload.franchise_id,platform,url,handle,payload.label or handle))
            except sqlite3.IntegrityError:raise HTTPException(409,'Этот аккаунт уже добавлен. Открой его в существующей франшизе.')
            return data.account(db,ident)
    @router.patch('/accounts/{aid}')
    def account_state(aid:str,payload:AccountState):
        with data.db() as db:
            data.account(db,aid);db.execute('UPDATE accounts SET paused=? WHERE id=?',(int(payload.paused),aid))
        return {'paused':payload.paused}
    @router.post('/accounts/{aid}/measurements')
    def manual(aid:str,payload:Measurement):
        with data.db() as db:return {'inserted':data.measure(db,aid,payload,'manual')}
    @router.post('/accounts/{aid}/sync')
    async def sync(aid:str,post_id:str=''):
        with data.db() as db:
            account=data.account(db,aid)
            if post_id:
                post=db.execute('SELECT * FROM posts WHERE id=? AND account_id=?',(post_id,aid)).fetchone()
                if not post:raise HTTPException(404,'Публикация не найдена')
                account={**account,'url':post['url'],'handle':post['handle']}
        try:
            from .tiktok_analytics import CREDENTIALS, collect_account
            if account['platform']=='tiktok' and (CREDENTIALS / ('tiktok-'+account['handle'].lstrip('@')+'.json')).is_file():
                if post_id:
                    with data.db() as db:account=data.account(db,aid)
                return await asyncio.to_thread(collect_account,data,account)
            metrics,precision,source=await collect_public(account,bool(post_id))
            with data.db() as db:
                data.measure(db,aid,Measurement(metrics=metrics,post_id=post_id,precision=precision),source)
                if post_id:db.execute("UPDATE posts SET last_attempt=?,error='' WHERE id=?",(stamp(),post_id))
                else:db.execute("UPDATE accounts SET status='ok',last_attempt=?,error='' WHERE id=?",(stamp(),aid))
            return {'metrics':metrics,'source':source,'precision':precision}
        except Exception as e:
            if isinstance(e,HTTPException):raise
            message=str(e) if isinstance(e,ValueError) else 'Не удалось прочитать публичную страницу. Проверь сеть или используй импорт.'
            with data.db() as db:
                if post_id:db.execute('UPDATE posts SET last_attempt=?,error=? WHERE id=?',(stamp(),message,post_id))
                else:db.execute("UPDATE accounts SET status='unavailable',last_attempt=?,error=? WHERE id=?",(stamp(),message,aid))
            raise HTTPException(422,message)
    @router.get('/accounts/{aid}/posts')
    def posts(aid:str):
        with data.db() as db:
            data.account(db,aid);result=[]
            for row in db.execute('SELECT * FROM posts WHERE account_id=? ORDER BY rowid DESC',(aid,)):
                latest=db.execute("SELECT * FROM measurements WHERE post_id=? AND basis='cumulative' ORDER BY observed_at DESC,id DESC LIMIT 1",(row['id'],)).fetchone()
                result.append({**dict(row),**json.loads(row['metadata']),'latest':{**dict(latest),'metrics':json.loads(latest['metrics'])} if latest else None})
            return result
    @router.post('/accounts/{aid}/posts',status_code=201)
    def create_post(aid:str,payload:PostInput):
        try:platform,url,handle=social_url(payload.url,True)
        except ValueError as e:raise HTTPException(422,str(e))
        ident=str(uuid.uuid4());meta=payload.model_dump(exclude={'url'})
        if meta['published_at']:meta['published_at']=stamp(meta['published_at'])
        with data.db() as db:
            account=data.account(db,aid)
            if account['platform']!=platform:raise HTTPException(422,'Публикация должна относиться к платформе аккаунта')
            if platform=='tiktok' and not url.casefold().startswith(account['url'].casefold()+'/video/'):raise HTTPException(422,'Это публикация другого владельца TikTok')
            meta['ownership']='url_verified' if platform=='tiktok' else 'user_assigned'
            try:db.execute('INSERT INTO posts(id,account_id,url,handle,metadata) VALUES (?,?,?,?,?)',(ident,aid,url,handle,json.dumps(meta,ensure_ascii=False)))
            except sqlite3.IntegrityError:raise HTTPException(409,'Публикация уже добавлена')
        return {'id':ident,**meta,'url':url}
    @router.post('/accounts/{aid}/csv')
    def import_csv(aid:str,payload:CSVInput):
        if not payload.text.strip():raise HTTPException(422,'CSV пуст')
        reader=csv.DictReader(io.StringIO(payload.text.lstrip('\ufeff')));rows=[]
        with data.db() as db:
            data.account(db,aid)
            post_urls={row['url']:row['id'] for row in db.execute('SELECT id,url FROM posts WHERE account_id=?',(aid,))}
        try:
            for row in reader:
                if len(rows)>=5000:raise HTTPException(422,'До 5000 строк за один импорт')
                if not row.get('observed_at'):raise HTTPException(422,'Нужен столбец observed_at')
                post_id=row.get('post_id') or ''
                if row.get('post_url'):
                    try:_,url,_=social_url(row['post_url'],True)
                    except ValueError as e:raise HTTPException(422,str(e))
                    if url not in post_urls:raise HTTPException(422,'Сначала добавь публикацию этого аккаунта: '+url)
                    post_id=post_urls[url]
                metrics={key:float(row[key]) for key in METRICS if (row.get(key) or '').strip()}
                rows.append(Measurement(metrics=checked_metrics(metrics),observed_at=stamp(row['observed_at']),post_id=post_id,basis=row.get('basis') or 'cumulative',precision=row.get('precision') or 'exact',note=row.get('note','')))
        except (ValueError,TypeError) as e:raise HTTPException(422,'Неверное число или формат CSV: '+str(e))
        with data.db() as db:
            data.account(db,aid)
            for row in rows:
                if row.post_id and not db.execute('SELECT 1 FROM posts WHERE id=? AND account_id=?',(row.post_id,aid)).fetchone():raise HTTPException(422,'Неизвестный post_id в CSV')
            inserted=sum(data.measure(db,aid,row,'csv') for row in rows) if not payload.preview else 0
        return {'rows':len(rows),'inserted':inserted,'preview':[row.model_dump() for row in rows[:20]]}
    @router.get('/series')
    def series(franchise_id:str='',account_id:str='',metric:str='followers',scope:str='account',days:int=30,basis:str='cumulative',from_ms:int|None=None,to_ms:int|None=None):
        if metric not in METRICS or scope not in {'account','post'} or basis not in {'cumulative','period'} or not 1<=days<=3650:raise HTTPException(422,'Неверный фильтр')
        try:
            since=(datetime.fromtimestamp(from_ms/1000,timezone.utc) if from_ms is not None else datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
            until=(datetime.fromtimestamp(to_ms/1000,timezone.utc) if to_ms is not None else datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
        except (ValueError,OverflowError,OSError):raise HTTPException(422,'Неверный диапазон дат')
        with data.db() as db:
            rows=db.execute('''SELECT m.*,a.label,a.platform,a.franchise_id,a.handle,p.handle AS post_handle,p.metadata FROM measurements m JOIN accounts a ON a.id=m.account_id LEFT JOIN posts p ON p.id=m.post_id
            WHERE (?='' OR a.franchise_id=?) AND (?='' OR a.id=?) AND m.observed_at>=? AND m.observed_at<=? AND m.basis=? AND ((?='account' AND m.post_id='') OR (?='post' AND m.post_id<>'')) ORDER BY m.observed_at LIMIT 20000''',(franchise_id,franchise_id,account_id,account_id,since,until,basis,scope,scope)).fetchall()
            return [{'time':r['observed_at'],'value':json.loads(r['metrics']).get(metric),'label':r['label']+' · @'+r['handle'].lstrip('@')+((' · '+(json.loads(r['metadata']).get('title') or r['post_id'])+' · '+r['post_handle']) if r['post_id'] else '')+' · '+{'public_page':'публичная страница','youtube_api':'YouTube API','tiktok_api':'TikTok API','manual':'ручной замер','csv':'CSV'}[r['source']]+' · '+('округлено' if r['precision']=='rounded' else 'точно'),'source':r['source'],'precision':r['precision'],'account_id':r['account_id'],'post_id':r['post_id']} for r in rows if metric in json.loads(r['metrics'])]
    @router.get('/history/{aid}')
    def history(aid:str):
        with data.db() as db:
            data.account(db,aid)
            return [{**dict(row),'metrics':json.loads(row['metrics'])} for row in db.execute('SELECT * FROM measurements WHERE account_id=? ORDER BY observed_at DESC LIMIT 1000',(aid,))]
    @router.get('/export/{aid}')
    def export(aid:str):
        with data.db() as db:
            data.account(db,aid)
            return [{**dict(row),'metrics':json.loads(row['metrics'])} for row in db.execute('SELECT * FROM measurements WHERE account_id=? ORDER BY observed_at',(aid,))]
    from .analytics_dashboard import dashboard_router
    router.include_router(dashboard_router(data))
    return router

async def run_public_collector(root):
    """API snapshots every 15 minutes, capped exponential retry after failures."""
    data=AnalyticsStore(root)
    failures={}
    while True:
        await asyncio.sleep(60)
        with data.db() as db:
            candidates=[dict(row) for row in db.execute("SELECT * FROM accounts WHERE paused=0 AND status IN ('new','ok','unavailable')")]
        for account in candidates:
            try:
                at=datetime.now(timezone.utc)
                delay=timedelta(minutes=min(360,15*2**min(failures.get(account['id'],0),5)))
                if account['last_attempt'] and at-datetime.fromisoformat(account['last_attempt'])<delay:continue
                with data.db() as db:
                    # Claim before HTTP so concurrent collector instances cannot poll the same account.
                    claimed=db.execute("UPDATE accounts SET last_attempt=? WHERE id=? AND paused=0 AND (last_attempt IS NULL OR last_attempt<?)",(stamp(),account['id'],(at-delay).isoformat())).rowcount
                if not claimed:continue
                try:
                    from .tiktok_analytics import CREDENTIALS, collect_account
                    if account['platform']=='tiktok' and (CREDENTIALS/('tiktok-'+account['handle'].lstrip('@')+'.json')).is_file():
                        await asyncio.to_thread(collect_account,data,account)
                        failures.pop(account['id'],None)
                        continue
                    from .instagram_analytics import collect_account as collect_instagram
                    if account['platform']=='instagram' and (CREDENTIALS/('instagram-'+account['handle'].lstrip('@')+'.json')).is_file():
                        await asyncio.to_thread(collect_instagram,data,account)
                        failures.pop(account['id'],None)
                        continue
                    metrics,precision,source=await collect_public(account)
                    with data.db() as db:
                        data.measure(db,account['id'],Measurement(metrics=metrics,precision=precision),source)
                        db.execute("UPDATE accounts SET status='ok',error='' WHERE id=?",(account['id'],))
                except Exception as exc:
                    failures[account['id']]=failures.get(account['id'],0)+1
                    message=str(exc) if isinstance(exc,ValueError) else 'Сбор временно недоступен; повтор с увеличенной задержкой.'
                    with data.db() as db:db.execute("UPDATE accounts SET status='unavailable',error=? WHERE id=?",(message,account['id']))
            except Exception:
                # A transient local error must not terminate collection for other accounts.
                continue
