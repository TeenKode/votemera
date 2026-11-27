"""Viewport regression: public screens must fit without scroll or clipped controls."""
import os,time,uuid
from playwright.sync_api import sync_playwright,expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
AUTH={'Authorization':'Bearer test-secret'}
KEY={'X-Terminal-Key':'test-key'}
SIZES=[(1024,600),(1366,768),(1920,1080),(800,1280),(390,844)]
def fits(page,selector):
    # Wait for the current render, then for the fit and entrance animations.
    page.locator(selector).first.wait_for()
    page.wait_for_function("document.querySelector('#main').dataset.fit")
    page.wait_for_timeout(650)
    bounds=page.evaluate("""selector=>{
      const stage=document.querySelector('.screen-stage').getBoundingClientRect(), main=document.querySelector('#main').getBoundingClientRect();
      const rects=[...document.querySelectorAll(selector)].filter(n=>n.getClientRects().length).map(n=>{const r=n.getBoundingClientRect();return [r.left,r.top,r.right,r.bottom]});
      return {ok:main.left>=stage.left-2&&main.right<=stage.right+2&&main.top>=stage.top-2&&main.bottom<=stage.bottom+2&&rects.every(r=>r[0]>=stage.left-2&&r[1]>=stage.top-2&&r[2]<=stage.right+2&&r[3]<=stage.bottom+2),scroll:document.documentElement.scrollHeight>innerHeight+2||document.documentElement.scrollWidth>innerWidth+2,scale:getComputedStyle(document.querySelector('#main')).getPropertyValue('--screen-scale'),rects,stage:[stage.left,stage.top,stage.right,stage.bottom]};
    }""",selector)
    assert bounds['ok'] and not bounds['scroll'],bounds

with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-sandbox'])
    control=browser.new_page()
    vote=browser.new_page(has_touch=True)
    display=browser.new_page()
    errors=[]
    for page in (vote,display):page.on('pageerror',lambda e:errors.append(str(e)))
    opts=[{'label':f'Вариант {i+1}: подробный ответ с дополнительным пояснением','color':c} for i,c in enumerate(['#16845b','#df5363','#4788cc','#b09a35','#8255bb','#198a9b'])]
    qs=[{'question':f'Вопрос {i+1}. Как вы оцениваете использование цифровых инструментов в профессиональной деятельности?','options':opts,'confirm':True,'description':'Выберите наиболее подходящий вариант ответа.'} for i in range(4)]
    api=control.request
    cfg=api.get(BASE+'/api/admin/state',headers=AUTH).json()['settings']
    cfg.update(vote_layout='list',display_page_size=4,display_rotate=0)
    assert api.put(BASE+'/api/admin/settings',headers=AUTH,data=cfg).ok
    event=api.post(BASE+'/api/admin/event',headers=AUTH,data={'title':'Проверка вёрстки','rounds':['До','После','Дополнительный этап'],'questions':qs}).json()
    payload=dict(event=event['id'],round=event['round'],opened=True,display='live')
    state=api.post(BASE+'/api/admin/control',headers=AUTH,data=payload).json()
    for i in range(6):
        assert api.post(BASE+'/api/ballot',headers=KEY,data={'round':state['round'],'epoch':state['epoch'],'choices':[i,i,i,i],'nonce':uuid.uuid4().hex,'terminal':uuid.uuid4().hex}).ok
    vote.goto(BASE+'/vote');vote.once('dialog',lambda d:d.accept('test-key'));vote.get_by_role('button',name='Подключить терминал').click()
    display.goto(BASE+'/display')
    for w,h in SIZES:
        for page in (vote,display):page.set_viewport_size({'width':w,'height':h})
        fits(vote,'.choice,.vote-nav button,.vote-question')
        assert vote.locator('.choice').count()==6
        fits(display,'.question-group,.display-nav button,.total-tile')
        assert display.locator('.question-group').count()>=1
    assert api.post(BASE+'/api/admin/control',headers=AUTH,data={**payload,'display':'compare'}).ok
    expect(display.locator('.compare-mode')).to_be_visible()
    for w,h in SIZES:
        display.set_viewport_size({'width':w,'height':h})
        fits(display,'.question-group,.delta-panel,.display-nav button')
    assert api.put(BASE+'/api/admin/analysis/'+str(event['id']),headers=AUTH,data={'first':0,'second':1,'chart':'heatmap'}).ok
    assert api.post(BASE+'/api/admin/control',headers=AUTH,data={**payload,'display':'analysis_compare'}).ok
    expect(display.locator('.heat-cell')).to_have_count(36)
    for w,h in SIZES:
        display.set_viewport_size({'width':w,'height':h})
        fits(display,'.cross-panel,.heat-cell,.screen-pager button')
    assert api.put(BASE+'/api/admin/analysis/'+str(event['id']),headers=AUTH,data={'first':0,'second':1,'chart':'stack'}).ok
    expect(display.locator('.cross-group')).to_have_count(6)
    for w,h in SIZES:
        display.set_viewport_size({'width':w,'height':h})
        fits(display,'.cross-panel,.cross-group,.screen-pager button')
    assert not errors,errors
    browser.close()
    print('Public viewport fit: OK')
