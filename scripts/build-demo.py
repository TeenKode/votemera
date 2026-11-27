#!/usr/bin/env python3
"""Build the browser-only GitHub Pages demo from the production UI."""
from pathlib import Path
import shutil,sys
root=Path(__file__).resolve().parents[1]
out=Path(sys.argv[1]) if len(sys.argv)>1 else root/'_site'
out.mkdir(parents=True,exist_ok=True)
static=out/'static';static.mkdir(exist_ok=True)
version=(root/'VERSION').read_text().strip()
html=(root/'static/index.html').read_text().replace('href="/static/','href="./static/').replace('src="/static/','src="./static/')
html=html.replace('<script defer src="./static/app.js">','<script defer src="./static/demo.js"></script><script defer src="./static/app.js">')
html=html.replace('<title>Votemera</title>','<title>Votemera — демонстрация</title>').replace('<div class="tools">','<div class="tools"><a class="demo-home" href="./">ДЕМО / Главная</a>')
html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'self\'; script-src \'self\'; style-src \'self\' \'unsafe-inline\'; connect-src \'none\'; img-src \'self\' data:; object-src \'none\'; base-uri \'none\'">')
(out/'index.html').write_text(html)
(out/'.nojekyll').touch()
js=(root/'static/app.js').read_text()
def change(old,new):
    global js
    if old not in js: raise RuntimeError('Demo adapter anchor missing: '+old[:100])
    js=js.replace(old,new)
change("location.pathname.slice(1)||'home'","new URLSearchParams(location.search).get('view')||'home'")
change("await fetch(path,{method,headers","await Demo.fetch(path,{method,headers")
change("new EventSource(","new Demo.Stream(")
change("a.href=url;a.target='_blank'","a.href=Demo.url(url.slice(1));a.target='_blank'")
change("window.open('/display?demo=1'","window.open(Demo.url('display','&demo=1')")
change("location.href='/'+p","location.href=Demo.url(p)")
change("else if(mode==='admin'){token?loadAdmin():login()}","else if(mode==='admin'){loadAdmin()}")
change("keyManager(at('terminals'));adminSettings(at('settings'),s);accessPanel(at('security'));maintenancePanel(at('maintenance'));","demoUnavailable(at('terminals'),'Менеджер ключей','В рабочей системе здесь создаются, заменяются и отзываются ключи. В публичной демонстрации не вводите настоящие секреты.');adminSettings(at('settings'),s);demoUnavailable(at('security'),'Пароль администратора','В демонстрации вход свободный; смена настоящего пароля доступна только на сервере.');demoUnavailable(at('maintenance'),'Обслуживание','Установка обновлений и резервное копирование SQLite доступны в серверной версии.');")
change("function renderAdmin(){","function demoUnavailable(parent,title,msg){parent.append(add(el('section','panel'),el('h2','',title),el('p','notice',msg)))}\nfunction renderAdmin(){")
change("el('p','center muted','Локальная система интерактивного голосования')","el('p','center muted','Публичная демонстрация. Все данные вымышлены и хранятся только в этом браузере. Откройте терминал, экран и админку в разных вкладках. Не вводите настоящие персональные данные или пароли.')")
change("main.append(row);api('/api/state')","main.append(row,add(el('div','actions'),btn('Сбросить демо',()=>{if(confirm('Удалить локальные тестовые данные и вернуть пример?'))Demo.reset()}),btn('Инструкция по установке',()=>window.open('https://github.com/TeenKode/votemera#readme','_blank','noopener'))));api('/api/state')")
(static/'app.js').write_text(js)
(static/'demo.js').write_text((root/'demo/backend.js').read_text().replace('__DEMO_VERSION__',version))
(static/'style.css').write_text((root/'static/style.css').read_text()+'\n.demo-home{font-size:12px;font-weight:700;color:var(--accent);white-space:nowrap;text-decoration:none}\n')
