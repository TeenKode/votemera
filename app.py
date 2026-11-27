import asyncio
import csv
import hmac
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import subprocess
import threading
from collections import OrderedDict
from contextlib import contextmanager, nullcontext, closing
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / 'VERSION').read_text().strip()
DB = Path(os.getenv('VOTEMERA_DB', str(ROOT / 'data' / 'votemera.db')))
ADMIN = os.getenv('VOTEMERA_ADMIN_PASSWORD', '')
TERMINAL = os.getenv('VOTEMERA_TERMINAL_KEY', '')
UPDATE_STATUS = Path('/var/lib/votemera-update/status')
UPDATE_REQUEST = Path('/var/lib/votemera/update-request')
UPDATE_REMOTE = 'https://github.com/TeenKode/votemera.git'
AUTH_FAILURES = OrderedDict()
AUTH_LOCK = threading.Lock()
TERMINALS = OrderedDict()
TERMINALS_LOCK = threading.Lock()
WRITE_LOCK = threading.RLock()
MAX_BACKUP_BYTES = 64 * 1024 * 1024
COLORS = ['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4']
DEFAULTS = dict(brand='Votemera', subtitle='Интерактивное голосование', theme='dark', accent='#43c8bd', cooldown=1800, show_percent=True, show_count=True, chart='stack', thank_you='Спасибо за ваш голос!', display_page_size=4, display_rotate=12, vote_layout='auto', motion='full', idle_timeout=90)

@contextmanager
def db(write=False, transaction=True):
    with (WRITE_LOCK if write else nullcontext()):
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if transaction: c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()

