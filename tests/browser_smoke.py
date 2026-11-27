"""Real Chromium end-to-end test; run against a disposable server."""
import os
from playwright.sync_api import sync_playwright, expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM') or None,args=['--no-sandbox'])
    admin=browser.new_page(viewport={'width':1366,'height':900})
    vote=browser.new_page(viewport={'width':1280,'height':800},has_touch=True)
    display=browser.new_page(viewport={'width':1920,'height':1080})
    errors=[]
    for page in [admin,vote,display]:page.on('pageerror',lambda e:errors.append(str(e)))
    auth={'Authorization':'Bearer test-secret'}
    e=admin.request.post(BASE+'/api/admin/event',headers=auth,data={'title':'Анкета тест','rounds':['До','После'],'questions':[{'question':'Используете ИИ?','options':[{'label':'Да','color':'#16845b'},{'label':'Нет','color':'#df5363'}]},{'question':'Время подготовки?','confirm':True,'options':[{'label':'Мало','color':'#16845b'},{'label':'Много','color':'#df5363'}]}]}).json()
    assert admin.request.post(BASE+'/api/admin/control',headers=auth,data={'event':e['id'],'round':e['round'],'opened':True,'display':'live'}).ok
    admin.goto(BASE+'/admin'); admin.get_by_label('Пароль администратора').fill('test-secret'); admin.get_by_role('button',name='Войти').click()
    expect(admin.get_by_text('Управление мероприятием')).to_be_visible()
    vote.goto(BASE+'/vote'); display.goto(BASE+'/display')
    vote.once('dialog',lambda d:d.accept('test-key'));vote.get_by_role('button',name='Подключить терминал').click()
    vote.get_by_role('button',name='Да',exact=True).tap();expect(vote.get_by_text('Вопрос 2 из 2')).to_be_visible()
    vote.reload();expect(vote.get_by_text('Вопрос 2 из 2')).to_be_visible()
    vote.get_by_role('button',name='Мало',exact=True).tap();vote.get_by_role('button',name='Отправить ответы').tap()
    expect(vote.get_by_text('Спасибо за ваш голос!')).to_be_visible()
    expect(display.locator('.total-number')).to_have_text('1')
    expect(vote.get_by_role('button',name='Нет',exact=True)).to_be_enabled(timeout=6000)
    vote.get_by_role('button',name='Нет',exact=True).tap();vote.get_by_role('button',name='Много',exact=True).tap();vote.get_by_role('button',name='Отправить ответы').tap()
    expect(display.locator('.total-number')).to_have_text('2')
    admin.get_by_label('Экран результатов').select_option('analysis');admin.get_by_role('button',name='Только режим экрана').click()
    expect(display.get_by_role('heading',name='Связь ответов')).to_be_visible()
    expect(display.get_by_text('100,0 п. п.')).to_be_visible()
    admin.get_by_role('button',name='Аналитика',exact=True).click()
    admin.get_by_label('Визуализация').select_option('heatmap');admin.get_by_role('button',name='Сохранить пару вопросов').click()
    expect(display.locator('.heat-cell')).to_have_count(4)
    assert not errors,errors
    if os.getenv('SCREENSHOT_DIR'):
        for name,page in [('admin',admin),('display',display),('vote',vote)]:page.screenshot(path=os.path.join(os.environ['SCREENSHOT_DIR'],name+'.png'))
    browser.close()
    print('Chromium smoke test: OK')
