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
import logging
import os
import re
import sqlite3
import time
import subprocess
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager, nullcontext, closing
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

import operator_sessions as sessions
from backup_restore import prepare_data, schema
from poll_results import (analysis_config, cross_results, external_round, external_source, meta,
                          presentation_config, questions, results)
from storage_schema import ACCESS_MIGRATIONS, DATA_MIGRATIONS, migrate
from voting import deadline, is_open, record_ballot, record_vote, terminal_locked

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger(__name__)
VERSION = (ROOT / 'VERSION').read_text().strip()
DB = Path(os.getenv('VOTEMERA_DB', str(ROOT / 'data' / 'votemera.db')))
ADMIN = os.getenv('VOTEMERA_ADMIN_PASSWORD', '')
TERMINAL = os.getenv('VOTEMERA_TERMINAL_KEY', '')
UPDATE_STATUS = Path('/var/lib/votemera-update/status')
UPDATE_REQUEST = Path('/var/lib/votemera/update-request')
UPDATE_REMOTE = 'https://github.com/TeenKode/votemera.git'
AUTH_FAILURES = OrderedDict()
AUTH_LOCK = threading.Lock()
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
        migrate(c, ACCESS_MIGRATIONS)
        if c.execute('SELECT 1 FROM access_settings WHERE id=1').fetchone(): return
        salt=secrets.token_bytes(32); key_salt=secrets.token_bytes(32)
        c.execute('INSERT INTO access_settings VALUES(1,?,?,?,?)',(salt,password_hash(ADMIN,salt) if ADMIN else '',key_salt,int(bool(TERMINAL))))
        if TERMINAL:
            c.execute('INSERT INTO terminal_keys(label,digest,created,device_id) VALUES(?,?,?,?)',
                      ('Первоначальный ключ',password_hash(TERMINAL,key_salt,KEY_ROUNDS),time.time(),uuid4().hex))

def access_required():
    with access_db() as c:
        return bool(c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0])

def verify_admin(value):
    with access_db() as c:
        row=c.execute('SELECT password_salt,password_hash FROM access_settings WHERE id=1').fetchone()
    if not row['password_hash']: fail(503,'Пароль администратора не настроен на сервере')
    if len(value)>512: return False
    return secret_eq(password_hash(value,row['password_salt']),row['password_hash'])

def replace_password(c, value):
    salt=secrets.token_bytes(32)
    c.execute('UPDATE access_settings SET password_salt=?,password_hash=? WHERE id=1',(salt,password_hash(value,salt)))
    c.execute('DELETE FROM access_sessions')

def set_admin_password(value):
    with access_db(True) as c:
        replace_password(c,value)

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
    with db(True) as c:
        migrate(c, DATA_MIGRATIONS)
        c.execute('INSERT OR IGNORE INTO settings(id,data) VALUES(1,?)',(json.dumps(DEFAULTS),))
        if not c.execute('SELECT 1 FROM events LIMIT 1').fetchone():
            e=c.execute('INSERT INTO events(title,question,options,created) VALUES(?,?,?,?)',('Педагогический совет','Готовы ли вы использовать искусственный интеллект в профессиональной деятельности?',json.dumps([{'label':'Да','color':COLORS[0]},{'label':'Нет','color':COLORS[1]}]),time.time())).lastrowid
            r=c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'До педсовета',0)).lastrowid
            c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,?)',(e,'После педсовета',1))
            c.execute('UPDATE settings SET active_event=?,active_round=? WHERE id=1 AND active_event IS NULL',(e,r))
    init_access()

def fail(status,msg): raise HTTPException(status,msg)
def secret_eq(a,b): return hmac.compare_digest(a.encode(),b.encode())
def admin(request: Request):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        sessions.check_origin(request)
    token = request.cookies.get(sessions.COOKIE, '')
    if token:
        with access_db() as c:
            if sessions.verify(c, token): return
    fail(401, 'Войдите в панель администратора')

