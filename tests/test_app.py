import os
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

os.environ['VOTEMERA_DB']=str(Path(tempfile.mkdtemp())/'initial.db')
os.environ['VOTEMERA_ADMIN_PASSWORD']='test-secret'
os.environ['VOTEMERA_TERMINAL_KEY']='test-key'
import app as a

AUTH={'Authorization':'Bearer test-secret'}
KEY={'X-Terminal-Key':'test-key'}
@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(a,'DB',tmp_path/'test.db'); a.init()
    return TestClient(a.app)
def activate(c,opened=True,display='live',idx=0):
    s=c.get('/api/admin/state',headers=AUTH).json(); e=s['event']; r=e['rounds'][idx]
    return c.post('/api/admin/control',headers=AUTH,json=dict(event=e['id'],round=r['id'],opened=opened,display=display)).json()
def payload(s,choice=0,terminal=None):
    return dict(round=s['round'],epoch=s['epoch'],choice=choice,nonce=uuid.uuid4().hex,terminal=terminal or uuid.uuid4().hex)
def submit(c,v):return c.post('/api/vote',json=v,headers=KEY)
def test_auth(client):
    assert client.get('/api/admin/state').status_code==401
    assert client.post('/api/vote',json=payload(activate(client))).status_code==403
    assert client.get('/health').json()['status']=='ok'
def test_votes_idempotent_cooldown(client):
    s=activate(client); v=payload(s)
    assert submit(client,v).status_code==200
    assert submit(client,v).json()['duplicate']
    assert submit(client,payload(s,1,v['terminal'])).status_code==429
    assert submit(client,payload(s,1)).status_code==200
    assert client.get('/api/state').json()['event']['rounds'][0]['counts']==[1,1]
def test_switch_stale_and_reopen(client):
    s=activate(client); v=payload(s); s2=activate(client,idx=1)
    assert submit(client,v).status_code==409
    assert submit(client,payload(s2)).status_code==200
    activate(client,False,idx=1); s3=activate(client,idx=1)
    assert submit(client,payload(s2)).status_code==409
    assert submit(client,payload(s3)).status_code==200
    assert s2['epoch']!=s3['epoch']
def test_hidden_and_compare(client):
    s=activate(client,display='hidden'); assert submit(client,payload(s)).status_code==200
    assert all('counts' not in r for r in client.get('/api/state').json()['event']['rounds'])
    assert client.get('/api/admin/state',headers=AUTH).json()['event']['rounds'][0]['total']==1
    activate(client,display='compare',idx=1)
    assert [r['total'] for r in client.get('/api/state').json()['event']['rounds']]==[1,0]
    assert all('counts' not in r for r in client.get('/api/state?view=vote').json()['event']['rounds'])
def test_concurrent(client):
    s=activate(client)
    with ThreadPoolExecutor(max_workers=12) as ex:
        results=list(ex.map(lambda i:submit(client,payload(s,i%2)).status_code,range(40)))
    assert results==[200]*40
    assert client.get('/api/state').json()['event']['rounds'][0]['counts']==[20,20]
def test_export_clear_and_edit(client):
    s=activate(client); e=s['event']; v=payload(s)
    assert submit(client,v).status_code==200
    assert client.delete(f'/api/admin/round/{s["round"]}/votes',headers=AUTH).status_code==409
    assert 'Голоса' in client.get(f'/api/admin/export/{e["id"]}',headers=AUTH).text
    new=dict(title='Тест',question='Вопрос',rounds=['А','Б'],options=e['options'])
    assert client.put(f'/api/admin/event/{e["id"]}',headers=AUTH,json=new).status_code==409
    activate(client,False)
    assert client.delete(f'/api/admin/round/{s["round"]}/votes',headers=AUTH).status_code==200
    assert client.put(f'/api/admin/event/{e["id"]}',headers=AUTH,json=new).status_code==200
    assert client.get('/api/admin/state',headers=AUTH).json()['event']['question']=='Вопрос'
