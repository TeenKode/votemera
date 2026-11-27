"""Extra Chromium coverage for shared terminals, conditional branching and display."""
import os
from playwright.sync_api import sync_playwright, expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM') or None,args=['--no-sandbox'])
    control=browser.new_page(viewport={'width':1366,'height':768})
    vote=browser.new_page(viewport={'width':1366,'height':768},has_touch=True)
    display=browser.new_page(viewport={'width':1920,'height':1080})
    errors=[]
    for page in (control,vote,display): page.on('pageerror',lambda e:errors.append(str(e)))
    assert control.request.post(BASE+'/api/admin/login',data={'password':'test-secret'}).ok
    options=[{'label':'Да','color':'#16845b'},{'label':'Нет','color':'#df5363'}]
    e=control.request.post(BASE+'/api/admin/event',data={'title':'Условная анкета','rounds':['До','После'],'questions':[{'question':'Используете ИИ?','options':options},{'question':'Удобно ли пользоваться?','options':options,'show_if':{'question':0,'choice':0}},{'question':'Готовы продолжать?','options':options}]}).json()
    payload=dict(event=e['id'],round=e['round'],opened=True,display='live')
    assert control.request.post(BASE+'/api/admin/control',data=payload).ok
    vote.goto(BASE+'/vote')
    vote.once('dialog',lambda d:d.accept('test-key'))
    vote.get_by_role('button',name='Подключить терминал').click()
    display.goto(BASE+'/display')
    vote.get_by_role('button',name='Нет',exact=True).tap()
    expect(vote.get_by_text('Вопрос 3 из 3')).to_be_visible()
    vote.reload()
    expect(vote.get_by_text('Вопрос 3 из 3')).to_be_visible()
    vote.route('**/api/ballot',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"temporary"}'))
    vote.get_by_role('button',name='Да',exact=True).tap()
    expect(vote.get_by_text('Ответ не подтверждён')).to_be_visible()
    vote.unroute('**/api/ballot')
    vote.reload()
    expect(vote.get_by_text('Ответ не подтверждён')).to_be_visible()
    vote.get_by_role('button',name='Повторить отправку').click()
    expect(display.locator('.total-number')).to_have_text('1')
    state=control.request.get(BASE+'/api/admin/state').json()
    assert state['event']['rounds'][0]['results'][1]['total']==0
    assert control.request.put(BASE+'/api/admin/presentation/'+str(e['id']),data={'order':[2,0],'slides':[{'mode':'live','question':2}],'seconds':5}).ok
    assert control.request.post(BASE+'/api/admin/control',data={**payload,'display':'presentation'}).ok
    expect(display.get_by_role('heading',name='Готовы продолжать?')).to_be_visible()
    expect(display.get_by_role('heading',name='Используете ИИ?')).to_have_count(0)
    demo=browser.new_page(viewport={'width':1366,'height':768})
    demo.goto(BASE+'/display?demo=1')
    expect(demo.get_by_text('Демонстрация · данные вымышлены')).to_be_visible()
    assert control.request.get(BASE+'/api/admin/state').json()['event']['rounds'][0]['total']==1
    assert not errors,errors
    browser.close()
    print('Advanced Chromium scenarios: OK')