def new_admin_session(request, password):
    address=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    with AUTH_LOCK, ACCESS_LOCK:
        n,until=AUTH_FAILURES.get(address,(0,0))
        if until<=now: n=0; until=now+60
        if n>=10: fail(429,'Слишком много попыток входа. Повторите через минуту.')
        if not verify_admin(password):
            AUTH_FAILURES[address]=(n+1,until)
            AUTH_FAILURES.move_to_end(address)
            while len(AUTH_FAILURES)>4096: AUTH_FAILURES.popitem(last=False)
            fail(401,'Неверный пароль')
        AUTH_FAILURES.pop(address,None)
        with access_db(True) as c:
            return sessions.issue(c)

def terminal(x_terminal_key: str = Header(default='')):
    with access_db() as c:
        required=c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0]
        if not required: return None
        if not x_terminal_key or len(x_terminal_key)>512: fail(403,'Неверный ключ терминала')
        salt=c.execute('SELECT key_salt FROM access_settings WHERE id=1').fetchone()[0]
        fingerprint=(hashlib.sha256(x_terminal_key.encode()).digest(),salt)
        with KEY_CACHE_LOCK: digest=VERIFIED_KEYS.get(fingerprint)
        if digest is None: digest=password_hash(x_terminal_key,salt,KEY_ROUNDS)
        row=c.execute('SELECT id,device_id FROM terminal_keys WHERE digest=? AND enabled=1',(digest,)).fetchone()
        if not row: fail(403,'Неверный или отключённый ключ терминала')
        with KEY_CACHE_LOCK:
            VERIFIED_KEYS[fingerprint]=digest
            while len(VERIFIED_KEYS)>128: VERIFIED_KEYS.popitem(last=False)
        return row

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

class AdminLogin(BaseModel):
    password: str = Field(max_length=512)

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

def audit(c,action,detail=''):
    c.execute('INSERT INTO audit_log(created,action,detail) VALUES(?,?,?)',(time.time(),action,str(detail)[:500]))
    c.execute('DELETE FROM audit_log WHERE id <= (SELECT max(id)-5000 FROM audit_log)')

def effective_open_for(s, end, *, now=None):
    return is_open(s, end, time.time() if now is None else now)

def effective_open(c,s):
    return effective_open_for(s, deadline(c))

def snapshot(c, private=False, voting=False, catalogue=True):
    s=c.execute('SELECT * FROM settings WHERE id=1').fetchone()
    cfg=DEFAULTS | json.loads(s['data'])
    e=c.execute('SELECT * FROM events WHERE id=?',(s['active_event'],)).fetchone()
    end=deadline(c)
    result=dict(settings=cfg, event=None, round=s['active_round'], opened=effective_open_for(s,end), deadline=end, epoch=s['epoch'], display=s['display'], version=VERSION, terminal_key_required=access_required())
    if e:
        qs=questions(e); options=qs[0]['options']; rounds=[]; ac=analysis_config(c,e['id'],qs); m=meta(c,e['id']); ext=external_source(c,e['id'])
        if ext:
            late = ext['last_sync'] is not None and time.time() - ext['last_sync'] > max(30, 2 * ext['interval'])
            result['external'] = dict(provider='yandex', url='https://forms.yandex.ru/cloud/u/'+ext['form_id']+'/',
                                      last_sync=ext['last_sync'], stale=bool(ext['error']) or not ext['enabled'] or late,
                                      enabled=bool(ext['enabled']))
            result['opened'] = False
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
    if private and catalogue:
        result['events']=[dict(x) for x in c.execute('SELECT e.id,e.title,e.question,e.created,coalesce(m.archived,0) archived,coalesce(m.template,0) template,(SELECT count(*) FROM rounds WHERE event_id=e.id) AS round_count,(SELECT count(*) FROM votes v JOIN rounds r ON r.id=v.round_id WHERE r.event_id=e.id AND v.question_index=0) AS total FROM events e LEFT JOIN event_meta m ON m.event_id=e.id ORDER BY e.id DESC')]
        for item in result['events']:
            ext=external_source(c,item['id'])
            if ext: item.update(source='yandex',total=json.loads(ext['aggregate'])['total'])
    return result

@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    worker = threading.Thread(target=yf_worker, args=(stop,), daemon=True, name='yandex-forms')
    worker.start()
    try:
        yield
    finally:
        stop.set()

