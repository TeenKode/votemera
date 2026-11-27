"""Operational browser workflow using a disposable server."""
import os
from playwright.sync_api import sync_playwright,expect
BASE=os.getenv('BASE_URL','http://127.0.0.1:8765')
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM') or None,args=['--no-sandbox'])
    admin=browser.new_page(viewport={'width':1366,'height':768})
    presenter=browser.new_page(viewport={'width':390,'height':844})
    errors=[]
    for page in (admin,presenter):page.on('pageerror',lambda e:errors.append(str(e)))
    admin.goto(BASE+'/admin');admin.get_by_label('Пароль администратора').fill('new-password-123');admin.get_by_role('button',name='Войти').click()
    admin.get_by_role('button',name='Мероприятия',exact=True).click()
    admin.get_by_role('button',name='Мастер создания',exact=True).click()
    admin.get_by_label('Название мероприятия').fill('Мастер тест')
    admin.get_by_role('button',name='Далее →').click()
    admin.get_by_label('Текст вопроса').fill('Новый вопрос')
    admin.get_by_role('button',name='Далее →').click()
    expect(admin.get_by_text('Шаг 3 из 3',exact=False)).to_be_visible()
    admin.get_by_role('button',name='Создать',exact=True).click()
    row=admin.locator('.management-row').filter(has_text='Мастер тест')
    expect(row).to_be_visible()
    row.get_by_role('button',name='Выбрать',exact=True).click()
    expect(admin.get_by_label('Мероприятие',exact=True).locator('option:checked')).to_have_text('Мастер тест')
    presenter.goto(BASE+'/presenter');presenter.get_by_label('Пароль администратора').fill('new-password-123');presenter.get_by_role('button',name='Войти').click()
    expect(presenter.get_by_role('heading',name='Пульт ведущего')).to_be_visible()
    presenter.get_by_label('Таймер, минут').fill('1')
    presenter.get_by_role('button',name='Открыть этап').click()
    expect(presenter.get_by_text('Голосование открыто',exact=True)).to_be_visible()
    expect(admin.get_by_text('Сейчас голосование открыто',exact=True)).to_be_visible()
    presenter.get_by_role('button',name='Скрыть результаты').click()
    assert presenter.request.get(BASE+'/api/admin/state').json()['display']=='hidden'
    presenter.get_by_role('button',name='Закрыть этап').click()
    expect(presenter.get_by_text('Голосование закрыто',exact=True)).to_be_visible()
    expect(admin.get_by_text('Сейчас голосование закрыто',exact=True)).to_be_visible()
    assert not errors,errors
    browser.close()
    print('Operational UI: OK')