def test_validation(client):
    s=activate(client); assert submit(client,payload(s,5)).status_code==422
    new=dict(title='Тест',question='Вопрос',rounds=['А','А'],options=s['event']['options'])
    assert client.post('/api/admin/event',headers=AUTH,json=new).status_code==422
    assert client.get('/vote').status_code==200
    assert client.get('/display').status_code==200

def multi(c):
    e=c.get('/api/admin/state',headers=AUTH).json()['event']
    opts=e['options']; qs=[{'question':'Первый вопрос','options':opts},{'question':'Второй вопрос','options':opts+[{'label':'Не знаю','color':'#123456'}]}]
    r=c.post('/api/admin/event',headers=AUTH,json={'title':'Анкета','rounds':['До','После'],'questions':qs})
    assert r.status_code==200
    data=r.json()
    return c.post('/api/admin/control',headers=AUTH,json={'event':data['id'],'round':data['round'],'opened':True,'display':'live'}).json()

def test_multi_ballot(client):
    s=multi(client); v=payload(s);v.pop('choice');v['choices']=[0,2]
    assert client.post('/api/ballot',headers=KEY,json=v).status_code==200
    assert client.post('/api/ballot',headers=KEY,json=v).json()['duplicate']
    assert client.post('/api/ballot',headers=KEY,json={**v,'choices':[1,2]}).status_code==409
    assert submit(client,payload(s)).status_code==409
    r=client.get('/api/state').json()['event']['rounds'][0]
    assert r['results']==[{'counts':[1,0],'total':1},{'counts':[0,0,1],'total':1}]
    assert 'Второй вопрос' in client.get('/api/admin/export/'+str(s['event']['id']),headers=AUTH).text
    assert 'results' not in client.get('/api/state?view=vote').json()['event']['rounds'][0]

def test_multi_invalid_atomic_and_stale(client):
    s=multi(client); v=payload(s);v.pop('choice');v['choices']=[0,4]
    assert client.post('/api/ballot',headers=KEY,json=v).status_code==422
    assert client.get('/api/state').json()['event']['rounds'][0]['total']==0
    assert client.post('/api/ballot',headers=KEY,json={**v,'choices':[0]}).status_code==422
    activate(client,idx=1)
    assert client.post('/api/ballot',headers=KEY,json={**v,'choices':[0,1]}).status_code==409

def test_multi_concurrent(client):
    s=multi(client)
    def go(i):
        v=payload(s);v.pop('choice');v['choices']=[i%2,i%3]
        return client.post('/api/ballot',headers=KEY,json=v).status_code
    with ThreadPoolExecutor(max_workers=12) as ex: assert list(ex.map(go,range(40)))==[200]*40
    assert client.get('/api/state').json()['event']['rounds'][0]['results']==[{'counts':[20,20],'total':40},{'counts':[14,13,13],'total':40}]

def test_migrate_legacy(client,monkeypatch,tmp_path):
    import sqlite3,json
    path=tmp_path/'legacy.db'; c=sqlite3.connect(path)
    c.executescript('CREATE TABLE events(id INTEGER PRIMARY KEY,title TEXT NOT NULL,question TEXT NOT NULL,options TEXT NOT NULL,created REAL NOT NULL); CREATE TABLE rounds(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL,name TEXT NOT NULL,position INTEGER NOT NULL); CREATE TABLE votes(id INTEGER PRIMARY KEY,round_id INTEGER NOT NULL,choice INTEGER NOT NULL,nonce TEXT NOT NULL UNIQUE,terminal TEXT NOT NULL,created REAL NOT NULL); CREATE TABLE settings(id INTEGER PRIMARY KEY,data TEXT NOT NULL,active_event INTEGER,active_round INTEGER,opened INTEGER NOT NULL DEFAULT 0,epoch TEXT NOT NULL DEFAULT "",display TEXT NOT NULL DEFAULT "live");')
    c.execute('INSERT INTO events VALUES(1,?,?,?,0)',('Старое','Вопрос',json.dumps([{'label':'Да','color':'#123456'},{'label':'Нет','color':'#654321'}])))
    c.execute('INSERT INTO rounds VALUES(1,1,"До",0)');c.execute('INSERT INTO votes VALUES(1,1,0,"nonce","terminal",0)');c.execute('INSERT INTO settings(id,data,active_event,active_round) VALUES(1,"{}",1,1)');c.commit();c.close()
    monkeypatch.setattr(a,'DB',path);a.init();a.init()
    assert client.get('/api/state').json()['event']['rounds'][0]['results']==[{'counts':[1,0],'total':1}]

