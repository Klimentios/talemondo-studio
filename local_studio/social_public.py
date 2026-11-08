"""Read public page data only. No cookies, login, CAPTCHA bypass or private APIs."""
import html
import json
import os
import re
from html.parser import HTMLParser
from urllib.parse import urlparse, parse_qs
import httpx

HOSTS = {'youtube.com':'youtube','www.youtube.com':'youtube','m.youtube.com':'youtube','youtu.be':'youtube',
         'tiktok.com':'tiktok','www.tiktok.com':'tiktok','instagram.com':'instagram','www.instagram.com':'instagram'}

def social_url(value, post=False):
    parsed=urlparse(value.strip())
    platform=HOSTS.get(parsed.hostname)
    if parsed.scheme != 'https' or not platform or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Нужна HTTPS-ссылка YouTube, TikTok или Instagram без дополнительных портов.')
    path=parsed.path.rstrip('/')
    if platform=='youtube':
        if post:
            ident=parsed.path.strip('/') if parsed.hostname=='youtu.be' else parse_qs(parsed.query).get('v',[''])[0] if path=='/watch' else path.split('/')[-1] if path.startswith('/shorts/') else ''
            if not re.fullmatch(r'[\w-]{11}',ident):raise ValueError('Нужна ссылка на конкретный ролик YouTube.')
            return platform,f'https://www.youtube.com/watch?v={ident}',ident
        if not re.fullmatch(r'/(@[\w.\-\u0080-\uffff]+|channel/UC[\w-]+)',path):raise ValueError('Нужна ссылка YouTube /@имя или /channel/UC…')
        return platform,'https://www.youtube.com'+path,path.split('/')[-1]
    if platform=='tiktok':
        pattern=r'/@([\w.]+)/video/(\d+)' if post else r'/@([\w.]+)'
        match=re.fullmatch(pattern,path)
        if not match:raise ValueError('Нужна полная ссылка TikTok на аккаунт или публикацию.')
        return platform,'https://www.tiktok.com'+path,match.group(2) if post else match.group(1)
    pattern=r'/(?:p|reel)/([\w-]+)' if post else r'/([\w.]+)'
    match=re.fullmatch(pattern,path)
    if not match or (not post and match.group(1) in {'accounts','explore','reels','p','reel','direct'}):raise ValueError('Нужна ссылка Instagram на аккаунт или публикацию.')
    return platform,'https://www.instagram.com'+path+'/',match.group(1)

class PageData(HTMLParser):
    def __init__(self):
        super().__init__();self.scripts=[];self.description='';self._script=False;self._text=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script':self._script=True;self._text=[]
        if tag=='meta' and (attrs.get('property')=='og:description' or attrs.get('name')=='description'):self.description=attrs.get('content','')
    def handle_data(self,data):
        if self._script:self._text.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self._script:self.scripts.append(''.join(self._text));self._script=False

def walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)

def count(value):
    text=value.replace(',','').replace(' ','').lower()
    match=re.fullmatch(r'(\d+(?:\.\d+)?)([kmb]?)',text)
    if not match:return None
    return int(float(match[1])*{'':1,'k':1000,'m':1000000,'b':1000000000}[match[2]])

def extract_public(platform,body,handle,post=False):
    page=PageData();page.feed(body);metrics={};precision='exact'
    docs=[]
    for script in page.scripts:
        try:docs.append(json.loads(script))
        except (ValueError,TypeError):
            match=re.search(r'(?:var )?ytInitial(?:Data|PlayerResponse)\s*=\s*(\{.*\})\s*;',script,re.S)
            if match:
                try:docs.append(json.loads(match[1]))
                except ValueError:pass
    if platform=='tiktok':
        for doc in docs:
            for obj in walk(doc):
                if not post and isinstance(obj.get('userInfo'),dict):
                    info=obj['userInfo']
                    if info.get('user',{}).get('uniqueId','').casefold()!=handle.casefold():continue
                    stats=info.get('stats',{})
                    mapping={'followers':'followerCount','following':'followingCount','likes':'heartCount','posts':'videoCount'}
                elif post and str(obj.get('id'))==handle and isinstance(obj.get('stats'),dict):
                    stats=obj['stats'];mapping={'views':'playCount','likes':'diggCount','comments':'commentCount','shares':'shareCount','saves':'collectCount'}
                else:continue
                metrics={key:int(stats[field]) for key,field in mapping.items() if isinstance(stats.get(field),(int,str)) and str(stats[field]).isdigit()}
                if metrics:return metrics,precision
    if platform=='youtube':
        for doc in docs:
            for obj in walk(doc if post else doc.get('header', {}) if isinstance(doc,dict) else {}):
                if post and isinstance(obj.get('videoDetails'),dict) and obj['videoDetails'].get('videoId')==handle:
                    value=obj['videoDetails'].get('viewCount')
                    if str(value).isdigit():metrics['views']=int(value)
                if not post:
                    for text in [obj.get('simpleText',''),obj.get('text','')]:
                        if not isinstance(text,str):continue
                        for key,label in [('followers','subscribers'),('posts','videos'),('views','views')]:
                            match=re.search(r'([\d,.]+\s*[KMB]?)\s+'+label,text,re.I)
                            if match and count(match[1]) is not None:metrics[key]=count(match[1]);precision='rounded'
    if platform=='instagram':
        labels=[('likes','likes'),('comments','comments')] if post else [('followers','followers'),('following','following'),('posts','posts')]
        for key,label in labels:
            match=re.search(r'([\d,.]+\s*[KMB]?)\s+'+label,page.description,re.I)
            if match and count(match[1]) is not None:metrics[key]=count(match[1]);precision='rounded' if re.search('[KMB.]',match[1],re.I) else precision
    if not metrics:raise ValueError('Публичная страница не отдала статистику. Возможно, требуется вход или площадка ограничила доступ. Используй CSV из кабинета или ручной замер.')
    return metrics,precision

async def collect_public(account,post=False):
    async with httpx.AsyncClient(timeout=20,follow_redirects=False,headers={'User-Agent':'Mozilla/5.0','Accept-Language':'en-US,en;q=0.9'}) as client:
        key=os.environ.get('H3_YOUTUBE_API_KEY')
        if account['platform']=='youtube' and key:
            endpoint='videos' if post else 'channels';handle=account['handle']
            params={'part':'statistics','key':key,'id':handle} if post or handle.startswith('UC') else {'part':'statistics','key':key,'forHandle':handle}
            response=await client.get('https://www.googleapis.com/youtube/v3/'+endpoint,params=params)
            if response.status_code!=200:raise ValueError('YouTube API не вернул данные. Проверь ключ и квоту в Google Cloud.')
            items=response.json().get('items',[])
            if not items:raise ValueError('Канал или ролик YouTube не найден.')
            stats=items[0].get('statistics',{});mapping={'views':'viewCount','likes':'likeCount','comments':'commentCount'} if post else {'followers':'subscriberCount','views':'viewCount','posts':'videoCount'}
            metrics={k:int(stats[v]) for k,v in mapping.items() if str(stats.get(v,'')).isdigit()}
            return metrics,'rounded' if not post else 'exact','youtube_api'
        response=await client.get(account['url'])
        if response.status_code!=200:raise ValueError(f'Площадка ограничила публичное чтение (HTTP {response.status_code}). Можно импортировать CSV или внести замер.')
        if len(response.content)>8*1024*1024:raise ValueError('Публичная страница превышает допустимый размер.')
        metrics,precision=extract_public(account['platform'],response.text,account['handle'],post)
        return metrics,precision,'public_page'
