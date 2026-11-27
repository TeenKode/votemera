"""Static demo: same UI, local-only state, shared tabs and no backend requests."""
import os
from playwright.sync_api import sync_playwright, expect
BASE=os.getenv('DEMO_URL','http://127.0.0.1:8766/')
with sync_playwright() as p:
    browser=p.chromium.launch(args=['--no-sandbox'])
    context=browser.new_context()
    failures=[]
    context.on('request',lambda r: failures.append(r.url) if '/api/' in r.url or not r.url.startswith(BASE) else None)
    admin=context.new_page();vote=context.new_page();display=context.new_page()
    errors=[]
    for page in (admin,vote,display): page.on('pageerror',lambda e:errors.append(str(e)))
    admin.goto(BASE+'?view=admin')
    expect(admin.get_by_role('heading',name='Обзор',exact=True)).to_be_visible()
    vote.goto(BASE+'?view=vote');display.goto(BASE+'?view=display')
    expect(display.locator('.total-number')).to_have_text('24')
    vote.get_by_role('button',name='Да',exact=True).first.click()
    expect(vote.get_by_text('Вопрос 2 из 3')).to_be_visible()
    vote.wait_for_timeout(350)
    vote.get_by_role('button',name='Да',exact=True).first.click()
    expect(vote.get_by_text('Вопрос 3 из 3')).to_be_visible()
    vote.wait_for_timeout(350)
    vote.get_by_role('button',name='Да',exact=True).first.click()
    expect(display.locator('.total-number')).to_have_text('25')
    admin.get_by_role('button',name='Мероприятия',exact=True).click()
    admin.get_by_role('button',name='+ Создать мероприятие').click()
    admin.get_by_label('Название мероприятия').fill('Мой тест')
    admin.get_by_label('Текст вопроса').fill('Готовы?')
    admin.get_by_role('button',name='Создать',exact=True).click()
    expect(admin.locator('.management-row').filter(has_text='Мой тест')).to_be_visible()
    admin.reload()
    expect(admin.locator('.management-row').filter(has_text='Мой тест')).to_be_visible()
    admin.get_by_role('button',name='Безопасность',exact=True).click()
    expect(admin.get_by_text('В демонстрации вход свободный;',exact=False)).to_be_visible()
    assert not failures,failures
    assert not errors,errors
    browser.close()
    print('Static demo: OK')