def init():
    with db(transaction=False) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, active_event INTEGER, active_round INTEGER, opened INTEGER NOT NULL DEFAULT 0, epoch TEXT NOT NULL DEFAULT '', display TEXT NOT NULL DEFAULT 'live');
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL, options TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, name TEXT NOT NULL, position INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS votes(id INTEGER PRIMARY KEY, round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE, choice INTEGER NOT NULL, nonce TEXT NOT NULL UNIQUE, terminal TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS votes_round ON votes(round_id,choice);
        CREATE INDEX IF NOT EXISTS votes_terminal ON votes(terminal,created);
        ''')
        if 'questions' not in [r['name'] for r in c.execute('PRAGMA table_info(events)')]:
            c.execute('ALTER TABLE events ADD COLUMN questions TEXT')
        if 'question_index' not in [r['name'] for r in c.execute('PRAGMA table_info(votes)')]:
            c.execute('ALTER TABLE votes ADD COLUMN question_index INTEGER NOT NULL DEFAULT 0')
        if 'ballot_id' not in [r['name'] for r in c.execute('PRAGMA table_info(votes)')]:
            # Recover exact v0.2/v0.3 links from deterministic nonces, never from time proximity.
            c.execute('BEGIN IMMEDIATE')
            c.execute('ALTER TABLE votes ADD COLUMN ballot_id TEXT')
            for v in c.execute('SELECT * FROM votes WHERE question_index=0').fetchall():
                c.execute('UPDATE votes SET ballot_id=? WHERE id=?',(v['nonce'],v['id']))
                for i in range(1,12):
                    n=hashlib.sha256(('ballot:'+v['nonce']+':'+str(i)).encode()).hexdigest()[:32]
                    c.execute('UPDATE votes SET ballot_id=? WHERE nonce=? AND round_id=? AND question_index=? AND terminal=? AND created=?',(v['nonce'],n,v['round_id'],i,v['terminal'],v['created']))
            c.commit()
        c.execute('CREATE INDEX IF NOT EXISTS votes_ballot ON votes(round_id,ballot_id,question_index)')
        c.execute('CREATE TABLE IF NOT EXISTS analysis_config(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE, first_q INTEGER NOT NULL, second_q INTEGER NOT NULL, chart TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS presentation_config(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,data TEXT NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS votes_question ON votes(round_id,question_index,choice)')
        c.execute('INSERT OR IGNORE INTO settings(id,data) VALUES(1,?)',(json.dumps(DEFAULTS),))
        if not c.execute('SELECT 1 FROM events LIMIT 1').fetchone():
            e=c.execute('INSERT INTO events(title,question,options,created) VALUES(?,?,?,?)',('Педагогический совет','Готовы ли вы использовать искусственный интеллект в профессиональной деятельности?',json.dumps([{'label':'Да','color':COLORS[0]},{'label':'Нет','color':COLORS[1]}]),time.time())).lastrowid
            r=c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'До педсовета',0)).lastrowid
            c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'После педсовета',1))
            c.execute('UPDATE settings SET active_event=?,active_round=? WHERE id=1 AND active_event IS NULL',(e,r))

def fail(status,msg): raise HTTPException(status,msg)
def secret_eq(a,b): return hmac.compare_digest(a.encode(),b.encode())
def admin(request: Request, authorization: str = Header(default='')):
    if not ADMIN: fail(503,'Пароль администратора не настроен на сервере')
    address=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with AUTH_LOCK:
        n,until=AUTH_FAILURES.get(address,(0,0))
        if until<=now: n=0; until=now+60
        if n>=10: fail(429,'Слишком много попыток входа. Повторите через минуту.')
        if not secret_eq(authorization,'Bearer '+ADMIN):
            AUTH_FAILURES[address]=(n+1,until)
            AUTH_FAILURES.move_to_end(address)
            while len(AUTH_FAILURES)>4096: AUTH_FAILURES.popitem(last=False)
            fail(401,'Неверный пароль')
        AUTH_FAILURES.pop(address,None)
def terminal(x_terminal_key: str = Header(default='')):
    if TERMINAL and not secret_eq(x_terminal_key,TERMINAL): fail(403,'Неверный ключ терминала')

def clean(s):
    s=s.strip()
    if not s: fail(422,'Пустое значение')
    return s

class Option(BaseModel):
    label: str = Field(min_length=1,max_length=80)
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
class Condition(BaseModel):
    question: int = Field(ge=0,le=11)
    choice: int = Field(ge=0,le=5)
class Question(BaseModel):
    question: str = Field(min_length=1,max_length=300)
    options: list[Option] = Field(min_length=2,max_length=6)
    description: str = Field(default="",max_length=300)
    confirm: bool = False
    show_if: Condition | None = None
class Event(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    question: str | None = Field(default=None,min_length=1,max_length=300)
    options: list[Option] | None = Field(default=None,min_length=2,max_length=6)
    questions: list[Question] | None = Field(default=None,min_length=1,max_length=12)
    rounds: list[str] = Field(min_length=1,max_length=12)
class Settings(BaseModel):
    brand: str = Field(min_length=1,max_length=80)
    subtitle: str = Field(max_length=120)
    theme: Literal['dark','light']
    accent: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    cooldown: int = Field(ge=500,le=10000)
    show_percent: bool
    show_count: bool
    chart: Literal['stack','bars']
    thank_you: str = Field(min_length=1,max_length=120)
    display_page_size: Literal[1,2,4] = 4
    display_rotate: int = Field(default=12,ge=0,le=120)
    vote_layout: Literal["auto","grid","list"] = "auto"
    motion: Literal["full","subtle","off"] = "full"
    idle_timeout: int = Field(default=90,ge=0,le=600)
class Control(BaseModel):
    event: int
    round: int
    opened: bool
    display: Literal['live','compare','hidden','analysis','analysis_compare','presentation']
class AnalysisConfig(BaseModel):
    first: int = Field(ge=0,le=11)
    second: int = Field(ge=0,le=11)
    chart: Literal['stack','heatmap'] = 'stack'

class Slide(BaseModel):
    mode: Literal['live','compare','analysis','analysis_compare']
    question: int = Field(default=-1,ge=-1,le=11)
class Presentation(BaseModel):
    order: list[int] = Field(default_factory=list,max_length=12)
    slides: list[Slide] = Field(default_factory=list,max_length=24)
    seconds: int = Field(default=15,ge=5,le=120)

class TerminalHeartbeat(BaseModel):
    terminal: str = Field(pattern=r'^[a-f0-9]{32}$')

class Vote(BaseModel):
    round: int
    epoch: str = Field(max_length=64)
    choice: int = Field(ge=0,le=5)
    nonce: str = Field(pattern=r'^[a-f0-9]{32}$')
    terminal: str = Field(pattern=r'^[a-f0-9]{32}$')

class Ballot(BaseModel):
    round: int
    epoch: str = Field(max_length=64)
    choices: list[int | None] = Field(min_length=1,max_length=12)
    nonce: str = Field(pattern=r'^[a-f0-9]{32}$')
    terminal: str = Field(pattern=r'^[a-f0-9]{32}$')

def questions(e):
    return json.loads(e['questions']) if e['questions'] else [dict(question=e['question'],options=json.loads(e['options']))]

def validate_event(e):
    raw=e.questions if e.questions is not None else ([Question(question=e.question,options=e.options)] if e.question is not None and e.options is not None else [])
    if not raw: fail(422,'Добавьте хотя бы один вопрос')
    qs=[]
    for q in raw:
        opts=[dict(label=clean(o.label),color=o.color) for o in q.options]
        if len(set(o['label'].casefold() for o in opts))!=len(opts): fail(422,'Варианты ответа должны быть уникальными')
        item=dict(question=clean(q.question),options=opts,description=q.description.strip(),confirm=q.confirm)
        if q.show_if:
            if q.show_if.question>=len(qs) or q.show_if.choice>=len(qs[q.show_if.question]['options']): fail(422,'Условие должно ссылаться на вариант предыдущего вопроса')
            item['show_if']=q.show_if.model_dump()
        qs.append(item)
    names=[clean(n) for n in e.rounds]
    if any(len(n)>80 for n in names) or len(set(n.casefold() for n in names))!=len(names): fail(422,'Названия этапов должны быть уникальными и не длиннее 80 символов')
    return qs,names

def results(c,rid,qs):
    items=[dict(counts=[0]*len(q['options'])) for q in qs]
    for v in c.execute('SELECT question_index,choice,count(*) n FROM votes WHERE round_id=? GROUP BY question_index,choice',(rid,)):
        items[v['question_index']]['counts'][v['choice']]=v['n']
    for x in items: x['total']=sum(x['counts'])
    return items

def analysis_config(c,eid,qs):
    if len(qs)<2: return None
    row=c.execute('SELECT first_q,second_q,chart FROM analysis_config WHERE event_id=?',(eid,)).fetchone()
    if row and row['first_q']!=row['second_q'] and max(row['first_q'],row['second_q'])<len(qs):
        return dict(first=row['first_q'],second=row['second_q'],chart=row['chart'])
    return dict(first=0,second=1,chart='stack')

def cross_results(c,rid,qs,first,second):
    matrix=[[0]*len(qs[second]['options']) for _ in qs[first]['options']]
    for v in c.execute('''SELECT a.choice x,b.choice y,count(*) n FROM votes a JOIN votes b
        ON a.round_id=b.round_id AND a.ballot_id=b.ballot_id
        WHERE a.round_id=? AND a.question_index=? AND b.question_index=? AND a.ballot_id IS NOT NULL
        GROUP BY a.choice,b.choice''',(rid,first,second)):
        matrix[v['x']][v['y']]=v['n']
    totals=[sum(row) for row in matrix]
    submitted=c.execute('SELECT count(*) FROM votes WHERE round_id=? AND question_index=0',(rid,)).fetchone()[0]
    return dict(matrix=matrix,totals=totals,total=sum(totals),unpaired=max(0,submitted-sum(totals)))

def presentation_config(c,eid,qs):
    row=c.execute('SELECT data FROM presentation_config WHERE event_id=?',(eid,)).fetchone()
    return json.loads(row['data']) if row else dict(order=list(range(len(qs))),slides=[],seconds=15)

def snapshot(c, private=False, voting=False):
    s=c.execute('SELECT * FROM settings WHERE id=1').fetchone()
    cfg=DEFAULTS | json.loads(s['data'])
    e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
    result=dict(settings=cfg, event=None, round=s['active_round'], opened=bool(s['opened']), epoch=s['epoch'], display=s['display'], version=VERSION, terminal_key_required=bool(TERMINAL))
    if e:
        qs=questions(e); options=qs[0]['options']; rounds=[]; ac=analysis_config(c,e['id'],qs)
        for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(e['id'],)):
            item=dict(id=r['id'],name=r['name'])
            if private or (not voting and s['display']!='hidden' and (s['display'] in ('compare','analysis_compare','presentation') or r['id']==s['active_round'])):
                rs=results(c,r['id'],qs)
                item.update(counts=rs[0]['counts'],total=rs[0]['total'],results=rs)
                if ac and (private or s['display'] in ('analysis','analysis_compare','presentation')):
                    item['cross']=cross_results(c,r['id'],qs,ac['first'],ac['second'])
            rounds.append(item)
        result['event']=dict(id=e['id'],title=e['title'],question=e['question'],options=options,questions=qs,rounds=rounds)
        if private or (not voting and s['display'] in ('analysis','analysis_compare','presentation')): result['event']['analysis']=ac
        if not voting: result['event']['presentation']=presentation_config(c,e['id'],qs)
    if private:
        result['events']=[dict(x) for x in c.execute('SELECT id,title,question,created FROM events ORDER BY id DESC')]
    return result

app=FastAPI(title='Votemera',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.middleware('http')
async def headers(request, call_next):
    r=await call_next(request)
    r.headers['Cache-Control']='no-store'
    r.headers['X-Content-Type-Options']='nosniff'
    r.headers['X-Frame-Options']='DENY'
    r.headers['Referrer-Policy']='no-referrer'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return r
@app.get('/')
@app.get('/vote')
@app.get('/display')
@app.get('/admin')
def page(): return FileResponse(ROOT/'static'/'index.html')
@app.get('/health')
def health():
    with db() as c: c.execute('SELECT 1 FROM settings').fetchone()
    return {'status':'ok','version':VERSION}
@app.get('/api/state')
def state(view: Literal['vote','display']='display'):
    with db() as c: return snapshot(c,voting=view=='vote')
@app.get('/api/stream')
async def stream(request:Request, view: Literal['vote','display']='display'):
    async def events():
        last=''; beat=0
        while not await request.is_disconnected():
            def read():
                with db() as c: return json.dumps(snapshot(c,voting=view=='vote'),ensure_ascii=False,separators=(',',':'))
            data=await asyncio.to_thread(read)
            if data!=last:
                yield 'data: '+data+'\n\n'; last=data; beat=0
            elif beat>=30:
                yield ': heartbeat\n\n'; beat=0
            beat+=1
            await asyncio.sleep(.4)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
@app.post('/api/vote',dependencies=[Depends(terminal)])
def vote(v:Vote):
    with db(True) as c:
        old=c.execute('SELECT * FROM votes WHERE nonce=?',(v.nonce,)).fetchone()
        if old:
            if (old['round_id'],old['choice'],old['terminal']) != (v.round,v.choice,v.terminal): fail(409,'Конфликт идентификатора')
            return {'ok':True,'duplicate':True}
        s=c.execute('SELECT * FROM settings').fetchone()
        if not s['opened'] or s['active_round']!=v.round or s['epoch']!=v.epoch: fail(409,'Этап изменился или закрыт. Повторите выбор.')
        e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
        if e and len(questions(e))!=1: fail(409,'Для нескольких вопросов отправьте анкету целиком')
        if not e or v.choice>=len(questions(e)[0]['options']): fail(422,'Неизвестный вариант')
        now=time.time(); cfg=DEFAULTS|json.loads(s['data'])
        last=c.execute('SELECT max(created) FROM votes WHERE terminal=?',(v.terminal,)).fetchone()[0]
        if last is not None and now-last<cfg['cooldown']/1000: fail(429,'Терминал ещё не готов')
        c.execute('INSERT INTO votes(round_id,choice,nonce,terminal,created,ballot_id) VALUES(?,?,?,?,?,?)',(v.round,v.choice,v.nonce,v.terminal,now,v.nonce))
    return {'ok':True,'duplicate':False}
def valid_ballot(qs, choices):
    if len(qs)!=len(choices): return False
    for i,(q,n) in enumerate(zip(qs,choices)):
        rule=q.get('show_if')
        visible=not rule or choices[rule['question']]==rule['choice']
        if visible:
            if n is None or not 0<=n<len(q['options']): return False
        elif n is not None: return False
    return True

@app.post('/api/ballot',dependencies=[Depends(terminal)])
def ballot(v:Ballot):
    # First answer uses the original nonce; remaining answers use deterministic, domain-separated nonces.
    nonces=[v.nonce]+[hashlib.sha256(('ballot:'+v.nonce+':'+str(i)).encode()).hexdigest()[:32] for i in range(1,len(v.choices))]
    with db(True) as c:
        old=c.execute('SELECT * FROM votes WHERE nonce=?',(v.nonce,)).fetchone()
        if old:
            rows=c.execute('SELECT * FROM votes WHERE round_id=? AND ballot_id=?',(v.round,v.nonce)).fetchall()
            expected={(i,n,nonces[i],v.terminal) for i,n in enumerate(v.choices) if n is not None}
            if {(r['question_index'],r['choice'],r['nonce'],r['terminal']) for r in rows}!=expected: fail(409,'Конфликт идентификатора')
            e=c.execute('SELECT events.* FROM events JOIN rounds ON rounds.event_id=events.id WHERE rounds.id=?',(v.round,)).fetchone()
            if not e or not valid_ballot(questions(e),v.choices): fail(409,'Конфликт анкеты')
            return {'ok':True,'duplicate':True}
        s=c.execute('SELECT * FROM settings').fetchone()
        if not s['opened'] or s['active_round']!=v.round or s['epoch']!=v.epoch: fail(409,'Этап изменился или закрыт. Начните заново.')
        e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
        qs=questions(e)
        if not valid_ballot(qs,v.choices): fail(422,'Неверные ответы или пропуски анкеты')
        now=time.time(); cfg=DEFAULTS|json.loads(s['data'])
        last=c.execute('SELECT max(created) FROM votes WHERE terminal=?',(v.terminal,)).fetchone()[0]
        if last is not None and now-last<cfg['cooldown']/1000: fail(429,'Терминал ещё не готов')
        if any(c.execute('SELECT 1 FROM votes WHERE nonce=?',(n,)).fetchone() for n in nonces): fail(409,'Конфликт идентификатора')
        c.executemany('INSERT INTO votes(round_id,choice,nonce,terminal,created,question_index,ballot_id) VALUES(?,?,?,?,?,?,?)',[(v.round,n,nonces[i],v.terminal,now,i,v.nonce) for i,n in enumerate(v.choices) if n is not None])
    return {'ok':True,'duplicate':False}

@app.post('/api/terminal/heartbeat',dependencies=[Depends(terminal)])
def heartbeat(v:TerminalHeartbeat):
    now=time.monotonic()
    with TERMINALS_LOCK:
        TERMINALS[v.terminal]=now
        TERMINALS.move_to_end(v.terminal)
        while TERMINALS and (len(TERMINALS)>4096 or next(iter(TERMINALS.values()))<now-300):
            TERMINALS.popitem(last=False)
    return {'ok':True}

@app.get('/api/admin/terminals',dependencies=[Depends(admin)])
def terminal_status():
    now=time.monotonic()
    with TERMINALS_LOCK:
        rows=[dict(id=k[-8:],seconds_ago=int(now-t),online=now-t<25) for k,t in reversed(TERMINALS.items()) if now-t<300]
    return {'online':sum(r['online'] for r in rows),'terminals':rows}

@app.get('/api/admin/state',dependencies=[Depends(admin)])
def admin_state():
    with db() as c: return snapshot(c,private=True)
@app.post('/api/admin/event',dependencies=[Depends(admin)])
def new_event(e:Event):
    qs,names=validate_event(e)
    with db(True) as c:
        eid=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',(clean(e.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),time.time(),json.dumps(qs,ensure_ascii=False))).lastrowid
        ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(eid,n,i)).lastrowid for i,n in enumerate(names)]
    return {'id':eid,'round':ids[0]}
@app.get('/api/admin/event/{eid}',dependencies=[Depends(admin)])
def event_detail(eid:int):
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        return dict(id=eid,title=e['title'],question=e['question'],options=json.loads(e['options']),questions=questions(e),rounds=[dict(r) for r in c.execute('SELECT id,name FROM rounds WHERE event_id=? ORDER BY position',(eid,))])
@app.put('/api/admin/event/{eid}',dependencies=[Depends(admin)])
def edit_event(eid:int,e:Event):
    qs,names=validate_event(e)
    with db(True) as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone(): fail(404,'Не найдено')
        s=c.execute('SELECT * FROM settings').fetchone()
        if (s['active_event']==eid and s['opened']) or c.execute('SELECT 1 FROM votes JOIN rounds ON rounds.id=votes.round_id WHERE event_id=? LIMIT 1',(eid,)).fetchone(): fail(409,'Редактирование доступно только до начала голосования или после очистки всех этапов')
        c.execute('UPDATE events SET title=?,question=?,options=?,questions=? WHERE id=?',(clean(e.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),json.dumps(qs,ensure_ascii=False),eid))
        c.execute('DELETE FROM analysis_config WHERE event_id=?',(eid,))
        c.execute('DELETE FROM presentation_config WHERE event_id=?',(eid,))
        c.execute('DELETE FROM rounds WHERE event_id=?',(eid,)); ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(eid,n,i)).lastrowid for i,n in enumerate(names)]
        if s['active_event']==eid: c.execute('UPDATE settings SET active_round=?,epoch=? WHERE id=1',(ids[0],uuid4().hex))
    return {'id':eid,'round':ids[0]}
@app.post('/api/admin/control',dependencies=[Depends(admin)])
def control(v:Control):
    with db(True) as c:
        if v.opened and update_busy(): fail(409,'Дождитесь завершения обновления')
        if not c.execute('SELECT 1 FROM rounds WHERE id=? AND event_id=?',(v.round,v.event)).fetchone(): fail(422,'Неверный этап')
        s=c.execute('SELECT * FROM settings').fetchone()
        epoch=uuid4().hex if (s['active_event'],s['active_round'],bool(s['opened']))!=(v.event,v.round,v.opened) else s['epoch']
        c.execute('UPDATE settings SET active_event=?,active_round=?,opened=?,epoch=?,display=? WHERE id=1',(v.event,v.round,int(v.opened),epoch,v.display))
        return snapshot(c,private=True)
@app.put('/api/admin/analysis/{eid}',dependencies=[Depends(admin)])
def set_analysis(eid:int,v:AnalysisConfig):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        if v.first==v.second or max(v.first,v.second)>=len(questions(e)): fail(422,'Выберите два разных вопроса')
        c.execute('INSERT INTO analysis_config VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET first_q=excluded.first_q,second_q=excluded.second_q,chart=excluded.chart',(eid,v.first,v.second,v.chart))
    return {'ok':True}

@app.put('/api/admin/presentation/{eid}',dependencies=[Depends(admin)])
def set_presentation(eid:int,v:Presentation):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        n=len(questions(e))
        if len(set(v.order))!=len(v.order) or any(not 0<=i<n for i in v.order): fail(422,'Неверный порядок вопросов')
        if any(s.question>=n or (s.mode.startswith('analysis') and n<2) for s in v.slides): fail(422,'Неверный слайд')
        c.execute('INSERT INTO presentation_config VALUES(?,?) ON CONFLICT(event_id) DO UPDATE SET data=excluded.data',(eid,json.dumps(v.model_dump())))
    return {'ok':True}

@app.post('/api/admin/rehearsal/{eid}',dependencies=[Depends(admin)])
def rehearsal(eid:int):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        new=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',('[Репетиция] '+e['title'][:105],e['question'],e['options'],time.time(),e['questions'])).lastrowid
        ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(new,r['name'],r['position'])).lastrowid for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(eid,)).fetchall()]
        for name,columns in [('analysis_config','first_q,second_q,chart'),('presentation_config','data')]:
            c.execute(f'INSERT INTO {name}(event_id,{columns}) SELECT ?,{columns} FROM {name} WHERE event_id=?',(new,eid))
    return {'id':new,'round':ids[0]}

@app.put('/api/admin/settings',dependencies=[Depends(admin)])
def settings(v:Settings):
    with db(True) as c: c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(v.model_dump(),ensure_ascii=False),))
    return {'ok':True}
@app.delete('/api/admin/round/{rid}/votes',dependencies=[Depends(admin)])
def clear(rid:int):
    with db(True) as c:
        s=c.execute('SELECT * FROM settings').fetchone()
        if s['opened'] and s['active_round']==rid: fail(409,'Сначала закройте этап')
        if not c.execute('SELECT 1 FROM rounds WHERE id=?',(rid,)).fetchone(): fail(404,'Этап не найден')
        c.execute('DELETE FROM votes WHERE round_id=?',(rid,))
        if s['active_round']==rid: c.execute('UPDATE settings SET epoch=? WHERE id=1',(uuid4().hex,))
    return {'ok':True}
@app.get('/api/admin/export/{eid}',dependencies=[Depends(admin)])
def export(eid:int):
    def safe(s): return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else s
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        out=io.StringIO(); w=csv.writer(out,delimiter=';'); w.writerow(['Мероприятие','Вопрос','Этап','Вариант','Голоса','Доля (%)','Всего'])
        for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(eid,)):
            qs=questions(e)
            for q,res in zip(qs,results(c,r['id'],qs)):
                total=res['total']
                for o,n in zip(q['options'],res['counts']):
                    w.writerow([safe(e['title']),safe(q['question']),safe(r['name']),safe(o['label']),n,f'{n*100/total if total else 0:.2f}'.replace('.',','),total])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="votemera-{eid}.csv"'})

def update_status():
    try:
        return UPDATE_STATUS.read_text().strip()
    except OSError:
        return 'unavailable'

def update_busy():
    return UPDATE_REQUEST.exists() or update_status()=='running'

@app.get('/api/admin/update',dependencies=[Depends(admin)])
def update_info(check: bool=False):
    status=update_status()
    info=dict(version=VERSION,status='queued' if UPDATE_REQUEST.exists() else status,available=None,latest=None)
    if check and status!='unavailable' and not update_busy():
        try:
            local=subprocess.check_output(['git','-c',f'safe.directory={ROOT}','-C',str(ROOT),'rev-parse','HEAD'],text=True,timeout=5).strip()
            remote=subprocess.check_output(['git','-c','credential.helper=','ls-remote',UPDATE_REMOTE,'refs/heads/main'],text=True,timeout=12,env={**os.environ,'GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'/bin/false','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null'}).split()
            if not remote or not re.fullmatch(r'[a-f0-9]{40}',remote[0]): raise ValueError('Invalid remote ref')
            info.update(available=local!=remote[0],latest=remote[0][:10])
        except (OSError,subprocess.SubprocessError,ValueError):
            info['error']='Не удалось проверить GitHub. Проверьте подключение к интернету.'
    return info

@app.post('/api/admin/update',dependencies=[Depends(admin)])
def request_update():
    with db(True) as c:
        if update_status()=='unavailable': fail(503,'Модуль обновления не установлен. Запустите scripts/install.sh.')
        if c.execute('SELECT opened FROM settings WHERE id=1').fetchone()[0]: fail(409,'Сначала закройте голосование')
        if update_busy(): fail(409,'Обновление уже запущено')
        try:
            fd=os.open(UPDATE_REQUEST,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(fd)
        except FileExistsError: fail(409,'Обновление уже запущено')
        except OSError: fail(503,'Служба обновления недоступна')
    return {'ok':True}

@app.get('/api/admin/backup',dependencies=[Depends(admin)])
def backup_database():
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        target=Path(directory)/'backup.db'
        with db(transaction=False) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
        data=target.read_bytes()
    return Response(data,media_type='application/x-sqlite3',headers={'Content-Disposition':'attachment; filename="votemera-backup.db"'})

def restore_database(path):
    def schema(c):
        return set(tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    with WRITE_LOCK:
        with db() as current:
            if current.execute('SELECT opened FROM settings WHERE id=1').fetchone()[0]: fail(409,'Сначала закройте голосование')
            if update_busy(): fail(409,'Дождитесь завершения обновления')
            expected_schema=schema(current)
        try:
            with closing(sqlite3.connect(path,timeout=5)) as source:
                source.row_factory=sqlite3.Row
                source.execute('PRAGMA trusted_schema=OFF')
                source.execute('PRAGMA foreign_keys=ON')
                if schema(source)!=expected_schema: fail(422,'Несовместимая схема базы: используйте копию той же версии')
                if source.execute('PRAGMA quick_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchone(): fail(422,'Повреждённая база')
                if source.execute('SELECT count(*) FROM settings').fetchone()[0]!=1: fail(422,'Некорректные настройки')
                source.execute('UPDATE settings SET opened=0,epoch=?,display=? WHERE id=1',(uuid4().hex,'hidden'))
                source.commit()
                preview=snapshot(source,private=True)
                if not preview['event'] or not any(r['id']==preview['round'] for r in preview['event']['rounds']): fail(422,'Нет корректного активного мероприятия')
                Settings.model_validate(preview['settings'])
                for e in source.execute('SELECT * FROM events').fetchall():
                    qs=questions(e)
                    if not 1<=len(qs)<=12: fail(422,'Некорректная анкета')
                    for q in qs: Question.model_validate(q)
                    for r in source.execute('SELECT * FROM rounds WHERE event_id=?',(e['id'],)).fetchall():
                        for vote in source.execute('SELECT DISTINCT question_index,choice FROM votes WHERE round_id=?',(r['id'],)):
                            if not 0<=vote['question_index']<len(qs) or not 0<=vote['choice']<len(qs[vote['question_index']]['options']): fail(422,'Неверный индекс ответа')
                directory=DB.parent/'restore-backups'
                directory.mkdir(mode=0o700,exist_ok=True)
                recovery=directory/(uuid4().hex+'.db')
                with closing(sqlite3.connect(DB,timeout=10)) as live, closing(sqlite3.connect(recovery)) as previous:
                    live.backup(previous)
                    os.chmod(recovery,0o600)
                    source.backup(live)
        except (sqlite3.Error,ValueError,TypeError,IndexError,KeyError):
            fail(422,'Не удалось проверить или восстановить резервную копию')
    return {'ok':True}

@app.post('/api/admin/restore',dependencies=[Depends(admin)])
async def restore(request:Request):
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'restore.db'
        size=0
        with path.open('wb') as f:
            async for chunk in request.stream():
                size+=len(chunk)
                if size>MAX_BACKUP_BYTES: fail(413,'Резервная копия превышает 64 МБ')
                f.write(chunk)
        with path.open('rb') as f:
            if f.read(16)!=b'SQLite format 3\x00': fail(422,'Неверный формат резервной копии')
        return await asyncio.to_thread(restore_database,path)

@app.get('/api/admin/cross-export/{eid}',dependencies=[Depends(admin)])
def cross_export(eid:int):
    def safe(s): return "'"+s if s.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else s
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        qs=questions(e); ac=analysis_config(c,eid,qs)
        if not ac: fail(422,'Нужно минимум два вопроса')
        a,b=qs[ac['first']],qs[ac['second']]
        out=io.StringIO(); w=csv.writer(out,delimiter=';')
        w.writerow(['Этап','Вопрос группы','Группа','Вопрос распределения','Ответ','Количество','Доля внутри группы (%)','Размер группы'])
        for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(eid,)):
            d=cross_results(c,r['id'],qs,ac['first'],ac['second'])
            for i,row in enumerate(d['matrix']):
                for j,n in enumerate(row):
                    w.writerow([safe(r['name']),safe(a['question']),safe(a['options'][i]['label']),safe(b['question']),safe(b['options'][j]['label']),n,f"{n*100/d['totals'][i] if d['totals'][i] else 0:.2f}",d['totals'][i]])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="votemera-cross-{eid}.csv"'})

init()
