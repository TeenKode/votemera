"""Browser + actual Votemera API integration against a mocked Yandex API."""
import os,sys,tempfile,subprocess,time
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8767'
SID='a'*24
AUTH={'Authorization':'Bearer test-secret'}
STUB=r"""
import yandex_forms as yf
import app,uvicorn
SID='a'*24
def request(org,token,path,params=None):
    if org!='bpf2c65rqcl8abcdefgh' or token!='mock-token-123':raise yf.FormsError('Нет доступа')
    if path=='/v1/users/me/':return {'id':1}
    if path=='/v1/surveys':return {'result':[{'id':SID,'name':'Тестовая форма','is_published':True}]}
    if path.endswith('/questions'):return {'pages':[{'items':[{'id':10,'label':'Используете ИИ?','type':'enum','widget':'checkbox','items':[{'label':'Да'},{'label':'Нет'}]},{'id':11,'label':'ФИО','type':'string'}]}]}
    if '/answers' in path:return {'columns':[{'id':10}],'answers':[{'id':1,'data':[{'value':['Да','Нет']}]},{'id':2,'data':[{'value':['Да']}]}]}
    raise AssertionError(path)
yf.request=request
uvicorn.run(app.app,host='127.0.0.1',port=8767)
"""
with tempfile.TemporaryDirectory() as d:
    env={**os.environ,'VOTEMERA_DB':str(Path(d)/'data.db'),'VOTEMERA_ADMIN_PASSWORD':'test-secret','VOTEMERA_TERMINAL_KEY':'test-key'}
    server=subprocess.Popen([sys.executable,'-c',STUB],cwd=ROOT,env=env)
    try:
        for i in range(50):
            try:
                if httpx.get(BASE+'/health',timeout=.5).is_success:break
            except httpx.HTTPError:pass
            time.sleep(.2)
        else:raise AssertionError('Mock server did not start')
        with sync_playwright() as p:
            b=p.chromium.launch(args=['--no-sandbox'])
            a=b.new_page(viewport={'width':1366,'height':768})
            errors=[];a.on('pageerror',lambda e:errors.append(str(e)))
            a.goto(BASE+'/admin')
            a.get_by_label('Пароль администратора').fill('test-secret')
            a.get_by_role('button',name='Войти').click()
            a.get_by_role('button',name='Интеграции',exact=True).click()
            a.get_by_label('ID организации').fill('bpf2c65rqcl8abcdefgh')
            expect(a.get_by_text('Yandex Identity Hub · X-Cloud-Org-Id',exact=True)).to_be_visible()
            a.get_by_label('OAuth-токен').fill('mock-token-123')
            a.get_by_role('button',name='Проверить и подключить').click()
            expect(a.get_by_text('Подключение сохранено · организация bpf2c65rqcl8abcdefgh')).to_be_visible()
            expect(a.get_by_label('OAuth-токен')).to_have_value('')
            a.get_by_role('button',name='Загрузить формы').click()
            expect(a.get_by_label('Название импортируемого опроса')).to_have_value('Тестовая форма')
            expect(a.get_by_role('checkbox',name='ФИО')).to_be_disabled()
            a.get_by_role('checkbox',name='Используете ИИ?').check()
            a.get_by_role('button',name='Создать из формы').click()
            expect(a.locator('[data-section=integrations] .management-row').filter(has_text='Тестовая форма')).to_be_visible()
            a.get_by_role('button',name='Мероприятия',exact=True).click()
            a.locator('.management-row').filter(has_text='Тестовая форма').get_by_role('button',name='Выбрать',exact=True).click()
            expect(a.get_by_role('heading',name='Яндекс Формы · управление показом')).to_be_visible()
            a.locator('[data-section=overview] [data-display-mode=live]').click()
            display=b.new_page();display.on('pageerror',lambda e:errors.append(str(e)));display.goto(BASE+'/display')
            expect(display.locator('.total-number')).to_have_text('2',timeout=15000)
            expect(display.get_by_text('Множественный выбор · доля ответивших')).to_be_visible()
            expect(a.locator('.overview-kpi').filter(has_text='Получено анкет').locator('strong')).to_have_text('2')
            a.get_by_role('button',name='Экран результатов',exact=True).click()
            expect(a.get_by_role('heading',name='Какие вопросы показывать')).to_be_visible()
            a.get_by_label('Вид диаграммы').select_option('donut')
            expect(a.locator('.display-preview')).to_contain_text('Для множественного выбора')
            a.get_by_role('button',name='Сохранить порядок и сценарий').click()
            expect(a.get_by_role('heading',name='Какие вопросы показывать')).to_be_visible()
            a.get_by_role('button',name='Обзор',exact=True).click()
            vote=b.new_page();vote.goto(BASE+'/vote')
            expect(vote.get_by_role('link',name='Открыть Яндекс Форму')).to_have_attribute('href','https://forms.yandex.ru/cloud/u/'+SID+'/')
            a.locator('[data-section=overview] [data-display-mode=hidden]').click()
            expect(display.get_by_role('heading',name='Результаты пока скрыты')).to_be_visible()
            assert not errors,errors
            b.close()
        print('Yandex Forms browser + mocked upstream integration: OK')
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