def send_ballot(c,s,choices):
    v=payload(s);v.pop('choice');v['choices']=choices
    assert c.post('/api/ballot',headers=KEY,json=v).status_code==200
    return v

def test_cross_exact_pairs_and_visibility(client):
    s=multi(client); eid=s['event']['id']
    for choices in [[0,0],[0,0],[0,2],[1,1],[1,2]]: send_ballot(client,s,choices)
    assert 'cross' not in client.get('/api/state').json()['event']['rounds'][0]
    assert client.put(f'/api/admin/analysis/{eid}',json={'first':0,'second':1}).status_code==401
    assert client.put(f'/api/admin/analysis/{eid}',headers=AUTH,json={'first':0,'second':0}).status_code==422
    assert client.put(f'/api/admin/analysis/{eid}',headers=AUTH,json={'first':0,'second':3}).status_code==422
    activate(client,display='analysis')
    r=client.get('/api/state').json()['event']['rounds'][0]
    assert r['cross']=={'matrix':[[2,0,1],[0,1,1]],'totals':[3,2],'total':5,'unpaired':0}
    assert all('cross' not in x for x in client.get('/api/state?view=vote').json()['event']['rounds'])
    activate(client,display='hidden')
    assert all('cross' not in x for x in client.get('/api/state').json()['event']['rounds'])
    assert client.put(f'/api/admin/analysis/{eid}',headers=AUTH,json={'first':1,'second':0,'chart':'heatmap'}).status_code==200
    s=activate(client,display='analysis_compare',idx=1)
    send_ballot(client,s,[1,0]); result=client.get('/api/state').json()['event']
    assert result['analysis']=={'first':1,'second':0,'chart':'heatmap'}
    assert [r['cross']['matrix'] for r in result['rounds']]==[[[2,0],[0,1],[1,1]],[[0,1],[0,0],[0,0]]]
    activate(client,False,display='analysis_compare',idx=1)
    assert client.delete(f'/api/admin/round/{s["round"]}/votes',headers=AUTH).status_code==200
    assert client.get('/api/state').json()['event']['rounds'][1]['cross']['total']==0

def test_cross_migrate_exact_ballot_nonces(client):
    s=multi(client)
    send_ballot(client,s,[0,2]);send_ballot(client,s,[1,1])
    with a.db(True) as c:
        c.execute('DROP INDEX votes_ballot');c.execute('ALTER TABLE votes DROP COLUMN ballot_id')
        # Same terminal/time is not enough to match a deliberately corrupted old nonce.
        c.execute('UPDATE votes SET nonce=? WHERE question_index=1 AND choice=1',(uuid.uuid4().hex,))
    a.init();a.init();activate(client,display='analysis')
    d=client.get('/api/state').json()['event']['rounds'][0]['cross']
    assert d=={'matrix':[[0,0,1],[0,0,0]],'totals':[1,0],'total':1,'unpaired':1}

