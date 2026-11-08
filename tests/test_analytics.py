import asyncio
from types import SimpleNamespace
from fastapi import FastAPI
import httpx
from local_studio.franchises import make_router as franchises
from local_studio.analytics import make_router
from local_studio.social_public import social_url,extract_public

def test_social_links_and_public_embedded_statistics():
    import pytest
    assert social_url('https://www.youtube.com/@Adam-vega')[2]=='@Adam-vega'
    for url in ['http://localhost/','https://www.youtube.com.evil.test/@x','https://www.youtube.com:444/@x','https://www.instagram.com/accounts/login/']:
        with pytest.raises(ValueError):social_url(url)
    data='''<script type="application/json">{"userInfo":{"user":{"uniqueId":"vega"},"stats":{"followerCount":12,"heartCount":30}}}</script>'''
    assert extract_public('tiktok',data,'vega')[0]=={'followers':12,'likes':30}
    with pytest.raises(ValueError):extract_public('tiktok','<html>login required</html>','vega')

def test_analytics_history_dedup_csv_isolation_and_missing_metrics(tmp_path):
    app=FastAPI();store=SimpleNamespace(root=tmp_path);app.include_router(franchises(store));app.include_router(make_router(store))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as c:
            f=(await c.get('/api/franchises')).json()
            a=(await c.post('/api/analytics/accounts',json={'franchise_id':f[0]['id'],'url':'https://www.youtube.com/@test'})).json()['id']
            path=f'/api/analytics/accounts/{a}'
            payload={'metrics':{'followers':10},'observed_at':'2026-10-10T12:00:00Z'}
            assert (await c.post(path+'/measurements',json=payload)).json()['inserted']
            assert not (await c.post(path+'/measurements',json=payload)).json()['inserted']
            assert (await c.post(path+'/measurements',json={**payload,'metrics':{'followers':11}})).status_code==409
            assert (await c.post(path+'/measurements',json={'metrics':{'views':-1}})).status_code==422
            assert (await c.post(path+'/measurements',json={'metrics':{'views':True}})).status_code==422
            assert (await c.post(path+'/measurements',json={**payload,'precision':'rounded'})).status_code==409
            assert (await c.get('/api/analytics/series',params={'metric':'views','days':3650})).json()==[]
            assert (await c.get('/api/analytics/series',params={'franchise_id':f[1]['id'],'days':3650})).json()==[]
            csv={'text':'observed_at,followers,views\n2026-10-11T12:00:00Z,20,30\n','preview':True}
            assert (await c.post(path+'/csv',json=csv)).json()['rows']==1
            assert len((await c.get('/api/analytics/history/'+a)).json())==1
            csv['preview']=False
            assert (await c.post(path+'/csv',json=csv)).json()['inserted']==1
            assert (await c.post(path+'/csv',json=csv)).json()['inserted']==0
            bad={**csv,'text':csv['text']+'wrong-date,10,20\n'}
            assert (await c.post(path+'/csv',json=bad)).status_code==422
            assert len((await c.get('/api/analytics/history/'+a)).json())==2
    asyncio.run(exercise())
