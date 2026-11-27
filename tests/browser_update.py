"""Maintenance log UI: live additions, reconnect and persistent last output."""
import os
from playwright.sync_api import sync_playwright,expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
with sync_playwright() as p:
    b=p.chromium.launch(args=['--no-sandbox'])
    page=b.new_page(viewport={'width':1024,'height':700})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    data={'version':'0.13.1','status':'running','available':None,'latest':None,'log':'[12:00:00] Проверка релиза\n','log_truncated':False}
    disconnected=False
    def upstream(route):
        if disconnected:route.abort()
        else:route.fulfill(json=data)
    page.route('**/api/admin/update?log=true*',upstream)
    page.goto(BASE+'/admin')
    page.get_by_label('Пароль администратора').fill('test-secret')
    page.get_by_role('button',name='Войти').click()
    page.get_by_role('button',name='Обслуживание',exact=True).click()
    log=page.get_by_label('Журнал обновления',exact=True)
    expect(log).to_contain_text('Проверка релиза')
    data['log']+='Установка зависимостей\n'+'строка\n'*100
    expect(log).to_contain_text('Установка зависимостей',timeout=6000)
    assert log.evaluate('e=>e.scrollHeight-e.scrollTop-e.clientHeight')<3
    page.get_by_label('Автопрокрутка').uncheck()
    log.evaluate('e=>e.scrollTop=0')
    data['log']+='Запуск сервера\n'
    expect(log).to_contain_text('Запуск сервера')
    assert log.evaluate('e=>e.scrollTop')==0
    disconnected=True
    expect(page.get_by_text('Сервер перезапускается.',exact=False)).to_be_visible(timeout=6000)
    expect(log).to_contain_text('Запуск сервера')
    disconnected=False;data['status']='success';data['log']+='Обновление успешно завершено\n'
    expect(log).to_contain_text('Обновление успешно завершено',timeout=6000)
    page.reload()
    expect(log).to_contain_text('Обновление успешно завершено',timeout=6000)
    assert not errors,errors
    b.close()
    print('Live update log, autoscroll and reconnect: OK')
