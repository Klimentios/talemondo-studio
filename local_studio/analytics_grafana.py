"""Provisioned native Grafana social reporting, no synthetic metrics."""
from .creator_analytics import TZ_NAME

def build_dashboard(base):
    ds={'type':'yesoreyeram-infinity-datasource','uid':'studio-history'}
    panels=[]
    def panel(title,kind,view,columns,x,y,w,h,options=None,custom=None):
        cols=[{'selector':k,'text':k,'type':t} for k,t in columns]
        target={'refId':'A','type':'json','source':'url','parser':'backend','format':'table','url':base+'/api/analytics/dashboard/'+view+'?franchise_id=${franchise:percentencode}&account_id=${account:percentencode}&from_ms=${__from}&to_ms=${__to}','root_selector':'','columns':cols,'url_options':{'method':'GET','headers':[]}}
        defaults={'color':{'mode':'palette-classic'},'noValue':'—','decimals':0,'custom':custom or {}}
        panels.append({'id':len(panels)+1,'title':title,'type':kind,'gridPos':{'x':x,'y':y,'w':w,'h':h},'datasource':ds,'targets':[target],'fieldConfig':{'defaults':defaults,'overrides':[]},'options':options or {}})
        if kind=='timeseries':
            group=next((k for k,t in columns if t=='string'),None)
            if group:panels[-1]['transformations']=[{'id':'partitionByValues','options':{'fields':[group]}}]
        if kind=='table':
            panels[-1]['transformations']=[{'id':'organize','options':{'indexByName':{k:i for i,(k,t) in enumerate(columns)}}}]
            for k,t in columns:
                if k=='ER по просмотрам, %':panels[-1]['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':k},'properties':[{'id':'decimals','value':2},{'id':'unit','value':'percent'}]})
                if k=='Ролик':panels[-1]['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':k},'properties':[{'id':'custom.width','value':320}]})
                if k=='Ссылка':panels[-1]['fieldConfig']['overrides'].append({'matcher':{'id':'byName','options':k},'properties':[{'id':'links','value':[{'title':'Открыть ролик','url':'${__value.raw}','targetBlank':True}]}]})
    panel('Аудитория каналов','barchart','accounts',[('Канал','string'),('Подписчики','number')],0,0,12,7,{'orientation':'horizontal','showValue':'always','legend':{'showLegend':False},'tooltip':{'mode':'single'},'xField':'Канал','barWidth':.55})
    panel('Аккаунты · состояние данных','table','accounts',[(k,t) for k,t in [('Канал','string'),('Аккаунт','string'),('Подписчики','number'),('Публикации','number'),('Последний замер','timestamp'),('Статус','string')]],12,0,12,7,{'showHeader':True,'cellHeight':'md','sortBy':[{'displayName':'Канал','desc':False}]})
    line={'drawStyle':'line','lineInterpolation':'linear','lineWidth':2,'fillOpacity':6,'gradientMode':'opacity','showPoints':'auto','pointSize':3,'spanNulls':False,'axisBorderShow':False}
    panel('Подписчики · с момента подключения API','timeseries','followers',[('time','timestamp'),('Подписчики','number'),('Канал','string')],0,8,16,9,{'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull']},'tooltip':{'mode':'multi'}},line)
    panel('Прирост подписчиков','timeseries','followers',[('time','timestamp'),('Изменение','number'),('Канал','string')],16,8,8,9,{'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull']},'tooltip':{'mode':'multi'}},{**line,'drawStyle':'bars','fillOpacity':80})
    panel('Ролики · просмотры и вовлечённость','table','posts',[(k,t) for k,t in [('Канал','string'),('Ролик','string'),('Дата','string'),('Просмотры','number'),('Охват','number'),('Лайки','number'),('Комментарии','number'),('Репосты','number'),('Сохранения','number'),('ER по просмотрам, %','number'),('Замер','timestamp'),('Ссылка','string')]],0,17,24,13,{'showHeader':True,'cellHeight':'md','sortBy':[{'displayName':'Просмотры','desc':True}]})
    panel('Топ-10 роликов · просмотры','barchart','top_views',[('Ролик','string'),('Просмотры','number')],0,30,12,12,{'orientation':'horizontal','showValue':'always','legend':{'showLegend':False},'xField':'Ролик','barWidth':.55,'yTickLabelMaxLength':55,'xTickLabelSpacing':100})
    panel('Топ-10 роликов · реакции','barchart','top_reactions',[('Ролик','string'),('Лайки','number'),('Комментарии','number'),('Репосты','number'),('Сохранения','number')],12,30,12,12,{'orientation':'horizontal','stacking':'normal','showValue':'never','yTickLabelMaxLength':55,'legend':{'showLegend':True,'placement':'bottom'},'xField':'Ролик'})
    panel('Когда опубликовано · просмотры роликов','timeseries','posts',[('Опубликован','timestamp'),('Просмотры','number'),('Ролик','string')],0,42,24,9,{'legend':{'showLegend':False},'tooltip':{'mode':'multi'}},{**line,'drawStyle':'points','pointSize':8,'fillOpacity':0})
    panel('Как растут просмотры роликов · история замеров','timeseries','video_history',[('time','timestamp'),('Просмотры','number'),('Ролик','string')],0,51,24,10,{'legend':{'displayMode':'table','placement':'bottom','calcs':['lastNotNull']},'tooltip':{'mode':'multi'}},line)
    panel('Время публикации · 10 окон с наибольшей медианой просмотров','barchart','posting_time',[('Окно публикации','string'),('Медиана просмотров','number')],0,61,24,12,{'orientation':'horizontal','showValue':'always','legend':{'showLegend':False},'xField':'Окно публикации','barWidth':.55})
    panels[-1]['description']=f'Часовой пояс: {TZ_NAME}. n — число роликов. Накопительные просмотры зависят от возраста ролика и контента; малые выборки не доказывают лучшее время. Фильтруйте аккаунт и период.'
    panel('Регионы публикации · только зафиксированные при отправке','barchart','publication_regions',[('Регион','string'),('Медиана просмотров','number'),('Роликов','number')],0,73,14,8,{'orientation':'horizontal','legend':{'showLegend':True},'xField':'Регион'})
    panels[-1]['description']='До записи соединения при отправке данных нет. Геолокация IP приблизительная; журнал Mac не доказывает регион публикации с другого устройства.'
    panel('Текущее соединение Mac','table','connection',[('Страна','string'),('Регион','string'),('Замер','timestamp'),('Источник','string')],14,73,10,8,{'showHeader':True})

    panels.append({'id':99,'type':'text','title':'Источники и свежесть','gridPos':{'x':0,'y':81,'w':24,'h':4},'options':{'mode':'markdown','content':'**Период сверху фильтрует ролики по дате публикации, а историю аудитории — по дате замера.** Просмотры, охват и реакции — накопительные значения на дату последнего замера, а не просмотры, полученные за выбранный период. Изменение подписчиков считается между соседними замерами одного источника; первая точка без базы не имеет прироста. Пустые значения означают, что площадка не передала показатель. Подключённые каналы показывают официальные API-замеры; старые публичные наблюдения сохранены отдельно и не соединены с API в графике роста. TikTok: просмотры, лайки, комментарии, репосты; охват и сохранения этим API не передаются. История роста начинается с подключения API.'}})
    import json
    from urllib.request import urlopen
    from urllib.error import URLError
    try:
        with urlopen(base+'/api/analytics/accounts',timeout=10) as response: accounts=json.load(response)
        with urlopen(base+'/api/franchises',timeout=10) as response: franchises=json.load(response)
    except (URLError, ValueError):
        accounts=[]; franchises=[]
    names={f['id']:f['name'] for f in franchises}
    variables=[]
    for name,label,choices in [('franchise','Франшиза',[(names.get(i,i),i) for i in dict.fromkeys(a['franchise_id'] for a in accounts if a['platform'] in ('instagram','tiktok'))]),('account','Аккаунт',[(a['platform']+' · @'+a['handle'].lstrip('@'),a['id']) for a in accounts if a['platform'] in ('instagram','tiktok')])]:
        options=[{'text':'Все','value':'__all','selected':True}]+[{'text':n,'value':i,'selected':False} for n,i in choices]
        variables.append({'name':name,'type':'custom','label':label,'hide':0,'query':','.join(o['text']+' : '+o['value'] for o in options),'options':options,'current':{'text':'Все','value':'__all'}})
    return {'uid':'studio-social','title':'TaleMondo · Социальная аналитика','schemaVersion':39,'version':3,'editable':False,'tags':['TaleMondo'],'timezone':'browser','refresh':'5m','time':{'from':'now-30d','to':'now'},'templating':{'list':variables},'panels':panels,'description':'Реальные сохранённые показатели аккаунтов и публикаций.'}
