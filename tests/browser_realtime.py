"""Real SSE and fallback regression, including simultaneous independent terminals."""
import os,time,uuid,json
from concurrent.futures import ThreadPoolExecutor
import httpx
from playwright.sync_api import sync_playwright,expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
AUTH={'Authorization':'Bearer test-secret'}
KEY={'X-Terminal-Key':'test-key'}

def submit(s,choices,nonce=None,terminal=None):
    with httpx.Client(timeout=15) as c:
        return c.post(BASE+'/api/ballot',headers=KEY,json={'round':s['round'],'epoch':s['epoch'],'choices':choices,'nonce':nonce or uuid.uuid4().hex,'terminal':terminal or uuid.uuid4().hex})

def next_event(lines):
    kind='message';data=[]
    for line in lines:
        if not line:
            if data:return kind,json.loads('\n'.join(data))
            kind='message'
        elif line.startswith('event:'):kind=line[6:].strip()
        elif line.startswith('data:'):data.append(line[5:].strip())
    raise AssertionError('SSE ended unexpectedly')

with httpx.Client(timeout=10) as api:
    cfg=api.get(BASE+'/api/admin/state',headers=AUTH).json()['settings']
    cfg.update(vote_style='contrast',vote_layout='auto',motion='off',display_rotate=0,display_page_size=1)
    assert api.put(BASE+'/api/admin/settings',headers=AUTH,json=cfg).is_success
    opts=[{'label':'Да','color':'#0f9f78'},{'label':'Нет','color':'#df5363'}]
    e=api.post(BASE+'/api/admin/event',headers=AUTH,json={'title':'Проверка реального времени','rounds':['До','После'],'questions':[{'question':'Готовы ли вы использовать ИИ в профессиональной деятельности?', 'options':opts}]}).json()
    s=api.post(BASE+'/api/admin/control',headers=AUTH,json=dict(event=e['id'],round=e['round'],opened=True,display='live')).json()
    with api.stream('GET',BASE+'/api/stream?view=display') as response:
        lines=response.iter_lines()
        kind,data=next_event(lines)
        assert kind=='message' and data['event']['rounds'][0]['total']==0
        kind,data=next_event(lines)
        assert kind=='heartbeat',kind
        start=time.monotonic()
        assert submit(s,[0]).is_success
        kind,data=next_event(lines)
        assert kind=='message' and data['event']['rounds'][0]['total']==1
        print('SSE single vote latency:',round(time.monotonic()-start,3),'s')
        assert time.monotonic()-start<3
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'])
        display=browser.new_page(viewport={'width':1366,'height':768})
        vote=browser.new_page(viewport={'width':1024,'height':600},has_touch=True)
        errors=[]
        for page in (display,vote):page.on('pageerror',lambda e:errors.append(str(e)))
        display.goto(BASE+'/display')
        expect(display.locator('.total-number')).to_have_text('1')
        vote.goto(BASE+'/vote')
        vote.once('dialog',lambda d:d.accept('test-key'))
        vote.get_by_role('button',name='Подключить терминал').click()
        expect(vote.locator('.choice')).to_have_count(2)
        for w,h in ((1024,600),(1366,768),(390,844)):
            vote.set_viewport_size({'width':w,'height':h});vote.wait_for_timeout(250)
            values=vote.evaluate("""()=>[...document.querySelectorAll('.choice')].map(b=>{const a=b.getBoundingClientRect(),l=b.querySelector('.choice-label'),r=l.getBoundingClientRect(),c=getComputedStyle(b);return {font:parseFloat(getComputedStyle(l).fontSize),center:Math.abs((a.left+a.right-r.left-r.right)/2),inside:r.top>=a.top-1&&r.bottom<=a.bottom+1&&r.left>=a.left-1&&r.right<=a.right+1,color:c.backgroundColor}})""")
            assert all(v['font']>=30 and v['center']<3 and v['inside'] for v in values),values
            assert values[0]['color']!=values[1]['color'],values
        # Concurrent unique ballots are counted exactly once, including duplicate submissions.
        with ThreadPoolExecutor(max_workers=12) as pool:
            replies=list(pool.map(lambda i:submit(s,[i%2]),range(50)))
        assert all(r.is_success for r in replies)
        nonce=uuid.uuid4().hex;tid=uuid.uuid4().hex
        assert submit(s,[0],nonce,tid).is_success
        assert submit(s,[0],nonce,tid).json()['duplicate']
        expect(display.locator('.total-number')).to_have_text('52',timeout=10000)
        # Drop only SSE: HTTP fallback must keep counts current.
        fallback=browser.new_page()
        fallback.route('**/api/stream?*',lambda route:route.abort())
        fallback.goto(BASE+'/display')
        expect(fallback.locator('#connection')).to_have_attribute('data-transport','poll',timeout=12000)
        expect(fallback.locator('.total-number')).to_have_text('52')
        assert submit(s,[1]).is_success
        expect(fallback.locator('.total-number')).to_have_text('53',timeout=10000)
        # Simulate a total connection loss without closing the browser.
        fallback.route('**/api/state?*',lambda route:route.abort())
        expect(fallback.locator('body')).to_have_attribute('data-connection','offline',timeout=12000)
        assert submit(s,[0]).is_success
        expect(fallback.locator('.total-number')).to_have_text('53')
        fallback.unroute('**/api/state?*');fallback.unroute('**/api/stream?*')
        expect(fallback.locator('.total-number')).to_have_text('54',timeout=15000)
        expect(fallback.locator('#connection')).to_have_attribute('data-transport','sse',timeout=30000)
        assert submit(s,[1]).is_success
        expect(fallback.locator('.total-number')).to_have_text('55',timeout=10000)
        # Closing/hiding and changing the stage must update all clients.
        assert api.post(BASE+'/api/admin/control',headers=AUTH,json=dict(event=e['id'],round=e['round'],opened=False,display='hidden')).is_success
        expect(display.get_by_role('heading',name='Результаты пока скрыты')).to_be_visible()
        expect(vote.get_by_role('heading',name='Голосование закрыто')).to_be_visible()
        assert not errors,errors
        browser.close()
        print('Realtime, 50 concurrent ballots, recovery and touch layout: OK')
