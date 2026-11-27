import asyncio
import base64
import binascii
import shutil
import csv
import hmac
import hashlib
import secrets
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
VERIFIED_ADMINS = OrderedDict()
VERIFIED_KEYS = OrderedDict()
KEY_CACHE_LOCK = threading.Lock()
STREAM_LOCK = threading.Lock()
STREAM_CACHE = {}
STREAM_REVISION = 0
TERMINALS = OrderedDict()
TERMINALS_LOCK = threading.Lock()
DISPLAYS=OrderedDict()
WRITE_LOCK = threading.RLock()
MAX_BACKUP_BYTES = 64 * 1024 * 1024
COLORS = ['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4']
DEFAULTS = dict(brand='Votemera', subtitle='Интерактивное голосование', theme='dark', accent='#43c8bd', cooldown=1800, show_percent=True, show_count=True, chart='stack', thank_you='Спасибо за ваш голос!', display_page_size=4, display_rotate=12, vote_layout='auto', vote_style='contrast', motion='full', idle_timeout=90, backup_enabled=True, backup_retention=14)

@contextmanager
def db(write=False, transaction=True):
    global STREAM_REVISION
    with (WRITE_LOCK if write else nullcontext()):
        DB.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            if transaction: c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield c
            c.commit()
            if write: STREAM_REVISION+=1
        except BaseException:
            c.rollback(); raise
        finally: c.close()

ACCESS_LOCK = threading.RLock()
PASSWORD_ROUNDS = 240000
KEY_ROUNDS = 120000

@contextmanager
def access_db(write=False):
    with ACCESS_LOCK:
        path=DB.with_name(DB.name+'.access')
        c=sqlite3.connect(path,timeout=10)
        c.row_factory=sqlite3.Row
        try:
            os.chmod(path,0o600)
            if write: c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally: c.close()

def password_hash(value,salt,rounds=PASSWORD_ROUNDS):
    return hashlib.pbkdf2_hmac('sha256',value.encode('utf-8'),salt,rounds).hex()

def init_access():
    with access_db(True) as c:
        c.execute('CREATE TABLE IF NOT EXISTS access_settings(id INTEGER PRIMARY KEY CHECK(id=1),password_salt BLOB NOT NULL,password_hash TEXT NOT NULL,key_salt BLOB NOT NULL,required INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS terminal_keys(id INTEGER PRIMARY KEY,label TEXT NOT NULL,digest TEXT UNIQUE NOT NULL,created REAL NOT NULL,enabled INTEGER NOT NULL DEFAULT 1)')
        c.execute('CREATE TABLE IF NOT EXISTS yandex_credentials(id INTEGER PRIMARY KEY CHECK(id=1),org TEXT NOT NULL,token TEXT NOT NULL,revision TEXT NOT NULL)')
        if c.execute('SELECT 1 FROM access_settings WHERE id=1').fetchone(): return
        salt=secrets.token_bytes(32); key_salt=secrets.token_bytes(32)
        c.execute('INSERT INTO access_settings VALUES(1,?,?,?,?)',(salt,password_hash(ADMIN,salt) if ADMIN else '',key_salt,int(bool(TERMINAL))))
        if TERMINAL:
            c.execute('INSERT INTO terminal_keys(label,digest,created) VALUES(?,?,?)',('Первоначальный ключ',password_hash(TERMINAL,key_salt,KEY_ROUNDS),time.time()))

def access_required():
    with access_db() as c:
        return bool(c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0])

def verify_admin(value):
    with access_db() as c:
        row=c.execute('SELECT password_salt,password_hash FROM access_settings WHERE id=1').fetchone()
    if not row['password_hash']: fail(503,'Пароль администратора не настроен на сервере')
    if len(value)>512: return False
    fingerprint=hashlib.sha256(value.encode()).digest()
    cache_key=(fingerprint,row['password_salt'],row['password_hash'])
    if cache_key in VERIFIED_ADMINS: return True
    valid=secret_eq(password_hash(value,row['password_salt']),row['password_hash'])
    if valid:
        VERIFIED_ADMINS[cache_key]=True
        while len(VERIFIED_ADMINS)>16: VERIFIED_ADMINS.popitem(last=False)
    return valid

def set_admin_password(value):
    salt=secrets.token_bytes(32)
    with access_db(True) as c:
        c.execute('UPDATE access_settings SET password_salt=?,password_hash=? WHERE id=1',(salt,password_hash(value,salt)))

def terminal_digest(c,value):
    salt=c.execute('SELECT key_salt FROM access_settings WHERE id=1').fetchone()[0]
    return password_hash(value,salt,KEY_ROUNDS)

def checked_secret(value,minimum=8):
    if not minimum<=len(value)<=128 or any(not 33<=ord(ch)<=126 for ch in value):
        fail(422,'Используйте от '+str(minimum)+' до 128 латинских букв, цифр или символов без пробелов')
    return value

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
        c.execute('CREATE TABLE IF NOT EXISTS event_meta(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,archived INTEGER NOT NULL DEFAULT 0,template INTEGER NOT NULL DEFAULT 0,brand TEXT NOT NULL DEFAULT "",accent TEXT NOT NULL DEFAULT "",logo TEXT NOT NULL DEFAULT "")')
        c.execute('CREATE TABLE IF NOT EXISTS runtime_config(id INTEGER PRIMARY KEY CHECK(id=1),deadline REAL)')
        c.execute('INSERT OR IGNORE INTO runtime_config(id) VALUES(1)')
        c.execute('CREATE TABLE IF NOT EXISTS terminal_config(id TEXT PRIMARY KEY,label TEXT NOT NULL DEFAULT "",locked INTEGER NOT NULL DEFAULT 0,event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,revision INTEGER NOT NULL DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS yandex_source(event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE, org TEXT NOT NULL, form_id TEXT NOT NULL, spec TEXT NOT NULL, date_from TEXT NOT NULL, date_to TEXT NOT NULL, interval INTEGER NOT NULL, revision TEXT NOT NULL, aggregate TEXT NOT NULL, last_sync REAL, error TEXT NOT NULL DEFAULT "", enabled INTEGER NOT NULL DEFAULT 1)')
        c.execute('CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY,created REAL NOT NULL,action TEXT NOT NULL,detail TEXT NOT NULL)')
        c.execute('INSERT OR IGNORE INTO settings(id,data) VALUES(1,?)',(json.dumps(DEFAULTS),))
        if not c.execute('SELECT 1 FROM events LIMIT 1').fetchone():
            e=c.execute('INSERT INTO events(title,question,options,created) VALUES(?,?,?,?)',('Педагогический совет','Готовы ли вы использовать искусственный интеллект в профессиональной деятельности?',json.dumps([{'label':'Да','color':COLORS[0]},{'label':'Нет','color':COLORS[1]}]),time.time())).lastrowid
            r=c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'До педсовета',0)).lastrowid
            c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'После педсовета',1))
            c.execute('UPDATE settings SET active_event=?,active_round=? WHERE id=1 AND active_event IS NULL',(e,r))
    init_access()

