"""Creator UI projection over attributed, saved API observations."""
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter

def local_zone_name() -> str:
    """IANA name of this Mac's time zone (H3_TIMEZONE overrides it); UTC when it cannot be told."""
    import os
    name = os.environ.get('H3_TIMEZONE', '').strip()
    if not name:
        target = os.path.realpath('/etc/localtime')
        name = target.split('zoneinfo/', 1)[1] if 'zoneinfo/' in target else 'UTC'
    try:
        ZoneInfo(name)
    except Exception:
        name = 'UTC'
    return name


TZ_NAME = local_zone_name()
TZ = ZoneInfo(TZ_NAME)
def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00'))
def official(row):return row['source'] in ('tiktok_api','instagram_api') or 'instagram_api:' in row['note']

def router(store):
    routes=APIRouter()
    @routes.get('/creator')
    def creator():
        now=datetime.now(timezone.utc)
        with store.db() as db:
            accounts=[dict(r) for r in db.execute("SELECT * FROM accounts WHERE platform IN ('tiktok','instagram')")]
            posts=[dict(r) for r in db.execute('SELECT * FROM posts')]
            rows=[dict(r) for r in db.execute("SELECT * FROM measurements WHERE basis='cumulative' ORDER BY observed_at,id") if official(r)]
        grouped={}
        for r in rows:
            r['metrics']=json.loads(r['metrics']);grouped.setdefault((r['account_id'],r['post_id']),[]).append(r)
        def latest(series):
            result={}
            for row in series:result.update({k:v for k,v in row['metrics'].items() if v is not None})
            return result
        videos=[]
        for p in posts:
            obs=grouped.get((p['account_id'],p['id']),[])
            if not obs:continue
            meta=json.loads(p['metadata']);values=latest(obs)
            samples=[{'time':r['observed_at'],**r['metrics']} for r in obs]
            unique=[]
            for sample in samples:
                if not unique or sample['time']!=unique[-1]['time']:unique.append(sample)
                else:unique[-1].update(sample)
            speed=None;growth=None;delta=None;acceleration=None;state='Недостаточно замеров'
            view_samples=[r for r in unique if r.get('views') is not None]
            if len(view_samples)>=2:
                a,b=view_samples[-2:];hours=(dt(b['time'])-dt(a['time'])).total_seconds()/3600
                if hours>0:
                    delta=b['views']-a['views'];speed=round(delta/hours,2)
                    growth=round(delta/a['views']*100,2) if a['views']>0 else None
                    state='Стабильно'
                    if delta<0:state='Коррекция источника';speed=None
                    elif len(view_samples)>=3:
                        prev=view_samples[-3];h=(dt(a['time'])-dt(prev['time'])).total_seconds()/3600
                        old=(a['views']-prev['views'])/h if h>0 else 0
                        if old>0:
                            acceleration=round(speed/old,2)
                            state='Ускоряется' if acceleration>=1.5 else 'Замедляется' if acceleration<.67 else 'Стабильно'
            videos.append({'id':p['id'],'account_id':p['account_id'],'url':p['url'],'title':meta.get('title') or 'Публикация '+p['handle'],'published_at':meta.get('published_at'),'thumbnail':meta.get('thumbnail_url'),'duration':meta.get('duration'),'metrics':values,'updated_at':view_samples[-1]['time'] if view_samples else obs[-1]['observed_at'],'metric_observed_at':{k:next(r['observed_at'] for r in reversed(obs) if k in r['metrics']) for k in values},'samples':unique,'vph':speed,'vpm':round(speed/60,2) if speed is not None else None,'growth':growth,'delta':delta,'acceleration':acceleration,'state':state})
        summaries=[]
        for a in accounts:
            obs=grouped.get((a['id'],''),[]);values=latest(obs);owned=[p for p in videos if p['account_id']==a['id']]
            name='@'+str(a.get('handle') or '').lstrip('@')
            cutoff=now-timedelta(hours=24)
            baseline=[r for r in obs if dt(r['observed_at'])<=cutoff and 'followers' in r['metrics']]
            follower_delta=values.get('followers',0)-baseline[-1]['metrics']['followers'] if baseline and (cutoff-dt(baseline[-1]['observed_at'])).total_seconds()<=1800 and 'followers' in values else None
            totals={k:sum(p['metrics'][k] for p in owned if k in p['metrics']) if any(k in p['metrics'] for p in owned) else None for k in ['views','likes','comments','shares','saves','reach']}
            windows={}
            for days in [1,7,30]:
                total=0;complete=bool(owned)
                for p in owned:
                    if 'views' not in p['metrics']:complete=False;continue
                    old=[r for r in p['samples'] if dt(r['time'])<=now-timedelta(days=days) and 'views' in r]
                    if old and (now-timedelta(days=days)-dt(old[-1]['time'])).total_seconds()<=1800:total+=p['metrics']['views']-old[-1]['views']
                    elif p['published_at'] and dt(p['published_at'])>=now-timedelta(days=days):total+=p['metrics']['views']
                    else:complete=False
                windows[str(days)]=total if complete else None
            updated=obs[-1]['observed_at'] if obs else max((p['updated_at'] for p in owned),default=None)
            summaries.append({'id':a['id'],'name':name,'platform':a['platform'],'handle':a['handle'],'url':a['url'],'status':a['status'],'error':a['error'],'updated_at':updated,'fresh':bool(updated and (now-dt(updated)).total_seconds()<1200),'followers':values.get('followers'),'follower_delta':follower_delta,'publications':values.get('posts'),'discovered':len(owned),'totals':totals,'windows':windows,'average_views':round(totals['views']/sum('views' in p['metrics'] for p in owned)) if totals['views'] is not None and owned else None,'follower_history':[{'time':r['observed_at'],'value':r['metrics']['followers']} for r in obs if 'followers' in r['metrics']]})
        from .audience_analytics import read as read_audience
        return {'audience':read_audience(store),'generated_at':now.isoformat(),'timezone':TZ_NAME,'accounts':sorted(summaries,key=lambda a:(a['name'],a['platform']!='tiktok')),'posts':videos,'capabilities':{'tiktok':{'views':True,'likes':True,'comments':True,'shares':True,'saves':False,'reach':False,'demographics':False},'instagram':{'views':True,'likes':True,'comments':True,'shares':True,'saves':True,'reach':True,'demographics':False}}}
    return routes
