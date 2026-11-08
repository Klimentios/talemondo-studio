"""Official Instagram read-only ingestion into the existing analytics history."""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
import httpx

CREDENTIALS=Path.home()/'.config/talemondo-social'

def collect_account(store,account):
    from .analytics import Measurement,stamp
    path=CREDENTIALS/('instagram-'+account['handle'].lstrip('@')+'.json')
    credentials=json.loads(path.read_text())
    def request(client,route,params):
        try:
            r=client.get(route,params=params);body=r.json()
        except (httpx.HTTPError,ValueError):raise ValueError('Instagram API временно недоступен') from None
        if r.status_code!=200 or 'error' in body:raise ValueError('Instagram API отклонил запрос; проверь срок токена или разрешения')
        return body
    with httpx.Client(base_url='https://graph.instagram.com/v25.0/',headers={'Authorization':'Bearer '+credentials['access_token']},timeout=25,follow_redirects=False) as client:
        profile=request(client,'me',{'fields':'user_id,username,followers_count,media_count'})
        if profile.get('username')!=account['handle'].lstrip('@'):raise ValueError('Instagram вернул другой аккаунт')
        items=[];after=None;seen=set()
        for _ in range(100):
            params={'fields':'id,caption,media_type,permalink,timestamp,like_count,comments_count,thumbnail_url','limit':100}
            if after:params['after']=after
            page=request(client,str(credentials['instagram_user_id'])+'/media',params);items.extend(page.get('data',[]))
            if not page.get('paging',{}).get('next'):break
            after=page.get('paging',{}).get('cursors',{}).get('after')
            if not after or after in seen:raise ValueError('Instagram вернул неполную пагинацию')
            seen.add(after)
        else:raise ValueError('Архив Instagram слишком большой для одного сбора')
        from .audience_analytics import collect as collect_audience
        collect_audience(store,account,client,credentials['instagram_user_id'])
        records=[]
        for item in items:
            if item.get('media_type')!='VIDEO':continue
            metrics={k:item[field] for k,field in [('likes','like_count'),('comments','comments_count')] if item.get(field) is not None}
            try:
                insights=request(client,str(item['id'])+'/insights',{'metric':'views,reach,saved,shares'})
                for metric in insights.get('data',[]):
                    value=(metric.get('values') or [{}])[-1].get('value',metric.get('total_value',{}).get('value'))
                    if isinstance(value,(int,float)):metrics[{'saved':'saves'}.get(metric['name'],metric['name'])]=value
            except ValueError:pass
            try:
                watch=request(client,str(item['id'])+'/insights',{'metric':'ig_reels_avg_watch_time'})
                for metric in watch.get('data',[]):
                    value=(metric.get('values') or [{}])[-1].get('value')
                    if metric.get('name')=='ig_reels_avg_watch_time' and isinstance(value,(int,float)) and not isinstance(value,bool) and value>=0:
                        metrics['avg_watch_seconds']=value/1000
            except ValueError:pass
            records.append((item,metrics))
    observed=stamp()
    with store.db() as db:
        metrics={k:profile[field] for k,field in [('followers','followers_count'),('posts','media_count')] if profile.get(field) is not None}
        if metrics:store.measure(db,account['id'],Measurement(metrics=metrics,observed_at=observed,note='Official Instagram API'),'instagram_api')
        for item,metrics in records:
            url=item['permalink'].rstrip('/')
            post=db.execute('SELECT * FROM posts WHERE rtrim(url,\'/\')=?',(url,)).fetchone()
            if post and post['account_id']!=account['id']:raise ValueError('Instagram ролик связан с другим аккаунтом')
            pid=post['id'] if post else str(uuid.uuid4());meta=json.loads(post['metadata']) if post else {}
            meta.update({'title':item.get('caption','')[:500] or meta.get('title') or 'Ролик '+item['id'],'published_at':item['timestamp'],'thumbnail_url':item.get('thumbnail_url'),'ownership':'api_verified'})
            if post:db.execute('UPDATE posts SET metadata=? WHERE id=?',(json.dumps(meta,ensure_ascii=False),pid))
            else:db.execute('INSERT INTO posts(id,account_id,url,handle,metadata) VALUES(?,?,?,?,?)',(pid,account['id'],item['permalink'],item['id'],json.dumps(meta,ensure_ascii=False)))
            if metrics:store.measure(db,account['id'],Measurement(metrics=metrics,observed_at=observed,post_id=pid,note='Official Instagram API'),'instagram_api')
        db.execute("UPDATE accounts SET status='ok',error='',last_attempt=? WHERE id=?",(observed,account['id']))
    return {'account':account['handle'],'videos':len(records)}
