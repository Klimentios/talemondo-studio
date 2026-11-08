"""Read-only dashboard views over saved, attributed social observations."""
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .creator_analytics import TZ
from fastapi import APIRouter


def dashboard_router(data):
    router = APIRouter()

    @router.get('/dashboard/{view}')
    def report(view: str, franchise_id: str = '', account_id: str = '', platform: str = '', from_ms: int = 0, to_ms: int = 0):
        franchise_id = '' if franchise_id in ('__all', 'Все :', '$__all') else franchise_id
        account_id = '' if account_id in ('__all', 'Все :', '$__all') else account_id
        end = to_ms / 1000 if to_ms else datetime.now(timezone.utc).timestamp()
        start = from_ms / 1000 if from_ms else end - 30 * 86400
        with data.db() as db:
            accounts = [dict(r) for r in db.execute("SELECT * FROM accounts WHERE platform IN ('instagram','tiktok') AND (?='' OR franchise_id=?) AND (?='' OR id=?) AND (?='' OR platform=?)", (franchise_id, franchise_id, account_id, account_id, platform, platform))]
            ids = {a['id']: a for a in accounts}
            rows = [dict(r) for r in db.execute("SELECT * FROM measurements WHERE basis='cumulative' ORDER BY observed_at,id") if r['account_id'] in ids and datetime.fromisoformat(r['observed_at']).timestamp() <= end]
            posts = {r['id']: dict(r) for r in db.execute('SELECT * FROM posts') if r['account_id'] in ids}
        def label(a):
            return '@' + str(a.get('handle') or '').lstrip('@') + ' · ' + ('Instagram' if a['platform']=='instagram' else 'TikTok')
        # Official series are authoritative when connected; legacy observations
        # remain in the full history/export and are never joined to API growth.
        def official(r):return r['source'] in ('tiktok_api','instagram_api') or 'instagram_api:' in r['note']
        official_accounts={r['account_id'] for r in rows if not r['post_id'] and official(r)}
        official_posts={r['post_id'] for r in rows if r['post_id'] and official(r)}
        rows=[r for r in rows if official(r) or (r['account_id'] not in official_accounts if not r['post_id'] else r['post_id'] not in official_posts)]
        latest = {}
        for r in rows:
            r['values'] = json.loads(r['metrics'])
            # Per metric: partial public snapshots must not erase imported API views.
            for key,value in r['values'].items():
                latest[(r['account_id'],r['post_id'],key)] = (value,r)
        if view in ('posts', 'top_views', 'top_reactions', 'posting_time'):
            result=[]
            for pid,p in posts.items():
                meta=json.loads(p['metadata']);published=meta.get('published_at')
                if published and not start <= datetime.fromisoformat(published.replace('Z','+00:00')).timestamp() <= end: continue
                values={k:v for (aid,post,k),(v,r) in latest.items() if post==pid}
                obs=[r for (aid,post,k),(v,r) in latest.items() if post==pid]
                interactions=sum(values.get(k,0) for k in ('likes','comments','shares','saves'))
                result.append({'Канал':label(ids[p['account_id']]),'Ролик':meta.get('title') or p['handle'],'Опубликован':published,'Дата':datetime.fromisoformat(published.replace('Z','+00:00')).astimezone(TZ).strftime('%d.%m.%Y %H:%M') if published else 'Не указана','Просмотры':values.get('views'),'Охват':values.get('reach'),'Лайки':values.get('likes'),'Комментарии':values.get('comments'),'Репосты':values.get('shares'),'Сохранения':values.get('saves'),'ER по просмотрам, %':round(interactions/values['views']*100,2) if values.get('views') else None,'Ссылка':p['url'],'Замер':max((r['observed_at'] for r in obs),default=None)})
            result=sorted(result,key=lambda p:p.get('Просмотры') or -1,reverse=True)
            if view=='posting_time':
                from statistics import median
                buckets={}
                days=['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
                for item in result:
                    if not item['Опубликован'] or item['Просмотры'] is None:continue
                    at=datetime.fromisoformat(item['Опубликован'].replace('Z','+00:00')).astimezone(TZ)
                    key=(at.weekday(),at.hour)
                    buckets.setdefault(key,[]).append(item['Просмотры'])
                return [{'Окно публикации':f'{days[day]} · {hour:02d}:00 · n={len(values)}','Медиана просмотров':median(values),'Роликов':len(values)} for (day,hour),values in sorted(buckets.items(),key=lambda pair:median(pair[1]),reverse=True)[:10]]
            if view.startswith('top_'):

                if view=='top_reactions':result=sorted(result,key=lambda p:sum(p.get(k) or 0 for k in ('Лайки','Комментарии','Репосты','Сохранения')),reverse=True)
                result=result[:10]
                for rank,item in enumerate(result,1):
                    title=re.sub(r'#[\w]+', '', item['Ролик']).strip()
                    title=re.sub(r'\s+', ' ',title) or 'Ролик'
                    title=title[:22].rstrip()+('…' if len(title)>22 else '')
                    platform='TT' if 'TikTok' in item['Канал'] else 'IG'
                    item['Ролик']=f"{rank:02d} · {platform} · {item['Дата'][:5]} · {title}"
            return result
        if view=='publication_regions':
            with data.db() as db:
                links={r['post_id']:r['country'] for r in db.execute('SELECT l.post_id,n.country FROM publication_network_links l JOIN publication_network n ON n.id=l.sample_id')}
            from statistics import median
            groups={}
            for pid,p in posts.items():
                published=json.loads(p['metadata']).get('published_at')
                if not published or not start<=datetime.fromisoformat(published.replace('Z','+00:00')).timestamp()<=end:continue
                value=latest.get((p['account_id'],pid,'views'))
                if pid in links and value:groups.setdefault(links[pid],[]).append(value[0])
            return [{'Регион':region,'Роликов':len(values),'Медиана просмотров':median(values)} for region,values in groups.items()]
        if view=='connection':
            with data.db() as db:
                row=db.execute('SELECT country,region,observed_at FROM publication_network ORDER BY id DESC LIMIT 1').fetchone()
            return [{'Страна':row['country'],'Регион':row['region'],'Замер':row['observed_at'],'Источник':'Соединение Mac; не регион старых публикаций'}] if row else []
        if view == 'video_history':
            result=[]
            for r in rows:
                if not r['post_id'] or r['post_id'] not in posts:continue
                if not start <= datetime.fromisoformat(r['observed_at']).timestamp() <= end:continue
                meta=json.loads(posts[r['post_id']]['metadata'])
                if 'views' in r['values']:result.append({'time':r['observed_at'],'Просмотры':r['values']['views'],'Ролик':label(ids[r['account_id']])+' · '+(meta.get('title') or r['post_id'])[:90] })
            return result
        if view == 'followers':
            # Keep source/precision separate: source changes are not growth.
            series={}
            for r in rows:
                if r['post_id'] or 'followers' not in r['values']:continue
                source='Instagram API' if 'instagram_api:' in r['note'] else r['source']
                key=(r['account_id'],source,r['precision'])
                series.setdefault(key,[]).append(r)
            result=[]
            for (aid,source,precision),items in series.items():
                previous=None
                for r in items:
                    value=r['values']['followers'];at=datetime.fromisoformat(r['observed_at']).timestamp()
                    if start<=at<=end:result.append({'time':r['observed_at'],'Подписчики':value,'Изменение':None if previous is None else value-previous,'Канал':label(ids[aid]),'Точность':precision,'Источник':source})
                    previous=value
            return result
        if view == 'accounts':
            result=[]
            for aid,a in ids.items():
                metrics={k:v for (ident,pid,k),(v,r) in latest.items() if ident==aid and not pid}
                observations=[r for (ident,pid,k),(v,r) in latest.items() if ident==aid and not pid]
                result.append({'Канал':label(a),'Аккаунт':'@'+a['handle'].lstrip('@'),'Подписчики':metrics.get('followers'),'Публикации':metrics.get('posts'),'Последний замер':max((r['observed_at'] for r in observations),default=None),'Статус':a['status']})
            return result
        return []
    return router
