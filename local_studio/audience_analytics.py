"""Official audience breakdown snapshots, including empty/denied responses."""
import json
from datetime import datetime, timezone, timedelta

def initialize(store):
    with store.db() as db:
        db.execute('CREATE TABLE IF NOT EXISTS audience_observations(id INTEGER PRIMARY KEY,account_id TEXT NOT NULL,metric TEXT NOT NULL,dimension TEXT NOT NULL,observed_at TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,reason TEXT NOT NULL)')
        db.execute('CREATE INDEX IF NOT EXISTS audience_account_time ON audience_observations(account_id,observed_at)')

def collect(store,account,client,user_id,force=False):
    initialize(store)
    with store.db() as db:
        latest=db.execute('SELECT observed_at FROM audience_observations WHERE account_id=? ORDER BY id DESC LIMIT 1',(account['id'],)).fetchone()
    if latest and not force and datetime.now(timezone.utc)-datetime.fromisoformat(latest['observed_at'])<timedelta(hours=6):return
    observed=datetime.now(timezone.utc).isoformat();records=[]
    for metric in ['follower_demographics','reached_audience_demographics','engaged_audience_demographics']:
        for dimension in ['country','age','gender']:
            try:
                response=client.get(str(user_id)+'/insights',params={'metric':metric,'period':'lifetime','metric_type':'total_value','breakdown':dimension,'timeframe':'last_30_days'})
                body=response.json()
                if response.status_code!=200 or body.get('error'):
                    status='denied';reason='Instagram не разрешил этот запрос; код '+str(body.get('error',{}).get('code',response.status_code));payload=[]
                else:
                    payload=body.get('data',[])
                    # A metric envelope without categories is not a measured zero.
                    has_values=any(b.get('results') for item in payload for b in item.get('total_value',{}).get('breakdowns',[]))
                    status='available' if has_values else 'empty'
                    reason='' if has_values else 'Instagram принял запрос (HTTP 200), но не вернул категории аудитории. Причина и порог доступности в ответе не указаны.'
            except Exception:
                status='error';reason='Не удалось получить ответ Instagram; повтор при следующей проверке.';payload=[]
            records.append((account['id'],metric,dimension,observed,status,json.dumps(payload,ensure_ascii=False),reason))
    with store.db() as db:db.executemany('INSERT INTO audience_observations(account_id,metric,dimension,observed_at,status,payload,reason) VALUES(?,?,?,?,?,?,?)',records)

def read(store):
    initialize(store)
    with store.db() as db:
        rows=[dict(r) for r in db.execute('SELECT * FROM audience_observations ORDER BY id')]
    latest={}
    for row in rows:
        key=(row['account_id'],row['metric'],row['dimension'])
        old=latest.get(key)
        # Preserve the last actual dataset alongside the latest attempt state.
        row['categories']=[]
        for item in json.loads(row.pop('payload')):
            for breakdown in item.get('total_value',{}).get('breakdowns',[]):
                for result in breakdown.get('results',[]):
                    value=result.get('value')
                    if isinstance(value,(int,float)) and not isinstance(value,bool):row['categories'].append({'category':' / '.join(result.get('dimension_values',[])),'value':value})
        row['data_observed_at']=row['observed_at'] if row['categories'] else None
        if not row['categories'] and old and old['categories']:
            row['categories']=old['categories'];row['data_observed_at']=old['data_observed_at']
        latest[key]=row
    return list(latest.values())