def test_question_and_display_settings(client):
    s=client.get('/api/admin/state',headers=AUTH).json(); e=s['event'];cfg=s['settings']
    cfg.update(display_page_size=2,display_rotate=0,vote_layout='list')
    assert client.put('/api/admin/settings',headers=AUTH,json=cfg).status_code==200
    assert client.get('/api/state').json()['settings']['vote_layout']=='list'
    assert client.put('/api/admin/settings',headers=AUTH,json={**cfg,'display_page_size':3}).status_code==422
    q={'question':'Тест','description':'Пояснение','confirm':True,'options':e['options']}
    assert client.put(f'/api/admin/event/{e["id"]}',headers=AUTH,json={'title':'Новый','rounds':['До','После'],'questions':[q]}).status_code==200
    assert client.get('/api/state?view=vote').json()['event']['questions']==[q]

def test_motion_settings_compatible(client):
    cfg=client.get('/api/admin/state',headers=AUTH).json()['settings']
    assert cfg.pop('motion')=='full'
    # Clients from previous versions can still save their settings.
    assert client.put('/api/admin/settings',headers=AUTH,json=cfg).status_code==200
    for motion in ('subtle','off','full'):
        assert client.put('/api/admin/settings',headers=AUTH,json={**cfg,'motion':motion}).status_code==200
        for view in ('vote','display'):
            assert client.get('/api/state',params={'view':view}).json()['settings']['motion']==motion
    assert client.put('/api/admin/settings',headers=AUTH,json={**cfg,'motion':'invalid'}).status_code==422

def test_maintenance_update_and_backup(client,monkeypatch,tmp_path):
    status=tmp_path/'status'; request=tmp_path/'request'
    monkeypatch.setattr(a,'UPDATE_STATUS',status);monkeypatch.setattr(a,'UPDATE_REQUEST',request)
    assert client.post('/api/admin/update',headers=AUTH).status_code==503
    status.write_text('idle')
    assert client.post('/api/admin/update').status_code==401
    activate(client)
    assert client.post('/api/admin/update',headers=AUTH).status_code==409
    activate(client,False)
    assert client.post('/api/admin/update',headers=AUTH).status_code==200
    assert request.exists()
    assert client.get('/api/admin/update',headers=AUTH).json()['status']=='queued'
    assert client.post('/api/admin/update',headers=AUTH).status_code==409
    s=client.get('/api/admin/state',headers=AUTH).json()
    assert client.post('/api/admin/control',headers=AUTH,json=dict(event=s['event']['id'],round=s['round'],opened=True,display='live')).status_code==409
    assert client.get('/api/admin/backup').status_code==401
    assert client.get('/api/admin/backup',headers=AUTH).content.startswith(b'SQLite format 3')
def test_cross_export(client):
    s=multi(client);send_ballot(client,s,[0,2])
    r=client.get('/api/admin/cross-export/'+str(s['event']['id']),headers=AUTH)
    assert r.status_code==200 and 'Размер группы' in r.text
def test_admin_rate_limit(client):
    a.AUTH_FAILURES.clear()
    try:
        for i in range(10): assert client.get('/api/admin/state').status_code==401
        assert client.get('/api/admin/state').status_code==429
    finally: a.AUTH_FAILURES.clear()

def test_terminal_monitor_and_idle_timeout(client):
    a.TERMINALS.clear()
    t=uuid.uuid4().hex
    assert client.post('/api/terminal/heartbeat',json={'terminal':t}).status_code==403
    assert client.post('/api/terminal/heartbeat',headers=KEY,json={'terminal':t}).status_code==200
    assert client.get('/api/admin/terminals').status_code==401
    r=client.get('/api/admin/terminals',headers=AUTH).json()
    assert r['online']==1 and r['terminals'][0]['id']==t[-8:]
    cfg=client.get('/api/admin/state',headers=AUTH).json()['settings']
    assert cfg['idle_timeout']==90
    cfg['idle_timeout']=15
    assert client.put('/api/admin/settings',headers=AUTH,json=cfg).status_code==200
    assert client.get('/api/state?view=vote').json()['settings']['idle_timeout']==15
    cfg['idle_timeout']=601
    assert client.put('/api/admin/settings',headers=AUTH,json=cfg).status_code==422

