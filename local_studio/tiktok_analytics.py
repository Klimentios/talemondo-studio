"""Read-only TikTok Display API collection. Credentials stay in user configuration."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx

CREDENTIALS = Path.home() / '.config/talemondo-social'


def collect_account(store, account):
    from .analytics import Measurement, stamp
    handle=account['handle'].lstrip('@')
    path=CREDENTIALS / ('tiktok-'+handle+'.json')
    if not path.is_file():return None
    from .tiktok_tokens import active_credentials
    credentials=active_credentials(path)
    if credentials.get('username') != handle:raise ValueError('Имя в файле доступа не совпадает с аккаунтом')
    def request(client, route, fields, payload=None):
        try:
            response=client.request('POST' if payload is not None else 'GET',route,params={'fields':fields},json=payload)
            body=response.json()
        except (httpx.HTTPError,ValueError):raise ValueError('TikTok API недоступен; повторите позже') from None
        if response.status_code!=200 or body.get('error',{}).get('code')!='ok':
            code=body.get('error',{}).get('code','request_failed')
            raise ValueError('TikTok API отклонил запрос: '+str(code)[:80])
        return body.get('data',{})
    with httpx.Client(base_url='https://open.tiktokapis.com',headers={'Authorization':'Bearer '+credentials['access_token']},timeout=25,follow_redirects=False) as client:
        profile=request(client,'/v2/user/info/','open_id,display_name,follower_count,following_count,likes_count,video_count')['user']
        if profile.get('open_id')!=credentials.get('open_id'):raise ValueError('TikTok вернул другой аккаунт')
        videos=[];cursor=None;seen=set()
        for _ in range(1000):
            payload={'max_count':20}
            if cursor is not None:payload['cursor']=cursor
            page=request(client,'/v2/video/list/','id,title,video_description,create_time,duration,cover_image_url,share_url,view_count,like_count,comment_count,share_count',payload)
            videos.extend(page.get('videos',[]))
            if not page.get('has_more'):break
            cursor=page.get('cursor')
            if cursor is None or cursor in seen:raise ValueError('TikTok повторил курсор; неполный сбор не сохранён')
            seen.add(cursor)
        else:raise ValueError('Слишком большой архив; неполный сбор не сохранён')
    observed=stamp();count=0
    with store.db() as db:
        values={key:profile[field] for key,field in [('followers','follower_count'),('following','following_count'),('likes','likes_count'),('posts','video_count')] if profile.get(field) is not None}
        store.measure(db,account['id'],Measurement(metrics=values,observed_at=observed,note='TikTok Display API · user.info.stats'), 'tiktok_api')
        for video in videos:
            vid=str(video['id']);url='https://www.tiktok.com/@'+handle+'/video/'+vid
            published=datetime.fromtimestamp(video['create_time'],timezone.utc).isoformat()
            meta={'title':(video.get('title') or video.get('video_description') or 'Ролик '+vid)[:200],'published_at':published,'duration':video.get('duration'),'thumbnail_url':video.get('cover_image_url'),'format':'TikTok video','ownership':'api_verified'}
            post=db.execute('SELECT id,account_id FROM posts WHERE url=?',(url,)).fetchone()
            if post and post['account_id']!=account['id']:raise ValueError('Ролик привязан к другому аккаунту')
            pid=post['id'] if post else str(uuid.uuid4())
            if post:db.execute('UPDATE posts SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),pid))
            else:db.execute('INSERT INTO posts(id,account_id,url,handle,metadata) VALUES(?,?,?,?,?)',(pid,account['id'],url,vid,json.dumps(meta,ensure_ascii=False)))
            metrics={key:video[field] for key,field in [('views','view_count'),('likes','like_count'),('comments','comment_count'),('shares','share_count')] if video.get(field) is not None}
            if metrics:store.measure(db,account['id'],Measurement(metrics=metrics,observed_at=observed,post_id=pid,note='TikTok Display API · video.list'),'tiktok_api')
            count+=1
        db.execute("UPDATE accounts SET status='ok',error='',last_attempt=? WHERE id=?",(observed,account['id']))
    return {'account':handle,'videos':count,'followers':profile.get('follower_count'),'observed_at':observed}


def sync_all(root):
    from .analytics import AnalyticsStore
    store=AnalyticsStore(root)
    with store.db() as db:accounts=[dict(r) for r in db.execute("SELECT * FROM accounts WHERE platform='tiktok'")]
    result=[]
    for account in accounts:
        try:
            result.append(collect_account(store,account))
        except Exception as exc:
            # Never include exception bodies or request headers in logs.
            message=str(exc) if isinstance(exc,ValueError) else 'Ошибка сбора TikTok API'
            result.append({'account':account['handle'],'error':message})
    return result