def fail(status,msg): raise HTTPException(status,msg)
def secret_eq(a,b): return hmac.compare_digest(a.encode(),b.encode())
def admin(request: Request, authorization: str = Header(default='')):
    address=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with AUTH_LOCK:
        n,until=AUTH_FAILURES.get(address,(0,0))
        if until<=now: n=0; until=now+60
        if n>=10: fail(429,'Слишком много попыток входа. Повторите через минуту.')
        if not authorization.startswith('Bearer ') or not verify_admin(authorization[7:]):
            AUTH_FAILURES[address]=(n+1,until)
            AUTH_FAILURES.move_to_end(address)
            while len(AUTH_FAILURES)>4096: AUTH_FAILURES.popitem(last=False)
            fail(401,'Неверный пароль')
        AUTH_FAILURES.pop(address,None)
def terminal(x_terminal_key: str = Header(default='')):
    with access_db() as c:
        required=c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0]
        if not required: return None
        if not x_terminal_key or len(x_terminal_key)>512: fail(403,'Неверный ключ терминала')
        salt=c.execute('SELECT key_salt FROM access_settings WHERE id=1').fetchone()[0]
        fingerprint=(hashlib.sha256(x_terminal_key.encode()).digest(),salt)
        with KEY_CACHE_LOCK: digest=VERIFIED_KEYS.get(fingerprint)
        if digest is None: digest=password_hash(x_terminal_key,salt,KEY_ROUNDS)
        row=c.execute('SELECT id FROM terminal_keys WHERE digest=? AND enabled=1',(digest,)).fetchone()
        if not row: fail(403,'Неверный или отключённый ключ терминала')
        with KEY_CACHE_LOCK:
            VERIFIED_KEYS[fingerprint]=digest
            while len(VERIFIED_KEYS)>128: VERIFIED_KEYS.popitem(last=False)
        return row['id']

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
    chart: Literal['stack','bars','donut']
    thank_you: str = Field(min_length=1,max_length=120)
    display_page_size: Literal[1,2,4] = 4
    display_rotate: int = Field(default=12,ge=0,le=120)
    vote_layout: Literal["auto","grid","list"] = "auto"
    vote_style: Literal["contrast","soft"] = "contrast"
    motion: Literal["full","subtle","off"] = "full"
    idle_timeout: int = Field(default=90,ge=0,le=600)
    backup_enabled: bool=True
    backup_retention: int=Field(default=14,ge=1,le=90)
class Control(BaseModel):
    event: int
    round: int
    opened: bool
    display: Literal['live','compare','hidden','analysis','analysis_compare','presentation']
    duration: int | None = Field(default=None,ge=0,le=86400)
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

class PasswordChange(BaseModel):
    current: str = Field(max_length=512)
    password: str = Field(min_length=8,max_length=128)

class KeyCreate(BaseModel):
    label: str = Field(min_length=1,max_length=80)
    key: str | None = Field(default=None,max_length=128)

class KeyEdit(BaseModel):
    label: str = Field(min_length=1,max_length=80)
    enabled: bool

class KeyRotate(BaseModel):
    key: str | None = Field(default=None,max_length=128)

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

def external_source(c,eid):
    return c.execute('SELECT * FROM yandex_source WHERE event_id=?',(eid,)).fetchone()

def external_round(c,rid):
    return c.execute('SELECT y.* FROM yandex_source y JOIN rounds r ON r.event_id=y.event_id WHERE r.id=?',(rid,)).fetchone()

def results(c,rid,qs):
    ext=external_round(c,rid)
    if ext: return json.loads(ext['aggregate'])['results']
    items=[dict(counts=[0]*len(q['options'])) for q in qs]
    for v in c.execute('SELECT question_index,choice,count(*) n FROM votes WHERE round_id=? GROUP BY question_index,choice',(rid,)):
        items[v['question_index']]['counts'][v['choice']]=v['n']
    for x in items: x['total']=sum(x['counts'])
    return items

def analysis_config(c,eid,qs):
    if len(qs)<2 or external_source(c,eid): return None
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

def audit(c,action,detail=''):
    c.execute('INSERT INTO audit_log(created,action,detail) VALUES(?,?,?)',(time.time(),action,str(detail)[:500]))
    c.execute('DELETE FROM audit_log WHERE id <= (SELECT max(id)-5000 FROM audit_log)')

def meta(c,eid):
    row=c.execute('SELECT archived,template,brand,accent,logo FROM event_meta WHERE event_id=?',(eid,)).fetchone()
    return dict(row) if row else dict(archived=0,template=0,brand='',accent='',logo='')

def deadline(c):
    return c.execute('SELECT deadline FROM runtime_config WHERE id=1').fetchone()[0]

def effective_open(c,s):
    end=deadline(c)
    return bool(s['opened'] and (end is None or end>time.time()))

def terminal_allowed(c,tid,eid):
    t=c.execute('SELECT locked,event_id FROM terminal_config WHERE id=?',(tid,)).fetchone()
    if t and (t['locked'] or (t['event_id'] is not None and t['event_id']!=eid)):
        fail(423,'Терминал заблокирован или назначен другому мероприятию')