def test_presentation_and_rehearsal(client):
    s=multi(client);eid=s['event']['id']
    send_ballot(client,s,[0,2])
    config=dict(order=[1,0],slides=[{'mode':'compare','question':1},{'mode':'analysis'}],seconds=10)
    assert client.put(f'/api/admin/presentation/{eid}',headers=AUTH,json=config).status_code==200
    assert client.put(f'/api/admin/presentation/{eid}',headers=AUTH,json={**config,'order':[1,1]}).status_code==422
    activate(client,display='presentation')
    public=client.get('/api/state').json()['event']
    assert public['presentation']['order']==[1,0]
    assert all('results' in r and 'cross' in r for r in public['rounds'])
    rehearsal=client.post(f'/api/admin/rehearsal/{eid}',headers=AUTH).json()
    assert rehearsal['id']!=eid
    copy=client.get(f'/api/admin/event/{rehearsal["id"]}',headers=AUTH).json()
    assert copy['title'].startswith('[Репетиция]') and copy['questions']==s['event']['questions']
    result=client.post('/api/admin/control',headers=AUTH,json=dict(event=rehearsal['id'],round=rehearsal['round'],opened=False,display='live')).json()
    assert all(r['total']==0 for r in result['event']['rounds'])
    assert result['event']['presentation']['order']==[1,0]

def test_conditional_questions(client):
    s=client.get('/api/admin/state',headers=AUTH).json(); opts=s['event']['options']
    qs=[dict(question='Первый',options=opts),dict(question='Только если нет',options=opts,show_if={'question':0,'choice':1}),dict(question='Третий',options=opts)]
    d=dict(title='Условная анкета',rounds=['До','После'],questions=qs)
    bad={**d,'questions':[dict(qs[0],show_if={'question':0,'choice':0}),*qs[1:]]}
    assert client.post('/api/admin/event',headers=AUTH,json=bad).status_code==422
    eid=client.post('/api/admin/event',headers=AUTH,json=d).json()
    s=client.post('/api/admin/control',headers=AUTH,json=dict(event=eid['id'],round=eid['round'],opened=True,display='live')).json()
    v=send_ballot(client,s,[0,None,1])
    assert client.post('/api/ballot',headers=KEY,json=v).json()['duplicate']
    assert client.post('/api/ballot',headers=KEY,json={**v,'choices':[0,0,1]}).status_code==409
    send_ballot(client,s,[1,0,1])
    invalid={**payload(s),'choices':[1,None,0]};invalid.pop('choice')
    assert client.post('/api/ballot',headers=KEY,json=invalid).status_code==422
    r=client.get('/api/state').json()['event']['rounds'][0]
    assert r['total']==2 and r['results'][1]['total']==1
    activate(client,display='analysis')
    cross=client.get('/api/state').json()['event']['rounds'][0]['cross']
    assert cross['total']==1 and cross['unpaired']==1

def test_restore_backup(client):
    s=activate(client)
    assert submit(client,payload(s)).status_code==200
    backup=client.get('/api/admin/backup',headers=AUTH).content
    assert client.post('/api/admin/restore',headers=AUTH,content=backup).status_code==409
    activate(client,False)
    assert client.delete(f'/api/admin/round/{s["round"]}/votes',headers=AUTH).status_code==200
    assert client.post('/api/admin/restore',content=backup).status_code==401
    assert client.post('/api/admin/restore',headers=AUTH,content=b'invalid').status_code==422
    assert client.post('/api/admin/restore',headers=AUTH,content=backup).status_code==200
    fresh=client.get('/api/admin/state',headers=AUTH).json()
    assert not fresh['opened'] and fresh['display']=='hidden'
    assert fresh['event']['rounds'][0]['total']==1
    assert all('counts' not in r for r in client.get('/api/state').json()['event']['rounds'])
    assert list((a.DB.parent/'restore-backups').glob('*.db'))
