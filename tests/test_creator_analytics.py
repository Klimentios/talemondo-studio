import asyncio
import json
from datetime import datetime,timezone,timedelta
from fastapi import FastAPI
import httpx
from local_studio.analytics import AnalyticsStore,Measurement
from local_studio.creator_analytics import router


def test_creator_rates_missing_baseline_and_source_isolation(tmp_path):
    store=AnalyticsStore(tmp_path);now=datetime.now(timezone.utc)
    with store.db() as db:
        db.execute("INSERT INTO accounts(id,franchise_id,platform,url,handle,label,status) VALUES('a','f','tiktok','https://www.tiktok.com/@adam_vega','adam_vega','Adam','ok')")
        db.execute('INSERT INTO posts(id,account_id,url,handle,metadata) VALUES(?,?,?,?,?)',('p','a','https://www.tiktok.com/@adam_vega/video/1','1',json.dumps({'title':'Real video','published_at':(now-timedelta(days=2)).isoformat()})))
        for hours,views in [(2,100),(1,160),(0,280)]:
            store.measure(db,'a',Measurement(post_id='p',observed_at=(now-timedelta(hours=hours)).isoformat(),metrics={'views':float(views)}),'tiktok_api')
        store.measure(db,'a',Measurement(metrics={'followers':12.0}),'tiktok_api')
        store.measure(db,'a',Measurement(metrics={'followers':112.0}),'public_page')
    app=FastAPI();app.include_router(router(store))
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            data=(await client.get('/creator')).json();a=data['accounts'][0];p=data['posts'][0]
            assert a['followers']==12
            assert a['follower_delta'] is None
            assert a['windows']['1'] is None
            assert p['vph']==120 and p['vpm']==2
            assert p['growth']==75 and p['acceleration']==2
            assert p['state']=='Ускоряется'
            assert 'saves' not in p['metrics']
    asyncio.run(check())