def snapshot(c, private=False, voting=False):
    s=c.execute('SELECT * FROM settings WHERE id=1').fetchone()
    cfg=DEFAULTS | json.loads(s['data'])
    e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
    result=dict(settings=cfg, event=None, round=s['active_round'], opened=effective_open(c,s), deadline=deadline(c), epoch=s['epoch'], display=s['display'], version=VERSION, terminal_key_required=access_required())
    if e:
        qs=questions(e); options=qs[0]['options']; rounds=[]; ac=analysis_config(c,e['id'],qs); m=meta(c,e['id']); ext=external_source(c,e['id'])
        if ext: result['external']=dict(provider='yandex',url='https://forms.yandex.ru/cloud/u/'+ext['form_id']+'/',last_sync=ext['last_sync'],stale=bool(ext['error']) or not ext['enabled'],enabled=bool(ext['enabled'])); result['opened']=False
        if m['brand']: cfg['brand']=m['brand']
        if m['accent']: cfg['accent']=m['accent']
        result['logo']=m['logo']
        if private: result['settings']=DEFAULTS | json.loads(s['data'])
        for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(e['id'],)):
            item=dict(id=r['id'],name=r['name'])
            if private or (not voting and s['display']!='hidden' and (s['display'] in ('compare','analysis_compare','presentation') or r['id']==s['active_round'])):
                rs=results(c,r['id'],qs)
                item.update(counts=rs[0]['counts'],total=json.loads(ext['aggregate'])['total'] if ext else rs[0]['total'],results=rs)
                if ac and (private or s['display'] in ('analysis','analysis_compare','presentation')):
                    item['cross']=cross_results(c,r['id'],qs,ac['first'],ac['second'])
            rounds.append(item)
        result['event']=dict(id=e['id'],title=e['title'],question=e['question'],options=options,questions=qs,rounds=rounds)
        if private: result['event']['meta']=m
        if private or (not voting and s['display'] in ('analysis','analysis_compare','presentation')): result['event']['analysis']=ac
        if not voting: result['event']['presentation']=presentation_config(c,e['id'],qs)
    if private:
        result['events']=[dict(x) for x in c.execute('SELECT e.id,e.title,e.question,e.created,coalesce(m.archived,0) archived,coalesce(m.template,0) template,(SELECT count(*) FROM rounds WHERE event_id=e.id) AS round_count,(SELECT count(*) FROM votes v JOIN rounds r ON r.id=v.round_id WHERE r.event_id=e.id AND v.question_index=0) AS total FROM events e LEFT JOIN event_meta m ON m.event_id=e.id ORDER BY e.id DESC')]
        for item in result['events']:
            ext=external_source(c,item['id'])
            if ext: item.update(source='yandex',total=json.loads(ext['aggregate'])['total'])
    return result

app=FastAPI(title='Votemera',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None)
app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.middleware('http')
async def headers(request, call_next):
    r=await call_next(request)
    r.headers.setdefault('Cache-Control','no-store')
    r.headers['X-Content-Type-Options']='nosniff'
    r.headers['X-Frame-Options']='DENY'
    r.headers['Referrer-Policy']='no-referrer'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    return r
@app.get('/')
@app.get('/vote')
@app.get('/display')
@app.get('/admin')
@app.get('/presenter')
def page(): return FileResponse(ROOT/'static'/'index.html')
@app.get('/health')
def health():
    with db() as c: c.execute('SELECT 1 FROM settings').fetchone()
    return {'status':'ok','version':VERSION}
@app.get('/api/state')
def state(view: Literal['vote','display']='display'):
    with db() as c: return snapshot(c,voting=view=='vote')
def stream_snapshot(view):
    # Coalesce expensive aggregate reads across simultaneous displays/terminals.
    with STREAM_LOCK:
        key=(str(DB),view,STREAM_REVISION); now=time.monotonic(); cached=STREAM_CACHE.get(key)
        if cached and now-cached[0]<.20: return cached[1]
        with db() as c: data=json.dumps(snapshot(c,voting=view=='vote'),ensure_ascii=False,separators=(',',':'))
        if len(STREAM_CACHE)>8: STREAM_CACHE.clear()
        STREAM_CACHE[key]=(time.monotonic(),data)
        return data

@app.get('/api/stream')
async def stream(request:Request, view: Literal['vote','display']='display'):
    async def events():
        last=None
        last_sent=0.0
        yield 'retry: 1000\n\n'
        while not await request.is_disconnected():
            data=await asyncio.to_thread(stream_snapshot,view)
            now=time.monotonic()
            if data!=last:
                last=data; last_sent=now
                yield 'data: '+data+'\n\n'
            elif now-last_sent>=3:
                last_sent=now
                # Named event reaches JS, unlike an invisible SSE comment.
                yield 'event: heartbeat\ndata: {}\n\n'
            await asyncio.sleep(.25)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-store, no-transform'})
@app.post('/api/vote',dependencies=[Depends(terminal)])
def vote(v:Vote):
    with db(True) as c:
        old=c.execute('SELECT * FROM votes WHERE nonce=?',(v.nonce,)).fetchone()
        if old:
            if (old['round_id'],old['choice'],old['terminal']) != (v.round,v.choice,v.terminal): fail(409,'Конфликт идентификатора')
            return {'ok':True,'duplicate':True}
        s=c.execute('SELECT * FROM settings').fetchone()
        if external_source(c,s['active_event']): fail(409,'Заполните Яндекс Форму')
        terminal_allowed(c,v.terminal,s['active_event'])
        if not effective_open(c,s) or s['active_round']!=v.round or s['epoch']!=v.epoch: fail(409,'Этап изменился или закрыт. Повторите выбор.')
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
        if external_source(c,s['active_event']): fail(409,'Заполните Яндекс Форму')
        terminal_allowed(c,v.terminal,s['active_event'])
        if not effective_open(c,s) or s['active_round']!=v.round or s['epoch']!=v.epoch: fail(409,'Этап изменился или закрыт. Начните заново.')
        e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
        qs=questions(e)
        if not valid_ballot(qs,v.choices): fail(422,'Неверные ответы или пропуски анкеты')
        now=time.time(); cfg=DEFAULTS|json.loads(s['data'])
        last=c.execute('SELECT max(created) FROM votes WHERE terminal=?',(v.terminal,)).fetchone()[0]
        if last is not None and now-last<cfg['cooldown']/1000: fail(429,'Терминал ещё не готов')
        if any(c.execute('SELECT 1 FROM votes WHERE nonce=?',(n,)).fetchone() for n in nonces): fail(409,'Конфликт идентификатора')
        c.executemany('INSERT INTO votes(round_id,choice,nonce,terminal,created,question_index,ballot_id) VALUES(?,?,?,?,?,?,?)',[(v.round,n,nonces[i],v.terminal,now,i,v.nonce) for i,n in enumerate(v.choices) if n is not None])
    return {'ok':True,'duplicate':False}

@app.post('/api/terminal/heartbeat')
def heartbeat(v:TerminalHeartbeat,key_id=Depends(terminal)):
    now=time.monotonic()
    with TERMINALS_LOCK:
        TERMINALS[v.terminal]=(now,key_id)
        TERMINALS.move_to_end(v.terminal)
        while TERMINALS and (len(TERMINALS)>4096 or next(iter(TERMINALS.values()))[0]<now-300):
            TERMINALS.popitem(last=False)
    with db() as c:
        t=c.execute('SELECT * FROM terminal_config WHERE id=?',(v.terminal,)).fetchone()
        e=c.execute('SELECT active_event FROM settings WHERE id=1').fetchone()[0]
        return {'ok':True,'locked':bool(t and (t['locked'] or (t['event_id'] is not None and t['event_id']!=e))),'revision':t['revision'] if t else 0,'label':t['label'] if t else ''}