app=FastAPI(title='Votemera',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
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
    return Response(stream_snapshot(view), media_type='application/json')

def stream_snapshot(view):
    # Coalesce expensive aggregate reads across simultaneous displays/terminals.
    with STREAM_LOCK:
        key=(str(DB),view,STREAM_REVISION); now=time.monotonic(); cached=STREAM_CACHE.get(key)
        if cached and now-cached[0]<.20: return cached[1]
        with db() as c:
            data=json.dumps(snapshot(c,private=view=='admin',voting=view=='vote',catalogue=False),
                            ensure_ascii=False,separators=(',',':'))
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
@app.post('/api/vote')
def vote(v: Vote, key=Depends(terminal)):
    device_id = key['device_id'] if key else v.terminal
    with db(True) as c:
        return record_vote(c, v, device_id, time.time(), DEFAULTS['cooldown'])


@app.post('/api/ballot')
def ballot(v: Ballot, key=Depends(terminal)):
    device_id = key['device_id'] if key else v.terminal
    with db(True) as c:
        return record_ballot(c, v, device_id, time.time(), DEFAULTS['cooldown'])


@app.post('/api/terminal/heartbeat')
def heartbeat(v: TerminalHeartbeat, key=Depends(terminal)):
    device_id = key['device_id'] if key else v.terminal
    now = time.monotonic()
    with TERMINALS_LOCK:
        TERMINALS[device_id] = (now, key['id'] if key else None)
        TERMINALS.move_to_end(device_id)
        while TERMINALS and (len(TERMINALS)>4096 or next(iter(TERMINALS.values()))[0]<now-300):
            TERMINALS.popitem(last=False)
    with db() as c:
        t = c.execute('SELECT * FROM terminal_config WHERE id=?', (device_id,)).fetchone()
        e = c.execute('SELECT active_event FROM settings WHERE id=1').fetchone()[0]
        return {'ok': True, 'device_id': device_id, 'locked': terminal_locked(t, e),
                'revision': t['revision'] if t else 0, 'label': t['label'] if t else ''}


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

@app.post('/api/admin/login')
def login(v: AdminLogin, request: Request, response: Response):
    sessions.check_origin(request)
    token = new_admin_session(request, v.password)
    sessions.set_cookie(response, request, token)
    return {'ok': True}


@app.post('/api/admin/logout')
def logout(request: Request, response: Response):
    sessions.check_origin(request)
    fingerprint = sessions.token_hash(request.cookies.get(sessions.COOKIE, ''))
    if fingerprint:
        with access_db(True) as c:
            c.execute('DELETE FROM access_sessions WHERE token_hash=?', (fingerprint,))
    sessions.clear_cookie(response, request)
    return {'ok': True}


@app.put('/api/admin/password',dependencies=[Depends(admin)])
def change_password(v: PasswordChange, request: Request, response: Response):
    value=checked_secret(v.password)
    with ACCESS_LOCK:
        if not verify_admin(v.current): fail(403,'Текущий пароль неверен')
        with access_db(True) as c:
            replace_password(c, value)
            token = sessions.issue(c)
    with db(True) as c: audit(c,'Смена пароля администратора')
    sessions.set_cookie(response, request, token)
    return {'ok':True}

@app.get('/api/admin/keys',dependencies=[Depends(admin)])
def list_keys():
    with access_db() as c:
        return {'required':bool(c.execute('SELECT required FROM access_settings WHERE id=1').fetchone()[0]),'keys':[dict(r) for r in c.execute('SELECT id,label,created,enabled,device_id FROM terminal_keys ORDER BY id')]}

@app.post('/api/admin/keys',dependencies=[Depends(admin)])
def create_key(v:KeyCreate):
    value=checked_secret(v.key if v.key is not None else secrets.token_hex(16))
    device_id = uuid4().hex
    with access_db(True) as c:
        if c.execute('SELECT count(*) FROM terminal_keys').fetchone()[0]>=100: fail(409,'Максимум 100 ключей')
        try:
            kid=c.execute('INSERT INTO terminal_keys(label,digest,created,device_id) VALUES(?,?,?,?)',
                          (clean(v.label),terminal_digest(c,value),time.time(),device_id)).lastrowid
        except sqlite3.IntegrityError: fail(409,'Такой ключ уже существует')
        c.execute('UPDATE access_settings SET required=1 WHERE id=1')
    with db(True) as c: audit(c,'Создание ключа терминала',kid)
    return {'id':kid,'key':value,'device_id':device_id}

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

@app.get('/api/admin/live', dependencies=[Depends(admin)])
def admin_live_state():
    return Response(stream_snapshot('admin'), media_type='application/json')
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

def apply_control(c, s, eid, rid, opened, display, duration):
    stage_changed = (eid, rid) != (s['active_event'], s['active_round'])
    change_voting = stage_changed or opened is not None or duration is not None
    end = deadline(c)
    now = time.time()
    was_open = effective_open_for(s, end, now=now)
    if opened is None:
        opened = was_open

    if not c.execute('SELECT 1 FROM events WHERE id=?', (eid,)).fetchone():
        fail(404, 'Мероприятие не найдено')
    if meta(c, eid)['archived']:
        fail(409, 'Сначала восстановите мероприятие из архива')
    if external_source(c, eid) and (opened or display not in ('live', 'hidden')):
        fail(409, 'Для Яндекс Форм используйте текущие результаты или скрытие; сбор ответов управляется в Яндексе')
    if change_voting and opened and update_busy():
        fail(409, 'Дождитесь завершения обновления')
    if not c.execute('SELECT 1 FROM rounds WHERE id=? AND event_id=?', (rid, eid)).fetchone():
        fail(422, 'Неверный этап')

    changed = stage_changed or opened != was_open
    epoch = uuid4().hex if changed else s['epoch']
    if change_voting and (not opened or changed or duration is not None):
        end = now + duration if opened and duration else None
        c.execute('UPDATE runtime_config SET deadline=? WHERE id=1', (end,))
    if changed or s['display'] != display or duration is not None:
        audit(c, 'Управление этапом', f'Мероприятие {eid}, этап {rid}, открыто={opened}, экран={display}')
    c.execute('UPDATE settings SET active_event=?,active_round=?,opened=?,epoch=?,display=? WHERE id=1',
              (eid, rid, int(opened) if change_voting else s['opened'], epoch, display))
    return dict(event_id=eid, round=rid, opened=effective_open_for({'opened': opened}, end),
                epoch=epoch, display=display, deadline=end)

@app.post('/api/admin/control',dependencies=[Depends(admin)])
def control(v:Control):
    with db(True) as c:
        s = c.execute('SELECT * FROM settings WHERE id=1').fetchone()
        return apply_control(c, s, v.event, v.round, v.opened, v.display, v.duration)
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
        order=set(v.order)
        if len(order)!=len(v.order) or any(not 0<=i<n for i in order): fail(422,'Неверный порядок вопросов')
        if any(s.question>=n or (s.mode.startswith('analysis') and n<2) for s in v.slides): fail(422,'Неверный слайд')
        if order and any(s.question>=0 and s.question not in order for s in v.slides): fail(422,'Слайд ссылается на скрытый вопрос')
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
    directory = Path(tempfile.mkdtemp(prefix='votemera-backup-'))
    target = directory/'backup.db'
    try:
        with db(transaction=False) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        return FileResponse(target, media_type='application/x-sqlite3', filename='votemera-backup.db',
                            background=BackgroundTask(shutil.rmtree, directory))
    except BaseException:
        shutil.rmtree(directory)
        raise

def restore_database(path):
    global STREAM_REVISION
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
                source.execute('BEGIN IMMEDIATE')
                prepare_data(source, expected_schema)
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
    with db(True) as c:
        s = c.execute('SELECT * FROM settings WHERE id=1').fetchone()
        rid = s['active_round'] if v.round is None else v.round
        display = s['display'] if v.display is None else v.display
        return apply_control(c, s, s['active_event'], rid, v.opened, display, v.duration)

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
YF_QUEUE_LOCK=threading.Lock()
YF_DUE={}

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

def yf_period(start, end):
    try:
        start, end = yf.validate_date(start), yf.validate_date(end)
    except yf.FormsError as exc:
        fail(422, str(exc))
    if start and end and datetime.fromisoformat(start) > datetime.fromisoformat(end):
        fail(422, 'Начало периода позже окончания')
    return start, end

@app.get('/api/admin/yandex',dependencies=[Depends(admin)])
def yf_status():
    creds=yf_credentials()
    with db() as c:
        rows = c.execute('''SELECT y.*, e.title, j.state AS sync_state
            FROM yandex_source y JOIN events e ON e.id=y.event_id
            LEFT JOIN yandex_sync_jobs j ON y.event_id=j.event_id ORDER BY e.id DESC''')
        sources=[dict(event_id=r['event_id'],title=r['title'],form_id=r['form_id'],interval=r['interval'],enabled=bool(r['enabled']),last_sync=r['last_sync'],error=r['error'],total=json.loads(r['aggregate'])['total'],date_from=r['date_from'],date_to=r['date_to'],sync_state=r['sync_state']) for r in rows]
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
    with WRITE_LOCK, access_db(True) as c:
        c.execute('INSERT OR REPLACE INTO yandex_credentials VALUES(1,?,?,?)',(org,token,uuid4().hex))
    return {'ok':True}

@app.delete('/api/admin/yandex',dependencies=[Depends(admin)])
def yf_disconnect():
    with WRITE_LOCK:
        with access_db(True) as c:c.execute('DELETE FROM yandex_credentials')
        with db(True) as c:
            c.execute("UPDATE yandex_source SET enabled=0,revision=?,error='Подключение отключено'",(uuid4().hex,))
            c.execute('DELETE FROM yandex_sync_jobs')
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
    start,end=yf_period(v.date_from,v.date_to)
    available={q['id']:q for q in yf_call(yf.questions,creds['org'],creds['token'],v.form_id)}
    if len(set(v.questions))!=len(v.questions) or any(q not in available or not available[q]['supported'] for q in v.questions):fail(422,'Выберите поддерживаемые вопросы')
    spec=[available[q] for q in v.questions]
    qs=[{'question':q['label'],'options':q['options'],'description':'Множественный выбор: доля ответивших' if q['multi'] else '', 'confirm':False,'multi':q['multi']} for q in spec]
    # Check response access before creating the event.
    yf_call(yf.request,creds['org'],creds['token'],'/v1/surveys/'+v.form_id.lower()+'/answers',{'page_size':1,'questions':','.join(str(q) for q in v.questions)})
    ag={'total':0,'results':[{'total':0,'counts':[0]*len(q['options'])} for q in qs]}
    with db(True) as c:
        if (yf_credentials() or {}).get('revision') != creds['revision']:
            fail(409, 'Подключение изменилось. Повторите создание мероприятия.')
        eid=c.execute('INSERT INTO events(title,question,options,created,questions) VALUES(?,?,?,?,?)',(clean(v.title),qs[0]['question'],json.dumps(qs[0]['options'],ensure_ascii=False),time.time(),json.dumps(qs,ensure_ascii=False))).lastrowid
        rid=c.execute('INSERT INTO rounds(event_id,name,position) VALUES(?,?,0)',(eid,'Ответы Яндекс Форм')).lastrowid
        c.execute('INSERT INTO yandex_source VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(eid,creds['org'],v.form_id.lower(),json.dumps(spec,ensure_ascii=False),start,end,v.interval,uuid4().hex,json.dumps(ag),None,'',1))
        audit(c,'Подключение Яндекс Формы',eid)
    return {'id':eid,'round':rid}

def yf_sync(eid, *, automatic_revision=None):
    with YF_LOCK:
        with db() as c:
            row = external_source(c, eid)
            if not row:
                return False
            if automatic_revision is not None:
                active = c.execute('SELECT active_event FROM settings WHERE id=1').fetchone()[0]
                if eid != active or not row['enabled'] or row['revision'] != automatic_revision:
                    return False
        creds = yf_credentials()
        error = ''
        try:
            if not creds or creds['org'] != row['org']:
                raise yf.FormsError('Нет подключения к организации формы')
            aggregate = yf.aggregate(creds['org'], creds['token'], row['form_id'], json.loads(row['spec']),
                                     row['date_from'], row['date_to'])
        except yf.FormsError as exc:
            aggregate = None
            error = str(exc)
        except Exception:
            aggregate = None
            error = 'Неожиданный формат данных Яндекс Форм'
        with db(True) as c:
            current = yf_credentials()
            if (current or {}).get('revision') != (creds or {}).get('revision'):
                return False
            if aggregate is None:
                updated = c.execute('UPDATE yandex_source SET error=? WHERE event_id=? AND revision=?',
                                    (error, eid, row['revision']))
            else:
                updated = c.execute('UPDATE yandex_source SET aggregate=?,last_sync=?,error="" WHERE event_id=? AND revision=?',
                                    (json.dumps(aggregate), time.time(), eid, row['revision']))
            if not updated.rowcount:
                return False
        delay = max(row['interval'], 60) if aggregate is None else row['interval']
        with YF_QUEUE_LOCK:
            YF_DUE[(str(DB), eid)] = (row['revision'], time.monotonic() + delay)
        return aggregate is not None

@app.put('/api/admin/yandex/events/{eid}',dependencies=[Depends(admin)])
def yf_settings(eid:int,v:YandexSettings):
    with db(True) as c:
        row=external_source(c,eid)
        if not row:fail(404,'Подключение не найдено')
        start,end=yf_period(row['date_from'] if v.date_from is None else v.date_from,
                            row['date_to'] if v.date_to is None else v.date_to)
        if (start,end)!=(row['date_from'],row['date_to']):
            # Never present statistics from the old period as statistics for the new period.
            empty={'total':0,'results':[{'total':0,'counts':[0]*len(q['options'])} for q in json.loads(row['spec'])]}
            c.execute('UPDATE yandex_source SET aggregate=?,last_sync=NULL,error=? WHERE event_id=?',(json.dumps(empty),'Период изменён; требуется синхронизация',eid))
        c.execute('UPDATE yandex_source SET interval=?,enabled=?,revision=?,date_from=?,date_to=? WHERE event_id=?',(v.interval,int(v.enabled),uuid4().hex,start,end,eid))
    return {'ok':True}

@app.post('/api/admin/yandex/events/{eid}/sync',dependencies=[Depends(admin)])
def yf_manual(eid:int):
    with db(True) as c:
        if not external_source(c,eid):fail(404,'Подключение не найдено')
        c.execute("""INSERT INTO yandex_sync_jobs(event_id,request_id,state,requested)
            VALUES(?,?,'queued',?) ON CONFLICT(event_id) DO UPDATE SET
            request_id=excluded.request_id,state='queued',requested=excluded.requested,finished=NULL""",
            (eid, uuid4().hex, time.time()))
    return {'queued':True}

def yf_sync_pending(stop):
    database = str(DB)
    with db() as c:
        active = c.execute('''SELECT y.event_id,y.revision FROM yandex_source y
            JOIN settings s ON s.active_event=y.event_id WHERE s.id=1 AND y.enabled=1''').fetchone()
        manual = c.execute("""SELECT event_id,request_id FROM yandex_sync_jobs
            WHERE state IN ('queued','running') ORDER BY requested""").fetchall()
    for job in manual:
        if stop.is_set():
            return
        with db(True) as c:
            claimed = c.execute("""UPDATE yandex_sync_jobs SET state='running'
                WHERE event_id=? AND request_id=? AND state IN ('queued','running')""",
                (job['event_id'], job['request_id'])).rowcount
        if not claimed:
            continue
        success = yf_sync(job['event_id'])
        with db(True) as c:
            c.execute('UPDATE yandex_sync_jobs SET state=?,finished=? WHERE event_id=? AND request_id=?',
                      ('success' if success else 'failed', time.time(), job['event_id'], job['request_id']))

    if stop.is_set() or not active or any(job['event_id'] == active['event_id'] for job in manual):
        return
    with YF_QUEUE_LOCK:
        if len(YF_DUE) > 1000:
            YF_DUE.clear()
        revision, due = YF_DUE.get((database, active['event_id']), (None, 0))
    if revision != active['revision'] or time.monotonic() >= due:
        yf_sync(active['event_id'], automatic_revision=active['revision'])

def yf_worker(stop):
    while not stop.wait(1):
        try:
            yf_sync_pending(stop)
        except Exception:
            LOG.exception('Yandex Forms background sync failed')
            stop.wait(5)

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
