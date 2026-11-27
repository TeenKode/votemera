"""Read-only Yandex Forms REST adapter. Persist aggregates, never respondent data."""
import json,re,time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit,urlencode
from datetime import datetime
HOST='api.forms.yandex.net'
BASE='https://'+HOST
COLORS=['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4']
class FormsError(Exception): pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
def form_id(value):
    if not re.fullmatch(r'[a-fA-F0-9]{24}',value): raise FormsError('Некорректный ID формы')
    return value.lower()
def organization_header(org):
    """Yandex 360 uses numeric IDs; Identity Hub uses opaque alphanumeric IDs."""
    if re.fullmatch(r'[0-9]{1,32}',org):return 'X-Org-Id'
    if re.fullmatch(r'[A-Za-z0-9_-]{3,64}',org) and re.search(r'[A-Za-z]',org):return 'X-Cloud-Org-Id'
    raise FormsError('Укажите числовой ID Яндекс 360 или ID Yandex Identity Hub (например, bpf…) без названия заголовка')

def request(org,token,path,params=None):
    if not path.startswith('/v1/'):raise FormsError('Некорректный путь API')
    u=urlsplit(path)
    if u.netloc or u.scheme or u.fragment:raise FormsError('Некорректный путь API')
    url=BASE+path+('?' + urlencode(params) if params else '')
    try:
        req=Request(url,headers={'Authorization':'OAuth '+token,organization_header(org):org,'Accept':'application/json','User-Agent':'Votemera'})
        with build_opener(NoRedirect()).open(req,timeout=12) as res:
            raw=res.read(8*1024*1024+1)
            if len(raw)>8*1024*1024:raise FormsError('Слишком большой ответ API')
            result=json.loads(raw)
            if not isinstance(result,dict):raise FormsError('Некорректный ответ API')
            return result
    except HTTPError as e:
        messages={400:'Яндекс отклонил параметры запроса; проверьте ID организации',401:'OAuth-токен не принят или не соответствует организации',403:'Нет прав на форму или ответы',404:'Форма не найдена',422:'Яндекс не принял параметры запроса; проверьте тип и ID организации',429:'Лимит запросов Яндекса'}
        raise FormsError(messages.get(e.code,'Ошибка API Яндекса: HTTP '+str(e.code))) from None
    except (URLError,OSError,ValueError):raise FormsError('Яндекс Формы недоступны или вернули неверный JSON') from None
def list_forms(org,token,search='',offset=0):
    data=request(org,token,'/v1/surveys',{'limit':50,'offset':offset,'name':search})
    rows=data.get('result')
    if not isinstance(rows,list):raise FormsError('Нет списка форм в ответе API')
    return [{'id':form_id(x['id']),'name':str(x.get('name') or x['id'])[:120],'published':bool(x.get('is_published'))} for x in rows]
def questions(org,token,sid):
    data=request(org,token,'/v1/surveys/'+form_id(sid)+'/questions')
    result=[]
    for page in data.get('pages',[]):
        for q in page.get('items',[]):
            if not isinstance(q,dict) or q.get('hidden'):continue
            kind=q.get('type'); widget=q.get('widget','radio')
            items=q.get('items') or []
            labels=[str(x.get('label','')) for x in items if isinstance(x,dict)]
            if kind=='boolean': labels=['Да','Нет']
            supported=kind=='boolean' or (kind=='enum' and not q.get('data_source') and not q.get('modify_choices') and 2<=len(labels)<=6 and len(set(labels))==len(labels) and all(0<len(x)<=80 and x.strip() for x in labels))
            result.append({'id':int(q['id']),'label':str(q.get('label') or 'Вопрос')[:300],'kind':kind,'multi':widget=='checkbox' and not any(v.get('type')=='single' for v in q.get('validators',[]) if isinstance(v,dict)),'supported':supported,'options':[{'label':x,'color':COLORS[i]} for i,x in enumerate(labels)] if supported else []})
    return result
def validate_date(value):
    if not value:return ''
    try:
        d=datetime.fromisoformat(value.replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError()
        return d.isoformat()
    except ValueError:raise FormsError('Укажите дату и время с часовым поясом')
def aggregate(org,token,sid,spec,date_from='',date_to=''):
    path='/v1/surveys/'+form_id(sid)+'/answers'
    params={'format':'default','use_slugs':'false','ordering':'asc','page_size':50,'questions':','.join(str(q['id']) for q in spec)}
    if date_from:params['date_from']=date_from
    if date_to:params['date_to']=date_to
    counts=[{'counts':[0]*len(q['options']),'total':0} for q in spec]
    seen=set();pages=set();columns=None;start=time.monotonic()
    for _ in range(400):
        if time.monotonic()-start>120:raise FormsError('Превышено время синхронизации: сузьте период')
        data=request(org,token,path,params);params=None
        if data.get('columns') is not None:
            columns=[str(col.get('id')) for col in data['columns']]
        if columns is None or any(str(q['id']) not in columns for q in spec):raise FormsError('Структура формы изменилась: создайте новое мероприятие')
        rows=data.get('answers')
        if not isinstance(rows,list):raise FormsError('Некорректный список ответов')
        for row in rows:
            rid=row.get('id')
            if not isinstance(rid,int):raise FormsError('Некорректный ID ответа')
            if rid in seen:continue
            seen.add(rid)
            values=row.get('data')
            if not isinstance(values,list) or len(values)!=len(columns):raise FormsError('Структура ответов изменилась')
            for i,q in enumerate(spec):
                item=values[columns.index(str(q['id']))]
                if not isinstance(item,dict) or item.get('value') is None:continue
                value=item['value']
                if q['kind']=='boolean' and isinstance(value,bool): value=['Да' if value else 'Нет']
                if not isinstance(value,list):value=[value]
                if not value:continue
                labels=[o['label'] for o in q['options']]
                indices=set()
                for v in value:
                    if isinstance(v,dict):v=v.get('label',v.get('text'))
                    if not isinstance(v,str) or v not in labels:raise FormsError('Варианты ответа изменились или не поддерживаются')
                    indices.add(labels.index(v))
                if len(indices)>1 and not q['multi']:raise FormsError('Обнаружен множественный ответ в одиночном вопросе')
                counts[i]['total']+=1
                for j in indices:counts[i]['counts'][j]+=1
        nxt=(data.get('next') or {}).get('next_url')
        if not nxt:return {'total':len(seen),'results':counts}
        u=urlsplit(nxt)
        expected='/v1/surveys/'+form_id(sid)+'/answers'
        if u.scheme or u.netloc or u.fragment or u.path not in (expected,expected.removeprefix('/v1')):raise FormsError('Некорректная ссылка следующей страницы')
        nxt=expected+('?' + u.query if u.query else '')
        if nxt in pages:raise FormsError('Повтор страницы ответов')
        pages.add(nxt);path=nxt
    raise FormsError('Более 20 000 ответов: сузьте период')