@app.post('/api/display/heartbeat')
def display_heartbeat(v:TerminalHeartbeat):
    now=time.monotonic()
    with TERMINALS_LOCK:
        DISPLAYS[v.terminal]=now; DISPLAYS.move_to_end(v.terminal)
        while DISPLAYS and (len(DISPLAYS)>1024 or next(iter(DISPLAYS.values()))<now-60): DISPLAYS.popitem(last=False)
    return {'ok':True}

@app.get('/api/admin/terminals',dependencies=[Depends(admin)])
def terminal_status():
    now=time.monotonic()
    with access_db() as c:
        names={r['id']:r['label'] for r in c.execute('SELECT id,label FROM terminal_keys WHERE enabled=1')}
        required=bool(c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0])
    with TERMINALS_LOCK:
        rows=[dict(id=k[-8:],device_id=k,seconds_ago=int(now-t),online=now-t<25 and (kid in names or (kid is None and not required)),key_name=names.get(kid,'Без ключа' if kid is None else 'Ключ отозван')) for k,(t,kid) in reversed(TERMINALS.items()) if now-t<300]
    with db() as c:
        configs={r['id']:dict(r) for r in c.execute('SELECT * FROM terminal_config')}
    for r in rows: r.update({k:v for k,v in configs.get(r['device_id'],{}).items() if k!='id'})
    for tid,t in configs.items():
        if not any(r['device_id']==tid for r in rows): rows.append(dict(device_id=tid,id=tid[-8:],seconds_ago=None,online=False,key_name='',**{k:v for k,v in t.items() if k!='id'}))
    return {'online':sum(r['online'] for r in rows),'terminals':rows}

@app.put('/api/admin/password',dependencies=[Depends(admin)])
def change_password(v:PasswordChange):
    value=checked_secret(v.password)
    with ACCESS_LOCK:
        if not verify_admin(v.current): fail(403,'Текущий пароль неверен')
        set_admin_password(value)
    with db(True) as c: audit(c,'Смена пароля администратора')
    return {'ok':True}

@app.get('/api/admin/keys',dependencies=[Depends(admin)])
def list_keys():
    with access_db() as c:
        return {'required':bool(c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0]),'keys':[dict(r) for r in c.execute('SELECT id,label,created,enabled FROM terminal_keys ORDER BY id')]}

@app.post('/api/admin/keys',dependencies=[Depends(admin)])
def create_key(v:KeyCreate):
    value=checked_secret(v.key if v.key is not None else secrets.token_hex(16))
    with access_db(True) as c:
        if c.execute('SELECT count(*) FROM terminal_keys').fetchone()[0]>=100: fail(409,'Максимум 100 ключей')
        try:
            kid=c.execute('INSERT INTO terminal_keys(label,digest,created) VALUES(?,?,?)',(clean(v.label),terminal_digest(c,value),time.time())).lastrowid
        except sqlite3.IntegrityError: fail(409,'Такой ключ уже существует')
        c.execute('UPDATE access_settings SET required=1 WHERE id=1')
    with db(True) as c: audit(c,'Создание ключа терминала',kid)
    return {'id':kid,'key':value}

def forget_key_terminals(kid):
    with TERMINALS_LOCK:
        for terminal_id,(_,key_id) in list(TERMINALS.items()):
            if key_id==kid: TERMINALS.pop(terminal_id,None)

@app.put('/api/admin/keys/{kid}',dependencies=[Depends(admin)])
def edit_key(kid:int,v:KeyEdit):
    with access_db(True) as c:
        if not c.execute('UPDATE terminal_keys SET label=?,enabled=? WHERE id=?',(clean(v.label),int(v.enabled),kid)).rowcount: fail(404,'Ключ не найден')
    if not v.enabled: forget_key_terminals(kid)
    with db(True) as c: audit(c,'Изменение ключа терминала',kid)
    return {'ok':True}

@app.post('/api/admin/keys/{kid}/rotate',dependencies=[Depends(admin)])
def rotate_key(kid:int,v:KeyRotate):
    value=checked_secret(v.key if v.key is not None else secrets.token_hex(16))
    with access_db(True) as c:
        try:
            if not c.execute('UPDATE terminal_keys SET digest=? WHERE id=?',(terminal_digest(c,value),kid)).rowcount: fail(404,'Ключ не найден')
        except sqlite3.IntegrityError: fail(409,'Такой ключ уже существует')
    forget_key_terminals(kid)
    with db(True) as c: audit(c,'Замена ключа терминала',kid)
    return {'id':kid,'key':value}

@app.delete('/api/admin/keys/{kid}',dependencies=[Depends(admin)])
def delete_key(kid:int):
    with access_db(True) as c:
        if not c.execute('DELETE FROM terminal_keys WHERE id=?',(kid,)).rowcount: fail(404,'Ключ не найден')
    forget_key_terminals(kid)
    with db(True) as c: audit(c,'Удаление ключа терминала',kid)
    return {'ok':True}

@app.get('/api/admin/state',dependencies=[Depends(admin)])
def admin_state():
    with db() as c: return snapshot(c,private=True)
@app.post('/api/admin/event',dependencies=[Depends(admin)])
def new_event(e:Event):
    qs,names=validate_event(e)
    with db(True) as c:
        eid=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',(clean(e.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),time.time(),json.dumps(qs,ensure_ascii=False))).lastrowid
        ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(eid,n,i)).lastrowid for i,n in enumerate(names)]
        audit(c,'Создание мероприятия',eid)
    return {'id':eid,'round':ids[0]}
@app.get('/api/admin/event/{eid}',dependencies=[Depends(admin)])
def event_detail(eid:int):
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        return dict(source='yandex' if external_source(c,eid) else 'local',id=eid,title=e['title'],question=e['question'],options=json.loads(e['options']),questions=questions(e),meta=meta(c,eid),rounds=[dict(r) for r in c.execute('SELECT id,name FROM rounds WHERE event_id=? ORDER BY position',(eid,))])
@app.put('/api/admin/event/{eid}',dependencies=[Depends(admin)])
def edit_event(eid:int,e:Event):
    qs,names=validate_event(e)
    with db(True) as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone(): fail(404,'Не найдено')
        if external_source(c,eid): fail(409,'Редактируйте структуру в Яндекс Формах и создайте новое подключение')
        s=c.execute('SELECT * FROM settings').fetchone()
        if (s['active_event']==eid and effective_open(c,s)) or c.execute('SELECT 1 FROM votes JOIN rounds ON rounds.id=votes.round_id WHERE event_id=? LIMIT 1',(eid,)).fetchone(): fail(409,'Редактирование доступно только до начала голосования или после очистки всех этапов')
        c.execute('UPDATE events SET title=?,question=?,options=?,questions=? WHERE id=?',(clean(e.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),json.dumps(qs,ensure_ascii=False),eid))
        c.execute('DELETE FROM analysis_config WHERE event_id=?',(eid,))
        c.execute('DELETE FROM presentation_config WHERE event_id=?',(eid,))
        c.execute('DELETE FROM rounds WHERE event_id=?',(eid,)); ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(eid,n,i)).lastrowid for i,n in enumerate(names)]
        if s['active_event']==eid: c.execute('UPDATE settings SET active_round=?,epoch=? WHERE id=1',(ids[0],uuid4().hex))
        audit(c,'Редактирование мероприятия',eid)
    return {'id':eid,'round':ids[0]}
@app.delete('/api/admin/event/{eid}',dependencies=[Depends(admin)])
def delete_event(eid:int,confirm_results:bool=False):
    with db(True) as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone(): fail(404,'Мероприятие не найдено')
        if c.execute('SELECT active_event FROM settings WHERE id=1').fetchone()[0]==eid: fail(409,'Сначала выберите другое активное мероприятие')
        ext=external_source(c,eid)
        if not confirm_results and ext and json.loads(ext['aggregate'])['total']: fail(409,'Подтвердите удаление результатов')
        if not confirm_results and c.execute('SELECT 1 FROM votes JOIN rounds ON rounds.id=votes.round_id WHERE event_id=? LIMIT 1',(eid,)).fetchone(): fail(409,'Подтвердите удаление результатов')
        audit(c,'Удаление мероприятия',eid)
        c.execute('DELETE FROM events WHERE id=?',(eid,))
    return {'ok':True}

@app.post('/api/admin/event/{eid}/copy',dependencies=[Depends(admin)])
def copy_event(eid:int):
    return clone_event(eid,'Копия: ')

@app.post('/api/admin/control',dependencies=[Depends(admin)])
def control(v:Control):
    with db(True) as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(v.event,)).fetchone(): fail(404,'Мероприятие не найдено')
        if meta(c,v.event)['archived']: fail(409,'Сначала восстановите мероприятие из архива')
        if external_source(c,v.event) and (v.opened or v.display not in ('live','hidden')): fail(409,'Для Яндекс Форм используйте текущие результаты или скрытие; сбор ответов управляется в Яндексе')
        if v.opened and update_busy(): fail(409,'Дождитесь завершения обновления')
        if not c.execute('SELECT 1 FROM rounds WHERE id=? AND event_id=?',(v.round,v.event)).fetchone(): fail(422,'Неверный этап')
        s=c.execute('SELECT * FROM settings').fetchone()
        changed=(s['active_event'],s['active_round'],effective_open(c,s))!=(v.event,v.round,v.opened)
        epoch=uuid4().hex if changed else s['epoch']
        if not v.opened or changed or v.duration is not None:
            c.execute('UPDATE runtime_config SET deadline=? WHERE id=1',(time.time()+v.duration if v.opened and v.duration else None,))
        if changed or s['display']!=v.display or v.duration is not None: audit(c,'Управление этапом',f'Мероприятие {v.event}, этап {v.round}, открыто={v.opened}, экран={v.display}')
        c.execute('UPDATE settings SET active_event=?,active_round=?,opened=?,epoch=?,display=? WHERE id=1',(v.event,v.round,int(v.opened),epoch,v.display))
        return snapshot(c,private=True)
@app.put('/api/admin/analysis/{eid}',dependencies=[Depends(admin)])
def set_analysis(eid:int,v:AnalysisConfig):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        if external_source(c,eid): fail(409,'Перекрёстный анализ для Яндекс Форм пока недоступен')
        if v.first==v.second or max(v.first,v.second)>=len(questions(e)): fail(422,'Выберите два разных вопроса')
        c.execute('INSERT INTO analysis_config VALUES(?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET first_q=excluded.first_q,second_q=excluded.second_q,chart=excluded.chart',(eid,v.first,v.second,v.chart))
    return {'ok':True}

@app.put('/api/admin/presentation/{eid}',dependencies=[Depends(admin)])
def set_presentation(eid:int,v:Presentation):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        if external_source(c,eid) and v.slides: fail(409,'Сценарии для Яндекс Форм пока недоступны')
        n=len(questions(e))
        if len(set(v.order))!=len(v.order) or any(not 0<=i<n for i in v.order): fail(422,'Неверный порядок вопросов')
        if any(s.question>=n or (s.mode.startswith('analysis') and n<2) for s in v.slides): fail(422,'Неверный слайд')
        c.execute('INSERT INTO presentation_config VALUES(?,?) ON CONFLICT(event_id) DO UPDATE SET data=excluded.data',(eid,json.dumps(v.model_dump())))
    return {'ok':True}

@app.post('/api/admin/rehearsal/{eid}',dependencies=[Depends(admin)])
def rehearsal(eid:int):
    return clone_event(eid,'[Репетиция] ')

def clone_event(eid:int,prefix:str):
    with db(True) as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        if external_source(c,eid): fail(409,'Создайте отдельное подключение в разделе интеграций')
        new=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',(prefix+e['title'][:120-len(prefix)],e['question'],e['options'],time.time(),e['questions'])).lastrowid
        ids=[c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(new,r['name'],r['position'])).lastrowid for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(eid,)).fetchall()]
        for name,columns in [('analysis_config','first_q,second_q,chart'),('presentation_config','data'),('event_meta','brand,accent,logo')]:
            c.execute(f'INSERT INTO {name}(event_id,{columns}) SELECT ?,{columns} FROM {name} WHERE event_id=?',(new,eid))
    return {'id':new,'round':ids[0]}

@app.put('/api/admin/settings',dependencies=[Depends(admin)])
def settings(v:Settings):
    with db(True) as c:
        c.execute('UPDATE settings SET data=? WHERE id=1',(json.dumps(v.model_dump(),ensure_ascii=False),))
        audit(c,'Настройки системы')
    return {'ok':True}
@app.delete('/api/admin/round/{rid}/votes',dependencies=[Depends(admin)])
def clear(rid:int):
    with db(True) as c:
        s=c.execute('SELECT * FROM settings').fetchone()
        if effective_open(c,s) and s['active_round']==rid: fail(409,'Сначала закройте этап')
        if not c.execute('SELECT 1 FROM rounds WHERE id=?',(rid,)).fetchone(): fail(404,'Этап не найден')
        if external_round(c,rid): fail(409,'Ответы импортируются из Яндекс Форм; удаляйте их в Яндексе')
        audit(c,'Очистка голосов',rid)
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
            from release_target import latest_release,version_tuple
            release=latest_release()
            info.update(available=version_tuple(release['version'])>version_tuple(VERSION),latest=release['version'])
        except (OSError,subprocess.SubprocessError,ValueError,KeyError):
            info['error']='Не удалось проверить GitHub. Проверьте подключение к интернету.'
    return info

@app.post('/api/admin/update',dependencies=[Depends(admin)])
def request_update():
    with db(True) as c:
        if update_status()=='unavailable': fail(503,'Модуль обновления не установлен. Запустите scripts/install.sh.')
        if effective_open(c,c.execute('SELECT * FROM settings WHERE id=1').fetchone()): fail(409,'Сначала закройте голосование')
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
    global STREAM_REVISION
    def schema(c):
        return set(tuple(r) for r in c.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    with WRITE_LOCK:
        with db() as current:
            if effective_open(current,current.execute('SELECT * FROM settings WHERE id=1').fetchone()): fail(409,'Сначала закройте голосование')
            if update_busy(): fail(409,'Дождитесь завершения обновления')
            expected_schema=schema(current)
        try:
            with closing(sqlite3.connect(path,timeout=5)) as source:
                source.row_factory=sqlite3.Row
                source.execute('PRAGMA trusted_schema=OFF')
                source.execute('PRAGMA foreign_keys=ON')
                # Previous backups may lack only the four additive v0.11 tables.
                actual_schema=schema(source)
                additions={'event_meta','runtime_config','terminal_config','audit_log','yandex_source'}
                missing=expected_schema-actual_schema
                if actual_schema-expected_schema or any(r[0]!='table' or r[1] not in additions for r in missing): fail(422,'Несовместимая схема базы')
                for row in sorted(missing): source.execute(row[3])
                source.execute('INSERT OR IGNORE INTO runtime_config(id) VALUES(1)')
                if source.execute('PRAGMA quick_check').fetchone()[0]!='ok' or source.execute('PRAGMA foreign_key_check').fetchone(): fail(422,'Повреждённая база')
                if source.execute('SELECT count(*) FROM settings').fetchone()[0]!=1: fail(422,'Некорректные настройки')
                source.execute('UPDATE settings SET opened=0,epoch=?,display=? WHERE id=1',(uuid4().hex,'hidden'))
                source.execute('UPDATE runtime_config SET deadline=NULL WHERE id=1')
                source.execute('UPDATE yandex_source SET enabled=0,revision=?,error=?',(uuid4().hex,'Восстановлено из резервной копии; включите синхронизацию'))
                source.commit()
                preview=snapshot(source,private=True)
                if not preview['event'] or not any(r['id']==preview['round'] for r in preview['event']['rounds']): fail(422,'Нет корректного активного мероприятия')
                Settings.model_validate(preview['settings'])
                for e in source.execute('SELECT * FROM events').fetchall():
                    qs=questions(e)
                    if not 1<=len(qs)<=12: fail(422,'Некорректная анкета')
                    for q in qs: Question.model_validate(q)
                    ext=external_source(source,e['id'])
                    if ext:
                        if not re.fullmatch(r'[a-f0-9]{24}',ext['form_id']): fail(422,'Неверный ID формы')
                        ag=json.loads(ext['aggregate']); rr=ag['results']; spec=json.loads(ext['spec'])
                        if len(rr)!=len(qs) or len(spec)!=len(qs) or type(ag['total']) is not int or ag['total']<0: fail(422,'Некорректная статистика формы')
                        for q,r in zip(qs,rr):
                            if len(r['counts'])!=len(q['options']) or type(r['total']) is not int or not 0<=r['total']<=ag['total'] or any(type(n) is not int or not 0<=n<=r['total'] for n in r['counts']): fail(422,'Некорректная статистика формы')
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
                    STREAM_REVISION+=1
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

class EventMeta(BaseModel):
    archived: bool=False
    template: bool=False
    brand: str=Field(default='',max_length=80)
    accent: str=Field(default='',pattern=r'^$|^#[a-fA-F0-9]{6}$')
    logo: str=Field(default='',max_length=280000)

class TerminalConfig(BaseModel):
    label: str=Field(default='',max_length=80)
    locked: bool=False
    event_id: int | None=None
    reload: bool=False

class LiveControl(BaseModel):
    opened: bool | None=None
    display: Literal['live','compare','hidden','analysis','analysis_compare','presentation'] | None=None
    duration: int | None=Field(default=None,ge=0,le=86400)
    round: int | None=None

@app.put('/api/admin/event/{eid}/meta',dependencies=[Depends(admin)])
def event_metadata(eid:int,v:EventMeta):
    if v.logo:
        m=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',v.logo)
        if not m: fail(422,'Допустимы PNG, JPEG и WebP')
        try:
            raw=base64.b64decode(m[2],validate=True)
            signatures={'png':raw.startswith(b'\x89PNG\r\n\x1a\n'),'jpeg':raw.startswith(b'\xff\xd8\xff'),'webp':raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'}
            if len(raw)>200000 or not signatures[m[1]]: fail(422,'Некорректный логотип или размер больше 200 КБ')
        except (binascii.Error,ValueError): fail(422,'Некорректный логотип')
    with db(True) as c:
        if not c.execute('SELECT 1 FROM events WHERE id=?',(eid,)).fetchone(): fail(404,'Мероприятие не найдено')
        if v.archived and c.execute('SELECT active_event FROM settings WHERE id=1').fetchone()[0]==eid: fail(409,'Сначала выберите другое мероприятие')
        c.execute('INSERT INTO event_meta VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET archived=excluded.archived,template=excluded.template,brand=excluded.brand,accent=excluded.accent,logo=excluded.logo',(eid,int(v.archived),int(v.template),v.brand.strip(),v.accent,v.logo))
        audit(c,'Настройки мероприятия',eid)
    return {'ok':True}

@app.put('/api/admin/terminal/{tid}',dependencies=[Depends(admin)])
def configure_terminal(tid:str,v:TerminalConfig):
    if not re.fullmatch(r'[a-f0-9]{32}',tid): fail(422,'Некорректный идентификатор')
    with db(True) as c:
        if v.event_id is not None and not c.execute('SELECT 1 FROM events WHERE id=?',(v.event_id,)).fetchone(): fail(404,'Мероприятие не найдено')
        c.execute('INSERT INTO terminal_config VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,locked=excluded.locked,event_id=excluded.event_id,revision=terminal_config.revision+excluded.revision',(tid,v.label.strip(),int(v.locked),v.event_id,int(v.reload)))
        audit(c,'Настройка терминала',tid[-8:])
    return {'ok':True}

@app.patch('/api/admin/live',dependencies=[Depends(admin)])
def live_control(v:LiveControl):
    # Serialize read/modify/write with normal stage control.
    with WRITE_LOCK:
        with db() as c:
            s=c.execute('SELECT * FROM settings WHERE id=1').fetchone()
            opened=effective_open(c,s) if v.opened is None else v.opened
            target_round=s['active_round'] if v.round is None else v.round
        return control(Control(event=s['active_event'],round=target_round,opened=opened,display=v.display if v.display is not None else s['display'],duration=v.duration))

@app.get('/api/admin/audit',dependencies=[Depends(admin)])
def audit_history():
    with db() as c: return {'entries':[dict(r) for r in c.execute('SELECT created,action,detail FROM audit_log ORDER BY id DESC LIMIT 200')]}

@app.get('/api/admin/diagnostics',dependencies=[Depends(admin)])
def diagnostics():
    with db() as c:
        status=c.execute('PRAGMA quick_check').fetchone()[0]
        s=c.execute('SELECT * FROM settings WHERE id=1').fetchone()
        db_ok=status=='ok'
        end=deadline(c)
    disk=shutil.disk_usage(DB.parent)
    backup_status=None
    try: backup_status=json.loads((DB.parent/'backup-status.json').read_text())
    except (OSError,ValueError): pass
    with TERMINALS_LOCK: display_count=sum(now>time.monotonic()-25 for now in DISPLAYS.values())
    return {'displays':display_count,'backup':backup_status,'version':VERSION,'database_ok':db_ok,'disk_free':disk.free,'disk_total':disk.total,'opened':effective_open_for(s,end),'deadline':end,'update':update_status(),'terminals':terminal_status()['online'],'access_required':access_required()}

def effective_open_for(s,end):
    return bool(s['opened'] and (end is None or end>time.time()))

@app.get('/api/admin/qr',dependencies=[Depends(admin)])
def terminal_qr(url:str):
    from urllib.parse import urlsplit
    import qrcode
    import qrcode.image.svg
    u=urlsplit(url)
    if len(url)>512 or u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path!='/vote': fail(422,'Укажите адрес терминала без ключа')
    out=io.BytesIO()
    qrcode.make(url,image_factory=qrcode.image.svg.SvgPathImage).save(out)
    return Response(out.getvalue(),media_type='image/svg+xml')

@app.get('/api/admin/report/{eid}/{fmt}',dependencies=[Depends(admin)])
def report(eid:int,fmt:Literal['pdf','xlsx']):
    from reports import make_report
    with db() as c:
        e=c.execute('SELECT * FROM events WHERE id=?',(eid,)).fetchone()
        if not e: fail(404,'Мероприятие не найдено')
        qs=questions(e)
        rows=[(r['name'],results(c,r['id'],qs)) for r in c.execute('SELECT * FROM rounds WHERE event_id=? ORDER BY position',(eid,))]
    try: data,media=make_report(e['title'],qs,rows,fmt)
    except FileNotFoundError: fail(503,'Для PDF установите fonts-dejavu-core на сервере')
    return Response(data,media_type=media,headers={'Content-Disposition':f'attachment; filename="votemera-{eid}.{fmt}"'})

# Yandex Forms integration, read-only remote access and aggregate-only storage.
import yandex_forms as yf
YF_LOCK=threading.Lock()
YF_DUE={}
YF_STOP=threading.Event()

class YandexCredentials(BaseModel):
    org: str
    token: str

class YandexCreate(BaseModel):
    form_id: str=Field(pattern=r'^[a-fA-F0-9]{24}$')
    title: str=Field(min_length=1,max_length=120)
    questions: list[int]=Field(min_length=1,max_length=12)
    date_from: str=Field(default='',max_length=40)
    date_to: str=Field(default='',max_length=40)
    interval: int=Field(default=5,ge=5,le=3600)

class YandexSettings(BaseModel):
    interval: int=Field(ge=5,le=3600)
    enabled: bool
    date_from: str | None=Field(default=None,max_length=40)
    date_to: str | None=Field(default=None,max_length=40)

def yf_credentials():
    with access_db() as c:
        row=c.execute('SELECT * FROM yandex_credentials WHERE id=1').fetchone()
        return dict(row) if row else None

def yf_require():
    creds=yf_credentials()
    if not creds: fail(409,'Сначала подключите Яндекс Формы')
    return creds

def yf_call(fn,*args):
    try:return fn(*args)
    except yf.FormsError as exc: fail(502,str(exc))
    except (KeyError,TypeError,ValueError):fail(502,'Неожиданный формат ответа Яндекс Форм')

@app.get('/api/admin/yandex',dependencies=[Depends(admin)])
def yf_status():
    creds=yf_credentials()
    with db() as c:
        sources=[dict(event_id=r['event_id'],title=r['title'],form_id=r['form_id'],interval=r['interval'],enabled=bool(r['enabled']),last_sync=r['last_sync'],error=r['error'],total=json.loads(r['aggregate'])['total'],date_from=r['date_from'],date_to=r['date_to']) for r in c.execute('SELECT y.*,e.title FROM yandex_source y JOIN events e ON e.id=y.event_id ORDER BY e.id DESC')]
    return {'connected':bool(creds),'org':creds['org'] if creds else '', 'org_header':yf.organization_header(creds['org']) if creds else None, 'sources':sources}

@app.put('/api/admin/yandex',dependencies=[Depends(admin)])
def yf_connect(v:YandexCredentials):
    org=v.org.strip(); token=v.token.strip()
    if token.lower().startswith('oauth '):token=token[6:].strip()
    try: yf.organization_header(org)
    except yf.FormsError as exc: fail(422,str(exc))
    if not 8<=len(token)<=2048 or not re.fullmatch(r'[!-~]+',token):fail(422,'Введите действительный OAuth-токен без пробелов')
    yf_call(yf.request,org,token,'/v1/users/me/')
    yf_call(yf.list_forms,org,token,'',0)
    with access_db(True) as c:
        c.execute('INSERT OR REPLACE INTO yandex_credentials VALUES(1,?,?,?)',(org,token,uuid4().hex))
    return {'ok':True}

@app.delete('/api/admin/yandex',dependencies=[Depends(admin)])
def yf_disconnect():
    with access_db(True) as c:c.execute('DELETE FROM yandex_credentials')
    with db(True) as c:c.execute("UPDATE yandex_source SET enabled=0,revision=?,error='Подключение отключено'",(uuid4().hex,))
    return {'ok':True}

@app.get('/api/admin/yandex/forms',dependencies=[Depends(admin)])
def yf_forms(search:str='',offset:int=0):
    c=yf_require()
    if not 0<=offset<=10000 or len(search)>120:fail(422,'Неверный фильтр')
    return {'forms':yf_call(yf.list_forms,c['org'],c['token'],search,offset)}

@app.get('/api/admin/yandex/forms/{sid}',dependencies=[Depends(admin)])
def yf_questions(sid:str):
    c=yf_require()
    return {'questions':yf_call(yf.questions,c['org'],c['token'],sid)}

@app.post('/api/admin/yandex/events',dependencies=[Depends(admin)])
def yf_create(v:YandexCreate):
    creds=yf_require()
    start=yf_call(yf.validate_date,v.date_from);end=yf_call(yf.validate_date,v.date_to)
    if start and end and yf.datetime.fromisoformat(start)>yf.datetime.fromisoformat(end): fail(422,'Начало периода позже окончания')
    available={q['id']:q for q in yf_call(yf.questions,creds['org'],creds['token'],v.form_id)}
    if len(set(v.questions))!=len(v.questions) or any(q not in available or not available[q]['supported'] for q in v.questions):fail(422,'Выберите поддерживаемые вопросы')
    spec=[available[q] for q in v.questions]
    qs=[{'question':q['label'],'options':q['options'],'description':'Множественный выбор: доля ответивших' if q['multi'] else '', 'confirm':False,'multi':q['multi']} for q in spec]
    # Check response access before creating the event.
    yf_call(yf.request,creds['org'],creds['token'],'/v1/surveys/'+v.form_id.lower()+'/answers',{'page_size':1,'questions':','.join(str(q) for q in v.questions)})
    ag={'total':0,'results':[{'total':0,'counts':[0]*len(q['options'])} for q in qs]}
    with db(True) as c:
        eid=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',(clean(v.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),time.time(),json.dumps(qs,ensure_ascii=False))).lastrowid
        rid=c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,0)',(eid,'Ответы Яндекс Форм')).lastrowid
        c.execute('INSERT INTO yandex_source VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(eid,creds['org'],v.form_id.lower(),json.dumps(spec,ensure_ascii=False),start,end,v.interval,uuid4().hex,json.dumps(ag),None,'',1))
        audit(c,'Подключение Яндекс Формы',eid)
    return {'id':eid,'round':rid}

def yf_sync(eid):
    if not YF_LOCK.acquire(blocking=False):return False
    try:
        with db() as c:
            r=external_source(c,eid)
            if not r:return False
            row=dict(r)
        creds=yf_credentials()
        try:
            if not creds or creds['org']!=row['org']:raise yf.FormsError('Нет подключения к организации формы')
            ag=yf.aggregate(creds['org'],creds['token'],row['form_id'],json.loads(row['spec']),row['date_from'],row['date_to'])
            error=''
        except yf.FormsError as exc:ag=None;error=str(exc)
        except Exception:ag=None;error='Неожиданный формат данных Яндекс Форм'
        with db(True) as c:
            current=yf_credentials()
            if (current or {}).get('revision')!=(creds or {}).get('revision'):return False
            if ag is None:
                c.execute('UPDATE yandex_source SET error=? WHERE event_id=? AND revision=?',(error,eid,row['revision']))
            else:
                c.execute('UPDATE yandex_source SET aggregate=?,last_sync=?,error="" WHERE event_id=? AND revision=?',(json.dumps(ag),time.time(),eid,row['revision']))
        YF_DUE[(str(DB),eid)]=time.monotonic()+max(row['interval'],60 if error else 0)
        return ag is not None
    finally:YF_LOCK.release()

@app.put('/api/admin/yandex/events/{eid}',dependencies=[Depends(admin)])
def yf_settings(eid:int,v:YandexSettings):
    with db(True) as c:
        row=external_source(c,eid)
        if not row:fail(404,'Подключение не найдено')
        start=row['date_from'] if v.date_from is None else yf_call(yf.validate_date,v.date_from)
        end=row['date_to'] if v.date_to is None else yf_call(yf.validate_date,v.date_to)
        if start and end and yf.datetime.fromisoformat(start)>yf.datetime.fromisoformat(end):fail(422,'Начало периода позже окончания')
        if (start,end)!=(row['date_from'],row['date_to']):
            # Never present statistics from the old period as statistics for the new period.
            empty={'total':0,'results':[{'total':0,'counts':[0]*len(q['options'])} for q in json.loads(row['spec'])]}
            c.execute('UPDATE yandex_source SET aggregate=?,last_sync=NULL,error=? WHERE event_id=?',(json.dumps(empty),'Период изменён; требуется синхронизация',eid))
        c.execute('UPDATE yandex_source SET interval=?,enabled=?,revision=?,date_from=?,date_to=? WHERE event_id=?',(v.interval,int(v.enabled),uuid4().hex,start,end,eid))
    YF_DUE[(str(DB),eid)]=0
    return {'ok':True}

@app.post('/api/admin/yandex/events/{eid}/sync',dependencies=[Depends(admin)])
def yf_manual(eid:int):
    with db() as c:
        if not external_source(c,eid):fail(404,'Подключение не найдено')
    # The worker does network I/O; no long HTTP request or blocked SSE loop.
    YF_DUE[(str(DB),eid)]=0
    with YF_QUEUE_LOCK: YF_MANUAL.add((str(DB),eid))
    return {'queued':True}

YF_MANUAL=set()
YF_QUEUE_LOCK=threading.Lock()
def yf_worker():
    while not YF_STOP.wait(1):
        try:
            with db() as c:
                rows=[dict(r) for r in c.execute('SELECT y.event_id,y.enabled,y.interval FROM yandex_source y JOIN settings s ON s.active_event=y.event_id WHERE s.id=1')]
            with YF_QUEUE_LOCK: manual=set(YF_MANUAL)
            candidates={(str(DB),r['event_id']) for r in rows if r['enabled']} | manual
            for key in candidates:
                if key[0]!=str(DB) or time.monotonic()<YF_DUE.get(key,0):continue
                with YF_QUEUE_LOCK: YF_MANUAL.discard(key)
                yf_sync(key[1])
            if len(YF_DUE)>1000:YF_DUE.clear()
        except Exception:
            YF_STOP.wait(5)

@app.on_event('startup')
def start_yandex_worker():
    YF_STOP.clear()
    threading.Thread(target=yf_worker,daemon=True,name='yandex-forms').start()

@app.on_event('shutdown')
def stop_yandex_worker():
    YF_STOP.set()

@app.get('/api/admin/yandex/events/{eid}/qr',dependencies=[Depends(admin)])
def yf_qr(eid:int):
    import qrcode
    import qrcode.image.svg
    with db() as c:
        row=external_source(c,eid)
        if not row:fail(404,'Подключение не найдено')
        sid=yf_call(yf.form_id,row['form_id'])
    out=io.BytesIO()
    qrcode.make('https://forms.yandex.ru/cloud/u/'+sid+'/',image_factory=qrcode.image.svg.SvgPathImage).save(out)
    return Response(out.getvalue(),media_type='image/svg+xml')

init()
