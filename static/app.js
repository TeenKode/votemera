'use strict';
document.body.dataset.mode=location.pathname.slice(1)||'home';
const $=id=>document.getElementById(id), main=$('main'), mode=location.pathname.slice(1)||'home';
// Only public screens are fitted. Admin keeps normal scrolling.
const kiosk=mode==='vote'||mode==='display';
const sandbox=typeof Demo!=='undefined';
const pageUrl=p=>sandbox?Demo.url(p):'/'+p;
let deviceLocked=false,deviceRevision=null;
let timerEnd=null;
function countdownText(end){const n=Math.max(0,Math.ceil(end-Date.now()/1000));return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')}
function clockTick(){const node=$('countdown');if(node){node.hidden=!timerEnd;node.textContent=timerEnd?'Осталось '+countdownText(timerEnd):''}}
let fitStage=null,fitFrame=0,fitViewport='',fitScale=1;
function scheduleFit(){if(kiosk&&!fitFrame)fitFrame=requestAnimationFrame(fitScreen)}
function choicePalette(hex){
 const channels=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));
 const rgb=channels.every(Number.isFinite)?channels:[67,200,189];
 const luma=values=>values.map(x=>{const c=x/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4}).reduce((a,b,i)=>a+b*[.2126,.7152,.0722][i],0);
 let factor=1;while(luma(rgb.map(x=>x*factor))>.13)factor*=.92;
 return {fill:'rgb('+rgb.map(x=>Math.round(x*factor)).join(',')+')',border:'rgb('+rgb.map(x=>Math.round(x*.65+255*.35)).join(',')+')'};
}
function fitChoiceLabels(){
 if(mode!=='vote')return;
 for(const b of main.querySelectorAll('.choice')){
  const label=b.querySelector('.choice-label');if(!label)continue;
  const long=Number(label.dataset.length)>35;
  const width=b.clientWidth,height=b.clientHeight;
  if(!width||!height)continue;
  const list=b.parentElement.classList.contains('choice-list'),binary=b.classList.contains('binary-answer');
  const max=Math.min(binary?110:list?52:84,Math.floor(width/(long?8:5)),Math.floor(height*(binary?.34:list?.55:.48)));
  let lo=14,hi=Math.max(lo,max);
  // Fit to the actual space, after reserving space for the icon and padding.
  const availableWidth=Math.max(1,width-2*parseFloat(getComputedStyle(b).paddingLeft)-8);
  const symbol=b.querySelector('.choice-symbol');const iconHeight=symbol&&getComputedStyle(symbol).display!=='none'?symbol.offsetHeight+parseFloat(getComputedStyle(b).rowGap||0):0;
  const availableHeight=Math.max(1,height-2*parseFloat(getComputedStyle(b).paddingTop)-iconHeight-6);
  label.style.maxWidth=availableWidth+'px';
  while(lo<hi){const n=Math.ceil((lo+hi)/2);label.style.fontSize=n+'px';if(label.scrollWidth<=availableWidth+1&&label.scrollHeight<=availableHeight+1)lo=n;else hi=n-1}
  label.style.fontSize=lo+'px';
 }
}
function fitScreen(){
 fitFrame=0;if(!fitStage||!fitStage.clientWidth||!fitStage.clientHeight)return;
 const w=fitStage.clientWidth,h=fitStage.clientHeight;
 const size=w+':'+h;
 if(fitViewport!==size){fitViewport=size;if(mode==='display'&&state)renderDisplay()}
 // Layout dimensions are untransformed, so shrinking never feeds back into measurement.
 if(mode==='vote')fitChoiceLabels();
 const width=Math.max(main.offsetWidth,main.scrollWidth),height=Math.max(main.offsetHeight,main.scrollHeight);
 const scale=Math.min(1,w/Math.max(1,width),h/Math.max(1,height));
 if(Math.abs(scale-fitScale)>.0001){fitScale=scale;main.style.setProperty('--screen-scale',String(scale))}
 main.dataset.fit=scale<.999?'scaled':'natural';
}
function initFit(){
 if(!kiosk)return;
 fitStage=document.createElement('div');fitStage.className='screen-stage';main.before(fitStage);fitStage.append(main);
 if('ResizeObserver' in window){const observer=new ResizeObserver(scheduleFit);observer.observe(fitStage);observer.observe(main)}
 new MutationObserver(scheduleFit).observe(main,{childList:true,characterData:true,subtree:true});
 window.addEventListener('resize',scheduleFit);document.addEventListener('fullscreenchange',scheduleFit);
 window.visualViewport?.addEventListener('resize',scheduleFit);
 document.fonts?.ready.then(scheduleFit);
 scheduleFit();
}
function screenPageSize(s,ordered,roundCount){
 const preferred=s.settings.display_page_size||4;
 const w=fitStage?.clientWidth||innerWidth,h=fitStage?.clientHeight||innerHeight;
 const dense=ordered.some(i=>{const q=s.event.questions[i];return q.options.length>3||q.question.length>120||q.options.some(o=>o.label.length>45)});
 if(w<680||h<480||roundCount>1)return 1;
 if(w<1050||h<740||dense)return Math.min(preferred,2);
 return preferred;
}
const el=(tag,cls,txt)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(txt!==undefined)n.textContent=txt;return n};
const add=(parent,...nodes)=>{parent.append(...nodes);return parent};
const btn=(txt,fn,cls='secondary')=>{const b=el('button',cls,txt);b.type='button';b.onclick=fn;return b};
const pct=(n,t)=>t?n*100/t:0, fmt=n=>n.toLocaleString('ru-RU',{maximumFractionDigits:1,minimumFractionDigits:1});
const hex=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),x=>x.toString(16).padStart(2,'0')).join('');
// Retargetable counters: continue from the currently displayed value, not the last target.
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
const counters=new Map();
function motionDuration(){return reducedMotion.matches||document.body.dataset.motion==='off'?0:document.body.dataset.motion==='subtle'?160:520}
function stopCounters(){for(const [node,c] of counters){cancelAnimationFrame(c.frame);node.textContent=c.format(c.target);node._shown=c.target}counters.clear()}
reducedMotion.addEventListener?.('change',stopCounters);
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCounters()});
function numeric(node,target,format=String){
 const old=counters.get(node),duration=motionDuration();
 if(old&&old.target===target&&duration)return;
 if(old)cancelAnimationFrame(old.frame);counters.delete(node);
 const start=Number.isFinite(node._shown)?node._shown:0;
 if(!node.textContent)node.textContent=format(start);
 if(!duration||start===target||!node.isConnected||document.hidden){node._shown=target;node.textContent=format(target);return}
 const started=performance.now(), c={target,format,frame:0};
 function tick(now){if(!node.isConnected){counters.delete(node);return}const t=Math.min(1,(now-started)/duration);node._shown=start+(target-start)*(1-(1-t)**3);node.textContent=format(t===1?target:node._shown);if(t<1)c.frame=requestAnimationFrame(tick);else counters.delete(node)}
 counters.set(node,c);c.frame=requestAnimationFrame(tick);
}
const countText=n=>String(Math.round(n));
const shareText=n=>fmt(n)+'%';
// Mark numeric leaves of analytics templates, then reconcile them in place.
function valueNode(tag,cls,n,kind='count'){const node=el(tag,cls,kind==='percent'?shareText(n):kind==='people'?countText(n)+' чел.':kind==='delta'?fmt(n)+' п. п.':countText(n));node.dataset.number=n;node.dataset.numberKind=kind;node._shown=n;return node}
function patchView(old,next){
 if(old.nodeType!==next.nodeType||old.nodeName!==next.nodeName){old.replaceWith(next);return}
 if(old.nodeType===3){if(old.data!==next.data)old.data=next.data;return}
 for(const a of [...old.attributes])if(!next.hasAttribute(a.name))old.removeAttribute(a.name);
 for(const a of [...next.attributes])if(old.getAttribute(a.name)!==a.value)old.setAttribute(a.name,a.value);
 if(next.hasAttribute('data-number')){const k=next.dataset.numberKind;numeric(old,+next.dataset.number,k==='percent'?shareText:k==='people'?n=>countText(n)+' чел.':k==='delta'?n=>fmt(n)+' п. п.':countText);return}
 if(counters.has(old)){cancelAnimationFrame(counters.get(old).frame);counters.delete(old)}
 delete old._shown;
 if(old.childNodes.length!==next.childNodes.length){old.replaceChildren(...next.childNodes);return}
 [...next.childNodes].forEach((child,i)=>patchView(old.childNodes[i],child));
}
let answers=[], ballotScope=null, displayFocus=-1, nextTapAt=0;
let state=null, online=false, busy=false, pending=null, toastTimer, terminal=localStorage.getItem('votemera-terminal')||hex(), token=sessionStorage.getItem('votemera-admin')||'', key=sessionStorage.getItem('votemera-key')||'';
localStorage.setItem('votemera-terminal',terminal);
function toast(s){const n=$('toast');n.textContent=s;n.style.display='block';n.classList.remove('toast-out');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{n.classList.add('toast-out');toastTimer=setTimeout(()=>{n.style.display='none'},motionDuration()?180:0)},4500)}
function conn(v,transport=''){online=v;const n=$('connection');n.textContent=v?(transport==='poll'?'В сети · резерв':'В сети'):'Связь потеряна';n.classList.toggle('online',v);n.dataset.transport=transport;document.body.dataset.connection=v?'online':'offline';}
function appearance(s){const c=s.settings;$('brand').textContent=c.brand;$('subtitle').textContent=c.subtitle;$('footer').textContent=c.brand;document.title=c.brand+' · '+({vote:'Голосование',display:'Результаты',admin:'Управление'}[mode]||'Главная');$('version').textContent='v'+s.version;document.body.dataset.voteStyle=c.vote_style||'contrast';timerEnd=s.opened?s.deadline:null;clockTick();const logo=$('event-logo');if(logo){logo.hidden=!s.logo;if(s.logo&&logo.getAttribute('src')!==s.logo)logo.src=s.logo;else if(!s.logo)logo.removeAttribute('src');}if(document.body.dataset.motion!==c.motion){document.body.dataset.motion=c.motion||'full';stopCounters()}document.body.classList.toggle('light',c.theme==='light');document.documentElement.style.setProperty('--accent',c.accent)}
$('fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen?.().catch(()=>toast('Используйте F11'))};
async function api(path,method='GET',data,auth=false){const headers={};if(data!==undefined)headers['Content-Type']='application/json';if(auth)headers.Authorization='Bearer '+token;else if(key)headers['X-Terminal-Key']=key;const r=await fetch(path,{method,headers,body:data===undefined?undefined:JSON.stringify(data),cache:'no-store',signal:AbortSignal.timeout?AbortSignal.timeout(path.startsWith('/api/admin/update')||path.startsWith('/api/admin/yandex')?30000:8000):undefined});if(!r.ok){let msg='Ошибка '+r.status;try{const j=await r.json();msg=typeof j.detail==='string'?j.detail:msg}catch{}const e=new Error(msg);e.status=r.status;throw e}return r}
function roundOf(s){return s.event?.rounds.find(r=>r.id===s.round)}
function heading(s,sub,question=s.event.question){return add(el('div','center'),el('div','eyebrow',sub||s.event.title),el('h1','',question))}
let selectedChoice=null,draftLoaded=false,idleTimer=null,lastActivity=Date.now();
const draftKey='votemera-draft';
function saveDraft(){
 lastActivity=Date.now();
 if(!answers.length&&selectedChoice===null&&!pending){sessionStorage.removeItem(draftKey);return}
 sessionStorage.setItem(draftKey,JSON.stringify({scope:ballotScope,answers,selectedChoice,pending,time:lastActivity}));
}
function clearDraft(){answers=[];selectedChoice=null;pending=null;sessionStorage.removeItem(draftKey);clearTimeout(idleTimer)}
function visibleQuestion(q,values){return !q.show_if||values[q.show_if.question]===q.show_if.choice}
function skipHidden(qs){while(answers.length<qs.length&&!visibleQuestion(qs[answers.length],answers))answers.push(null)}
function restoreDraft(scope){
 if(draftLoaded)return;draftLoaded=true;
 try{const d=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(!d)return;
 if(d.pending&&d.pending.terminal===terminal){pending=d.pending;return}
 const limit=state.settings.idle_timeout??90;
 if(d.scope===scope&&(!limit||Date.now()-d.time<limit*1000)&&Array.isArray(d.answers)&&d.answers.length<state.event.questions.length&&d.answers.every((v,i)=>visibleQuestion(state.event.questions[i],d.answers)?Number.isInteger(v)&&v>=0&&v<state.event.questions[i].options.length:v===null)){
 answers=d.answers;lastActivity=d.time;selectedChoice=Number.isInteger(d.selectedChoice)&&d.selectedChoice>=0&&d.selectedChoice<state.event.questions[answers.length].options.length?d.selectedChoice:null
 }else clearDraft();
 }catch{clearDraft()}
}
function resetIdle(){
 clearTimeout(idleTimer);const seconds=state?.settings.idle_timeout??90;
 if(mode==='vote'&&seconds&&!pending&&!busy&&(answers.length||selectedChoice!==null))idleTimer=setTimeout(()=>{clearDraft();renderVote();toast('Анкета сброшена по тайм-ауту')},Math.max(0,seconds*1000-(Date.now()-lastActivity)));
}
function renderVote(){
 if(busy)return;
 main.className='vote-main';main.replaceChildren();
 if(!state?.event){main.append(el('h1','center','Ожидание мероприятия'));return}
 const s=state,scope=s.round+':'+s.epoch;
 if(ballotScope!==scope){answers=[];selectedChoice=null;ballotScope=scope;if(draftLoaded&&!pending)clearDraft()}restoreDraft(scope);resetIdle()
 const qs=s.event.questions, q=qs[Math.min(answers.length,qs.length-1)];
 const top=el('div','vote-top');top.append(el('div','eyebrow',s.event.title+' · '+(roundOf(s)?.name||'')));
 if(qs.length>1){top.append(el('div','progress-label','Вопрос '+Math.min(answers.length+1,qs.length)+' из '+qs.length));const progress=el('div','vote-progress');progress.setAttribute('aria-hidden','true');qs.forEach((_,i)=>progress.append(el('span',i<answers.length?(answers[i]===null?'skipped':'done'):i===answers.length?'current':'')));top.append(progress)}
 main.append(top);
 if(!s.external&&s.terminal_key_required&&!key){main.append(add(el('div','center wait'),el('h1','','Подключение терминала'),btn('Подключить терминал',()=>{key=prompt('Ключ терминала')||'';sessionStorage.setItem('votemera-key',key);heartbeat();renderVote()},'')));return}
 if(s.external){const link=el('a','external-link','Открыть Яндекс Форму');link.href=s.external.url;link.target='_blank';link.rel='noopener noreferrer';main.append(add(el('div','center wait'),el('h1','','Заполните Яндекс Форму'),el('p','muted','Ответы автоматически появятся на экране результатов.'),link));return}
 if(deviceLocked){main.append(add(el('div','center wait'),el('h1','','Терминал заблокирован'),el('p','muted','Обратитесь к организатору')));return}
 if(pending){main.append(add(el('div','center wait'),el('h2','','Ответ не подтверждён'),el('p','muted','Повторная отправка не создаст второй голос.'),btn('Повторить отправку',()=>send(pending),''),btn('Отменить',()=>{if(confirm('Ответ мог быть записан. Отменить повторную отправку?')){clearDraft();renderVote()}})));return}
 if(!s.opened||!online){main.append(add(el('div','center wait'),el('h1','',online?'Голосование закрыто':'Нет связи с сервером'),el('p','muted',online?'Ожидайте начала этапа':'Ответы временно не принимаются')));return}
 main.append(el('h1','vote-question',q.question));if(q.description)main.append(el('p','question-description',q.description));
 const accept=i=>{if(busy||!online||deviceLocked||!state?.opened||state.round!==s.round||state.epoch!==s.epoch||ballotScope!==scope||answers.length>=qs.length||i===null||(!q.confirm&&Date.now()<nextTapAt))return;nextTapAt=Date.now()+300;answers.push(i);selectedChoice=null;skipHidden(qs);if(answers.length===qs.length)send({round:s.round,epoch:s.epoch,choices:[...answers],nonce:hex(),terminal});else {saveDraft();renderVote()}};
 const layout=s.settings.vote_layout==='auto'?(q.options.some(o=>o.label.length>35)?'list':'grid'):s.settings.vote_layout;
 const choices=el('div','choices choice-'+layout+' choices-'+q.options.length);choices.style.setProperty('--choice-rows',layout==='list'?q.options.length:Math.ceil(q.options.length/2));
 q.options.forEach((o,i)=>{
  const short=o.label.trim().length<=14;
  const binary=q.options.length===2&&q.options.every(x=>/^(да|нет|yes|no)$/iu.test(x.label.trim()));
  const symbol=binary?(/^(да|yes)$/iu.test(o.label.trim())?'✓':'×'):'';
  const b=btn('',()=>{if(busy||!online||deviceLocked||!state?.opened||ballotScope!==scope)return;if(q.confirm){selectedChoice=i;saveDraft();resetIdle();choices.querySelectorAll('.choice').forEach((choice,j)=>{choice.classList.toggle('selected',j===i);choice.setAttribute('aria-pressed',String(j===i));choice.querySelector('.choice-indicator').textContent=j===i?'✓':''});main.querySelector('.vote-next').disabled=false}else accept(i)},'choice'+(short?' short-answer':'')+(binary?' binary-answer':'')+(q.confirm&&selectedChoice===i?' selected':''));
  b.style.setProperty('--choice',o.color);b.style.setProperty('--order',i);b.setAttribute('aria-label',o.label);
  const palette=choicePalette(o.color);b.style.setProperty('--choice-fill',palette.fill);b.style.setProperty('--choice-border',palette.border);
  if(symbol){const mark=el('span','choice-symbol',symbol);mark.setAttribute('aria-hidden','true');b.append(mark)}
  const label=el('span','choice-label',o.label);label.dataset.length=String(o.label.trim().length);
  const indicator=el('span','choice-indicator',q.confirm&&selectedChoice===i?'✓':'');indicator.setAttribute('aria-hidden','true');
  b.append(label,indicator);if(q.confirm)b.setAttribute('aria-pressed',String(selectedChoice===i));choices.append(b)
 });
 main.append(choices);const nav=el('div','vote-nav');if(answers.length)nav.append(btn('← Назад',()=>{do{selectedChoice=answers.pop()}while(selectedChoice===null&&answers.length);saveDraft();renderVote()}),btn('Начать заново',()=>{clearDraft();renderVote()}));else nav.append(el('span','muted','Коснитесь выбранного варианта ответа'));
 if(q.confirm){const next=btn(answers.length===qs.length-1?'Отправить ответы':'Далее →',()=>accept(selectedChoice),'');next.classList.add('vote-next');next.disabled=selectedChoice===null;nav.append(next)}main.append(nav);scheduleFit();
}
async function send(v){if(busy)return;busy=true;pending=v;saveDraft();clearTimeout(idleTimer);main.querySelectorAll('button').forEach(b=>b.disabled=true);try{await api('/api/ballot','POST',v);clearDraft();main.replaceChildren(add(el('div','center wait'),el('div','success','✓'),el('h1','',state.settings.thank_you),el('p','muted','Терминал скоро будет готов к следующему участнику')));setTimeout(()=>{busy=false;renderVote()},state.settings.cooldown)}catch(e){busy=false;if(e.status===409||e.status===422){clearDraft()}else if(e.status===423){deviceLocked=true;heartbeat()}else if(e.status===403){key='';sessionStorage.removeItem('votemera-key')}if(pending)saveDraft();toast(e.message||'Не удалось подтвердить голос');renderVote()}}
let displayPage=0,displayPageScope='',rotateTimer=null,crossPage=0,crossScope='',crossTimer=null;
function crossPanel(s,r){
 const ac=s.event.analysis, a=s.event.questions[ac.first],b=s.event.questions[ac.second],d=r.cross;
 const panel=el('section','question-group cross-panel');panel.append(add(el('div','cross-top'),el('h2','',r.name),el('span','muted',`Анкет: ${d?.total??0}`)));
 if(!d?.total){panel.append(el('p','muted','Нет заполненных анкет с обоими ответами.'));return panel}
 const legend=el('div','cross-legend');b.options.forEach(o=>{const dot=el('i','swatch');dot.style.setProperty('--option-color',o.color);legend.append(add(el('span','metric-label'),dot,el('span','',o.label)))});panel.append(legend);
 if(ac.chart==='heatmap'){
  const wrap=el('div','cross-scroll'),table=el('table','cross-table'),head=el('tr');head.append(el('th','','Группа'));b.options.forEach(o=>{const h=el('th','',o.label);h.scope='col';head.append(h)});table.append(add(el('thead'),head));const body=el('tbody');
  d.matrix.forEach((row,i)=>{const tr=el('tr'),h=el('th','',a.options[i].label+' · '+d.totals[i]);h.scope='row';tr.append(h);row.forEach((n,j)=>{const cell=el('td','heat-cell');const share=pct(n,d.totals[i]);cell.style.background=`color-mix(in srgb, ${b.options[j].color} ${Math.round(share*.45)}%, var(--bg))`;cell.append(d.totals[i]?valueNode('strong','',share,'percent'):el('strong','','—'),valueNode('small','',n,'people'));tr.append(cell)});body.append(tr)});table.append(body);wrap.append(table);panel.append(wrap);
 }else{
  d.matrix.forEach((row,i)=>{const group=el('div','cross-group');group.append(add(el('div','cross-top'),el('strong','',a.options[i].label),el('span','muted',d.totals[i]+' чел.')));const bar=el('div','stack cross-stack'),values=el('div','cross-values');row.forEach((n,j)=>{const share=pct(n,d.totals[i]),seg=el('div','segment');seg.style.background=b.options[j].color;seg.style.width=share+'%';seg.title=`${b.options[j].label}: ${n} (${fmt(share)}%)`;bar.append(seg);const cell=el('div','cross-value');cell.style.setProperty('--option-color',b.options[j].color);cell.append(el('small','',b.options[j].label),d.totals[i]?valueNode('strong','',share,'percent'):el('strong','','—'),valueNode('span','muted',n,'people'));values.append(cell)});group.append(bar,values);panel.append(group)});
 }
 const valid=d.totals.map((n,i)=>n?i:-1).filter(i=>i>=0);let best=null;
 for(let j=0;j<b.options.length;j++)for(const i of valid)for(const k of valid){const diff=pct(d.matrix[i][j],d.totals[i])-pct(d.matrix[k][j],d.totals[k]);if(diff>0&&(!best||diff>best.diff))best={j,i,k,diff}}
 if(best){const box=el('div','cross-insight');box.append(el('small','muted','Наибольшая наблюдаемая разница'),valueNode('strong','',best.diff,'delta'),el('p','',`«${b.options[best.j].label}»: ${fmt(pct(d.matrix[best.i][best.j],d.totals[best.i]))}% в группе «${a.options[best.i].label}» и ${fmt(pct(d.matrix[best.k][best.j],d.totals[best.k]))}% в группе «${a.options[best.k].label}».`));panel.append(box)}
 panel.append(el('p','muted cross-note','Доли рассчитаны внутри каждой группы. Связь не доказывает причинность.'+(d.unpaired?` Не включено анкет без связанной пары: ${d.unpaired}.`:'')));return panel;
}
function renderCross(s){
 clearInterval(rotateTimer);const ac=s.event.analysis;
 const allRounds=s.display==='analysis_compare'?s.event.rounds:s.event.rounds.filter(r=>r.id===s.round);
 const crossWidth=fitStage?.clientWidth||innerWidth,crossHeight=fitStage?.clientHeight||innerHeight;
 const compactPair=ac&&s.event.questions[ac.first].options.length<=3&&s.event.questions[ac.second].options.length<=3&&[s.event.questions[ac.first],s.event.questions[ac.second]].every(q=>q.options.every(o=>o.label.length<=45));
 const crossSize=compactPair&&crossWidth>=1100&&crossHeight>=600?2:1;const pages=Math.max(1,Math.ceil(allRounds.length/crossSize));
 const scope=JSON.stringify([s.event.id,s.display,s.round,ac,allRounds.map(r=>r.id),crossSize,s.settings.display_rotate]);
 if(scope!==crossScope){crossScope=scope;crossPage=0;clearInterval(crossTimer);crossTimer=null}
 crossPage=Math.min(crossPage,pages-1);
 const rounds=allRounds.slice(crossPage*crossSize,(crossPage+1)*crossSize);
 const sig=JSON.stringify(['cross',scope,crossPage,s.event.questions,rounds.map(r=>[r.id,r.name])]);const view=el('div');
 view.append(add(el('div','display-heading'),add(el('div'),el('div','eyebrow','Перекрёстный анализ · '+s.event.title),el('h1','','Связь ответов'))));
 if(!ac){main.dataset.sig=sig;main.className='display-main cross-main';main.replaceChildren(...view.children,el('p','muted','Для анализа необходимо не менее двух вопросов.'));return}
 const a=s.event.questions[ac.first],b=s.event.questions[ac.second];view.append(add(el('div','cross-pair'),add(el('div'),el('small','muted','ГРУППИРОВКА'),el('h2','',a.question)),el('span','cross-arrow','→'),add(el('div'),el('small','muted','РАСПРЕДЕЛЕНИЕ'),el('h2','',b.question))));
 if(pages>1){const nav=el('div','display-nav screen-pager');nav.append(btn('‹',()=>{crossPage=(crossPage+pages-1)%pages;renderDisplay()}),el('span','muted',`Страница ${crossPage+1} / ${pages}`),btn('›',()=>{crossPage=(crossPage+1)%pages;renderDisplay()}));view.append(nav);if(!crossTimer&&s.settings.display_rotate>0)crossTimer=setInterval(()=>{crossPage=(crossPage+1)%pages;renderDisplay()},s.settings.display_rotate*1000)}
 const wrap=el('div','cross-rounds');rounds.forEach(r=>wrap.append(crossPanel(s,r)));view.append(wrap);
 if(main.dataset.sig===sig&&main.children.length===view.children.length){[...view.children].forEach((n,i)=>patchView(main.children[i],n))}else{main.dataset.sig=sig;main.className='display-main cross-main';main.replaceChildren(...view.children)}
}
function analysisAdmin(parent,s){if(s.external){parent.append(add(el('section','panel'),el('h2','','Аналитика Яндекс Форм'),el('p','muted','Доступны агрегированные распределения, PDF, Excel и CSV. Перекрёстный анализ пока не поддерживается.'),btn('PDF',()=>downloadAdmin('/api/admin/report/'+s.event.id+'/pdf','votemera-'+s.event.id+'.pdf')),btn('Excel',()=>downloadAdmin('/api/admin/report/'+s.event.id+'/xlsx','votemera-'+s.event.id+'.xlsx'))));return}
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Перекрёстный анализ'),el('p','muted','Выберите два вопроса активного мероприятия. Результаты показывают распределение внутри групп, без идентификации участников.'));
 const ac=s.event.analysis;if(!ac){panel.append(el('p','muted','Добавьте минимум два вопроса.'));return}
 const opts=s.event.questions.map((q,i)=>[i,(i+1)+'. '+q.question]);const a=select(panel,'Группировать по вопросу',opts,ac.first),b=select(panel,'Анализировать ответы на вопрос',opts,ac.second),chart=select(panel,'Визуализация',[['stack','Групповые шкалы'],['heatmap','Тепловая таблица']],ac.chart);
 panel.append(add(el('div','actions'),btn('Сохранить пару вопросов',()=>action('/api/admin/analysis/'+s.event.id,'PUT',{first:+a.value,second:+b.value,chart:chart.value}),'')));
 const preview=el('div','cross-rounds');s.event.rounds.forEach(r=>preview.append(crossPanel(s,r)));panel.append(preview);
}

let scenarioIndex=0,scenarioTimer=null,scenarioSignature='';
function scenarioState(s){const p=s?.event?.presentation;const active=s?.display==='presentation';const sig=JSON.stringify([active,s?.event?.id,p]);if(sig!==scenarioSignature){scenarioSignature=sig;scenarioIndex=0;clearInterval(scenarioTimer);if(active&&p?.slides.length>1)scenarioTimer=setInterval(()=>{scenarioIndex=(scenarioIndex+1)%p.slides.length;renderDisplay()},p.seconds*1000)}if(!active)return s;const slide=p?.slides[scenarioIndex]||{mode:'live',question:-1};return {...s,display:slide.mode,scenarioQuestion:slide.question,scenario:true}}
function renderDisplay(){
 let s=scenarioState(state);if(s?.external)s={...s,settings:{...s.settings,chart:'bars'}};
 if(!s?.event){main.replaceChildren(el('h1','center','Ожидание мероприятия'));return}
 if(['analysis','analysis_compare'].includes(s.display)){renderCross(s);return}
 clearInterval(crossTimer);crossTimer=null;crossScope='';
 const qs=s.event.questions, allRounds=s.display==='compare'?s.event.rounds:s.event.rounds.filter(r=>r.id===s.round);
 const ordered=s.event.presentation?.order?.length?s.event.presentation.order:qs.map((_,i)=>i);
 const roundSize=s.display==='compare'?2:1,roundPages=Math.max(1,Math.ceil(allRounds.length/roundSize));
 const pageSize=screenPageSize(s,ordered,Math.min(roundSize,allRounds.length));
 const pageScope=JSON.stringify([s.event.id,s.display,qs,ordered,s.scenarioQuestion]);
 if(displayPageScope!==pageScope){displayPageScope=pageScope;displayPage=0;displayFocus=-1}
 if(displayFocus>=qs.length||(!ordered.includes(displayFocus)&&displayFocus!==-1))displayFocus=-1;if(s.scenarioQuestion>=0)displayFocus=s.scenarioQuestion;
 const questionPages=displayFocus===-1?Math.max(1,Math.ceil(ordered.length/pageSize)):1,pages=questionPages*roundPages;
 displayPage=Math.min(displayPage,pages-1);
 const questionPage=displayPage%questionPages,roundPage=Math.floor(displayPage/questionPages);
 const rounds=allRounds.slice(roundPage*roundSize,(roundPage+1)*roundSize);
 const indices=displayFocus===-1?ordered.slice(questionPage*pageSize,(questionPage+1)*pageSize):[displayFocus];
 const sig=JSON.stringify([s.event.id,s.event.title,qs,s.display,s.settings,s.external?.stale,!!s.external?.last_sync,rounds.map(r=>[r.id,r.name]),s.opened,displayFocus,displayPage,ordered,s.scenarioQuestion,s.scenario,pageSize,pages]);
 if(main.dataset.sig===sig){if(s.display!=='hidden'){for(const i of indices){for(const r of rounds)updateChart(r,s,i);updateDelta(s,rounds,i)}updateTotals(rounds)}return}
 clearInterval(rotateTimer);main.dataset.sig=sig;main.className='display-main'+(indices.length===1?' spotlight':'')+(s.display==='compare'?' compare-mode':'');
 const top=el('div','display-heading');const labels=el('div');labels.append(el('div','eyebrow',s.external?'Яндекс Формы · онлайн':s.display==='compare'?'Сравнение этапов':s.opened?'Голосование открыто':'Голосование завершено'),el('h1','',s.event.title));top.append(labels);
 main.replaceChildren(top);
 if(s.external&&s.display!=='hidden')main.append(el('p','notice external-status',!s.external.last_sync?'Ожидание первой синхронизации с Яндекс Формами':s.external.stale?'Яндекс Формы: показаны последние сохранённые результаты':'Яндекс Формы: автоматическая синхронизация'));
 if(s.display==='hidden'){main.append(add(el('div','center wait'),el('h2','','Результаты пока скрыты'),el('p','muted','Они появятся по решению организатора')));return}
 const totals=el('div','display-totals');for(const r of rounds){const tile=el('div','total-tile');tile.append(el('span','',r.name),el('strong','total-number','0'),el('small','','Участников'));tile.dataset.round=r.id;totals.append(tile)}top.append(totals);updateTotals(rounds);
 if((qs.length>1&&!s.scenario)||pages>1){const controls=el('div','display-controls');if(qs.length>1&&!s.scenario){const picker=select(controls,'Вопрос',[[-1,'Обзор вопросов'],...ordered.map(i=>[i,(i+1)+'. '+qs[i].question])],displayFocus);picker.onchange=()=>{displayFocus=+picker.value;displayPage=0;renderDisplay()}}if(pages>1){const nav=el('div','display-nav');nav.append(btn('‹',()=>{displayPage=(displayPage+pages-1)%pages;renderDisplay()}),el('span','muted',`${displayPage+1} / ${pages}`),btn('›',()=>{displayPage=(displayPage+1)%pages;renderDisplay()}));controls.append(nav);if(s.settings.display_rotate>0)rotateTimer=setInterval(()=>{displayPage=(displayPage+1)%pages;renderDisplay()},s.settings.display_rotate*1000)}main.append(controls)}
 const grid=el('div',indices.length>1?'question-grid':'question-single');grid.dataset.size=indices.length;main.append(grid);
 for(const i of indices){const q=qs[i],group=el('section','question-group');group.style.setProperty('--order',indices.indexOf(i));group.append(el('div','question-kicker','ВОПРОС '+String(i+1).padStart(2,'0')),el('h2','',q.question));if(q.multi)group.append(el('p','muted','Множественный выбор · доля ответивших'));
 const panels=el('div','round-panels');group.append(panels);grid.append(group);
 for(const r of rounds){const panel=el('section','panel result-panel');panel.id='round-'+r.id+'-'+i;panel.append(add(el('div','round-title'),el('strong','',r.name),el('span','total')));
 const stats=el('div','stats result-stats');for(const o of q.options){const metric=el('div','metric');metric.style.setProperty('--option-color',o.color);metric.append(add(el('span','metric-label'),el('i','swatch'),el('span','',o.label)),add(el('div','metric-values'),el('b','count'),el('small','share')));if(s.settings.chart==='bars'){const bar=el('div','bar'),seg=el('div','segment');seg.style.background=o.color;seg.style.width='0%';bar.append(seg);metric.append(bar)}stats.append(metric)}if(s.settings.chart==='donut'){const donut=el('div','donut');donut.setAttribute('role','img');donut.setAttribute('aria-label','Распределение ответов');panel.append(add(el('div','donut-layout'),donut,stats))}else panel.append(stats);
 if(s.settings.chart==='stack'){const bar=el('div','stack');for(const o of q.options){const seg=el('div','segment');seg.style.background=o.color;seg.style.width='0%';bar.append(seg)}panel.append(bar)}panels.append(panel);updateChart(r,s,i)}
 if(rounds.length>1){const delta=el('section','delta-panel');delta.id='delta-'+i;group.append(delta);updateDelta(s,rounds,i)}}
}
function updateTotals(rounds){for(const t of main.querySelectorAll('.total-tile')){const r=rounds.find(x=>String(x.id)===t.dataset.round);numeric(t.querySelector('.total-number'),r?.total??0,countText)}}
function updateChart(r,s,i){const panel=$('round-'+r.id+'-'+i),res=r.results?.[i];if(!panel||!res)return;panel.querySelector('.total').textContent='Ответили: '+res.total+' из '+r.total;panel.querySelectorAll('.count').forEach((n,j)=>{n.hidden=!s.settings.show_count;numeric(n,res.counts[j],countText)});panel.querySelectorAll('.share').forEach((n,j)=>{n.hidden=!s.settings.show_percent;numeric(n,pct(res.counts[j],res.total),shareText)});panel.querySelectorAll('.segment').forEach((n,j)=>{n.style.width=pct(res.counts[j],res.total)+'%'});const donut=panel.querySelector('.donut');if(donut){let start=0;const stops=res.counts.map((n,j)=>{const end=start+pct(n,res.total);const value=s.event.questions[i].options[j].color+' '+start+'% '+end+'%';start=end;return value});donut.style.background=res.total?'conic-gradient('+stops.join(',')+')':'var(--line)';donut.setAttribute('aria-label',s.event.questions[i].options.map((o,j)=>o.label+': '+fmt(pct(res.counts[j],res.total))+'%').join(', '))}}
function updateDelta(s,rounds,i){const node=$('delta-'+i);if(!node)return;const a=rounds[0],b=rounds.at(-1),ar=a.results[i],br=b.results[i],view=el('section','delta-panel');view.id=node.id;view.append(el('h3','','Изменение: '+a.name+' → '+b.name));if(!ar.total||!br.total){view.append(el('p','muted','Для сравнения нужны голоса в обоих этапах'));patchView(node,view);return}const row=el('div','delta');s.event.questions[i].options.forEach((o,j)=>{const diff=br.counts[j]-ar.counts[j],dp=pct(br.counts[j],br.total)-pct(ar.counts[j],ar.total);row.append(add(el('div'),el('span','',o.label),el('b','',(diff>0?'+':'')+diff+' голосов'),el('small','muted',(dp>0?'+':'')+fmt(dp)+' п. п.')))});view.append(row,el('p','muted','Разница общих результатов, не число участников, изменивших мнение.'));patchView(node,view)}
let heartbeatBusy=false;
async function heartbeat(){if(mode!=='vote'||!online||heartbeatBusy||(state?.terminal_key_required&&!key))return;heartbeatBusy=true;const sentKey=key;
 try{const d=await(await api('/api/terminal/heartbeat','POST',{terminal})).json();const locked=!!d.locked;if(deviceRevision===null)deviceRevision=Number(sessionStorage.getItem('votemera-device-revision')||0);if(d.revision!==undefined&&d.revision>deviceRevision){sessionStorage.setItem('votemera-device-revision',d.revision);location.reload();return}if(locked!==deviceLocked){deviceLocked=locked;renderVote()}}catch(e){if(e.status===403&&key===sentKey){key='';sessionStorage.removeItem('votemera-key');toast('Ключ терминала отключён или неверен');renderVote()}}finally{heartbeatBusy=false}
}
if(mode==='vote')setInterval(heartbeat,3000);
function displayHeartbeat(){if(mode==='display'&&!sandbox&&online){let id=sessionStorage.getItem('votemera-display-id');if(!id){id=hex();sessionStorage.setItem('votemera-display-id',id)}api('/api/display/heartbeat','POST',{terminal:id}).catch(()=>{})}}
if(mode==='display')setInterval(displayHeartbeat,5000);
function demoDisplay(){let current=null;const es=new EventSource('/api/stream?view=vote');es.onmessage=e=>{const s=JSON.parse(e.data);if(!s.event)return;const id=s.event.id;if(current?.event.id!==id){current=s;current.event.title='ДЕМО · '+s.event.title;current.display='compare';current.opened=true;for(const r of current.event.rounds){r.results=s.event.questions.map(q=>({counts:q.options.map(()=>0),total:0}));r.total=0}}state=current;appearance(state);$('subtitle').textContent='Демонстрация · данные вымышлены';conn(true);renderDisplay()};es.onerror=()=>conn(false);setInterval(()=>{if(!current||!online)return;for(const r of current.event.rounds){r.total++;for(const q of r.results){q.counts[Math.floor(Math.random()*q.counts.length)]++;q.total++}}renderDisplay()},900)}
function startStream(){let es=null, generation=0, lastSeen=0, lastData='', fallback=false, polling=false, lastTry=0, retryAt=0;
 const now=()=>Date.now();
 const apply=(s,transport)=>{
  if(!s||typeof s!=='object'||!s.settings)return;
  const data=JSON.stringify(s),changed=data!==lastData,wasOnline=online;
  lastData=data;state=s;lastSeen=now();conn(true,transport);
  if(changed||!wasOnline){appearance(s);if(mode==='vote'){heartbeat();renderVote()}else{renderDisplay();displayHeartbeat()}}
 };
 function lost(){if(online){conn(false);if(mode==='vote')renderVote()}else conn(false)}
 function beginFallback(){if(fallback){if(es){generation++;es.close();es=null;retryAt=now()+15000}return}fallback=true;generation++;es?.close();es=null;lost();retryAt=now()+15000;poll()}
 function open(){
  const current=++generation;let received=false;
  es=new EventSource('/api/stream?view='+mode);
  lastTry=now();
  es.onmessage=e=>{if(current!==generation)return;try{const data=JSON.parse(e.data);received=true;fallback=false;apply(data,'sse')}catch{beginFallback()}};
  es.addEventListener?.('heartbeat',()=>{if(current!==generation||!received)return;lastSeen=now();conn(true,'sse')});
  // A connected TCP stream is not proof that snapshots are current.
  es.onerror=()=>{if(current===generation&&!sandbox)beginFallback();else if(current===generation)lost()};
 }
 async function poll(){
  if(!fallback||polling)return;polling=true;const current=generation;
  try{const s=await(await api('/api/state?view='+mode)).json();if(fallback&&current===generation)apply(s,'poll')}
  catch{if(fallback&&current===generation)lost()}
  finally{polling=false}
 }
 function check(){
  if(sandbox)return;
  if(!fallback&&now()-Math.max(lastSeen,lastTry)>7500)beginFallback();
  if(fallback){if(es&&now()-lastTry>7500){generation++;es.close();es=null;retryAt=now()+15000}if(!es&&now()>=retryAt&&!polling)open();poll()}
 }
 open();setInterval(check,1000);
 window.addEventListener('offline',()=>{if(!sandbox)beginFallback()});
 window.addEventListener('online',()=>{if(!sandbox&&fallback){retryAt=0;check()}});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!sandbox){if(now()-lastSeen>3500&&!fallback)beginFallback();check()}});
 window.addEventListener('pagehide',()=>es?.close());
 window.addEventListener('pageshow',e=>{if(e.persisted){fallback=false;open()}});}
function field(parent,label,value,type='text'){const id='f-'+hex();const l=el('label','',label);l.htmlFor=id;const n=el(type==='textarea'?'textarea':'input');n.id=id;if(type!=='textarea')n.type=type;n.value=value;parent.append(l,n);return n}
function select(parent,label,items,value){const l=el('label','',label),n=el('select');n.id='f-'+hex();l.htmlFor=n.id;items.forEach(([v,t])=>{const o=el('option','',t);o.value=v;n.append(o)});n.value=value;parent.append(l,n);return n}
function check(parent,label,value){const row=el('div','check'),n=el('input');n.type='checkbox';n.checked=value;n.id='f-'+hex();const l=el('label','',' '+label);l.htmlFor=n.id;row.append(n,l);parent.append(row);return n}
let adminState=null, editorId=null;
async function loadAdmin(){try{adminState=await (await api('/api/admin/state','GET',undefined,true)).json();conn(true);appearance(adminState);mode==='presenter'?renderPresenter():renderAdmin()}catch(e){conn(false);if(e.status===401||e.status===503){token='';sessionStorage.removeItem('votemera-admin');login(e.message)}else{main.replaceChildren(el('h2','',e.message),btn('Повторить',loadAdmin))}}}
function login(msg=''){main.className='admin';main.replaceChildren();const p=el('form','panel login');p.append(el('h2','','Управление голосованием'));if(msg)p.append(el('p','muted',msg));const f=field(p,'Пароль администратора','','password');f.required=true;const b=el('button','','Войти');b.type='submit';p.append(add(el('div','actions'),b));p.onsubmit=e=>{e.preventDefault();token=f.value;sessionStorage.setItem('votemera-admin',token);loadAdmin()};main.append(p)}
let adminActions=0;
async function action(path,method,data,refresh=true){adminActions++;document.body.dataset.adminBusy='true';try{const r=await api(path,method,data,true);toast('Сохранено');if(refresh)await loadAdmin();return r}catch(e){toast(e.message);if(e.status===401){token='';sessionStorage.removeItem('votemera-admin');login(e.message)}return null}finally{if(--adminActions===0)delete document.body.dataset.adminBusy}}
function downloadAdmin(path,name){return api(path,'GET',undefined,true).then(async r=>{const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}).catch(e=>toast(e.message))}
function maintenancePanel(parent){
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Обновление и резервное копирование'));const status=el('p','muted','Проверка службы…');panel.append(status);let timer=null;
 const labels={unavailable:'Служба обновления не установлена. Запустите установщик один раз.',idle:'Готово к обновлению',queued:'Обновление поставлено в очередь',running:'Устанавливается обновление…',success:'Обновление завершено',failed:'Обновление не удалось. Проверьте журнал службы.'};
 const apply=btn('Установить обновление',async()=>{if(!confirm('Сервер будет кратковременно недоступен. Продолжить?'))return;try{await api('/api/admin/update','POST',{},true);apply.disabled=true;status.textContent='Обновление запущено…';poll()}catch(e){toast(e.message)}},'');apply.disabled=true;
 const refresh=async(check=false)=>{if(!panel.isConnected){clearTimeout(timer);return}try{const d=await(await api('/api/admin/update'+(check?'?check=true':''),'GET',undefined,true)).json();status.textContent=(labels[d.status]||d.status)+' · v'+d.version+(d.error?' · '+d.error:d.available===true?' · Доступна версия '+(d.latest||''):d.available===false?' · Версия актуальна':'');apply.disabled=d.available!==true||['queued','running','unavailable'].includes(d.status);if(d.version!==adminState.version){location.reload();return}if(['queued','running'].includes(d.status))poll()}catch(e){status.textContent='Сервер временно недоступен';poll()}};
 function poll(){clearTimeout(timer);timer=setTimeout(()=>refresh(),2000)}
 panel.append(add(el('div','actions'),btn('Проверить обновления',()=>refresh(true)),apply,btn('Скачать резервную копию',()=>downloadAdmin('/api/admin/backup','votemera-backup.db')),btn('Восстановить копию',()=>{const f=el('input');f.type='file';f.accept='.db,.sqlite,.sqlite3';f.onchange=async()=>{const file=f.files[0];if(!file)return;if(file.size>64*1024*1024){toast('Файл превышает 64 МБ');return}if(!confirm('Восстановить базу? Текущие данные будут заменены. Голосование должно быть закрыто.'))return;try{const r=await fetch('/api/admin/restore',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/octet-stream'},body:file});if(!r.ok){const j=await r.json();throw Error(j.detail||'Ошибка восстановления')}toast('Копия восстановлена. Голосование закрыто.');await loadAdmin()}catch(e){toast(e.message)}};f.click()},'danger')));refresh()
}
let adminPoll=null;
function liveAdmin(panel,s,terminalParent){const totals=el('div','panel');totals.append(el('h2','','Терминалы и текущая статистика'));const rows=el('div');totals.append(rows);panel.prepend(totals);const devicePanel=el('section','panel'),deviceRows=el('div','management-list');devicePanel.append(el('h2','','Подключённые устройства'),deviceRows);terminalParent.prepend(devicePanel);clearInterval(adminPoll);let active=false;const tick=async()=>{if(active||!totals.isConnected||(!token&&!sandbox)||document.hidden)return;active=true;try{const [fresh,t]=await Promise.all([api('/api/admin/state','GET',undefined,true).then(r=>r.json()),api('/api/admin/terminals','GET',undefined,true).then(r=>r.json())]);const view=el('div');view.append(el('p','',`Терминалов онлайн: ${t.online}`),el('p','muted',fresh.event.title+' · '+(fresh.opened?'Голосование открыто':'Голосование закрыто')+(fresh.opened&&fresh.deadline?' · '+countdownText(fresh.deadline):'')));fresh.event.rounds.forEach(r=>view.append(el('p','',r.name+': '+r.total+' анкет')));if(t.terminals.length)view.append(el('p','muted',t.terminals.map(x=>x.id+' · '+(x.key_name||'')+' — '+(x.online?'в сети':'не в сети')).join(' · ')));rows.replaceChildren(...view.childNodes);deviceRows.replaceChildren(el('p','muted',`В сети: ${t.online}`));for(const x of t.terminals){const device=add(el('div','management-row'),add(el('div','management-meta'),el('strong','',x.label||x.id),el('span','muted',(x.key_name||'Без ключа')+(x.locked?' · Заблокирован':''))),el('span','',x.online?'В сети':'Не в сети'));if(!sandbox&&x.device_id)device.append(add(el('div','actions'),btn('Настроить',()=>deviceOptions(x)),btn(x.locked?'Разблокировать':'Блокировать',()=>deviceAction(x,{locked:!x.locked})),btn('Перезапустить',async()=>{if(await askDialog('Перезапуск терминала','Перезагрузить интерфейс? Незавершённая анкета сохранится.','Перезапустить'))deviceAction(x,{reload:true})})));deviceRows.append(device)}if(!t.terminals.length)deviceRows.append(el('p','muted','Нет подключённых устройств'));conn(true)}catch{conn(false)}finally{active=false}};tick();adminPoll=setInterval(tick,3000)}
function presentationAdmin(parent,s){const p=s.event.presentation||{order:[],slides:[],seconds:15},panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Конструктор экрана результатов'),el('p','muted','Порядок вопросов задаёт обзор, сценарий переключает режимы автоматически.'));const order=field(panel,'Номера вопросов в порядке показа (через запятую; пусто — все)',p.order.map(i=>i+1).join(', '));const seconds=field(panel,'Время показа слайда, секунд',p.seconds,'number');const list=el('div');panel.append(list);const modes=[['live','Текущий этап'],['compare','Сравнение этапов'],['analysis','Перекрёстный анализ'],['analysis_compare','Перекрёстный анализ этапов']];function slide(d={mode:'live',question:-1}){if(list.children.length>=24)return;const row=el('div','panel');const m=select(row,'Режим',modes,d.mode),q=select(row,'Вопрос',[[-1,'Обзор'],...s.event.questions.map((x,i)=>[i,(i+1)+'. '+x.question])],d.question);row.getData=()=>({mode:m.value,question:+q.value});row.append(add(el('div','actions'),btn('↑',()=>{if(row.previousElementSibling)list.insertBefore(row,row.previousElementSibling)}),btn('↓',()=>{if(row.nextElementSibling)list.insertBefore(row.nextElementSibling,row)}),btn('Удалить',()=>row.remove(),'danger')));list.append(row)}p.slides.forEach(slide);panel.append(add(el('div','actions'),btn('+ Слайд',()=>slide()),btn('Сохранить сценарий',()=>{const values=order.value.trim()?order.value.split(',').map(x=>Number(x.trim())-1):[];action('/api/admin/presentation/'+s.event.id,'PUT',{order:values,seconds:+seconds.value,slides:[...list.children].map(x=>x.getData())})},''),btn('Демонстрация',()=>window.open('/display?demo=1','_blank','noopener')),btn('Создать репетицию',async()=>{if(!confirm('Создать отдельную копию анкеты без голосов?'))return;const r=await action('/api/admin/rehearsal/'+s.event.id,'POST',{},false);if(r)await loadAdmin()})));}
const ADMIN_SECTIONS=[['overview','Обзор','Управление этапом и текущие результаты'],['events','Мероприятия','Список мероприятий и конструктор анкет'],['display','Экран результатов','Настройка сценариев показа'],['analysis','Аналитика','Перекрёстный анализ и экспорт'],['terminals','Терминалы','Подключения и ключи доступа'],['settings','Настройки','Оформление и параметры голосования'],['security','Безопасность','Пароль администратора'],['integrations','Интеграции','Яндекс Формы и внешние источники'],['maintenance','Обслуживание','Обновления и резервные копии']];
let adminSections=new Map(),adminNav=new Map(),activeAdminSection='overview',adminTitle,adminDescription;
function showAdminSection(id){
 if(!adminSections.has(id))id='overview';activeAdminSection=id;sessionStorage.setItem('votemera-admin-section',id);
 for(const [name,node] of adminSections)node.hidden=name!==id;
 for(const [name,node] of adminNav){node.classList.toggle('active',name===id);if(name===id)node.setAttribute('aria-current','page');else node.removeAttribute('aria-current')}
 if(id==='integrations'&&!sandbox&&!adminSections.get(id).children.length)yandexPanel(adminSections.get(id));
 const section=ADMIN_SECTIONS.find(x=>x[0]===id);adminTitle.textContent=section[1];adminDescription.textContent=section[2];
}
function accessPanel(parent){
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Смена пароля администратора'),el('p','muted','Старый пароль перестанет действовать. Новый сохраняется при обновлениях.'));
 const form=el('form','access-form');panel.append(form);
 const old=field(form,'Текущий пароль','','password'),next=field(form,'Новый пароль','','password'),repeat=field(form,'Повторите новый пароль','','password');old.autocomplete='current-password';next.autocomplete=repeat.autocomplete='new-password';old.required=next.required=repeat.required=true;next.minLength=repeat.minLength=8;
 form.append(el('p','muted','От 8 до 128 латинских букв, цифр или символов без пробелов.'));
 const save=el('button','','Изменить пароль');save.type='submit';form.append(add(el('div','actions'),save));
 form.onsubmit=async e=>{e.preventDefault();if(next.value!==repeat.value){toast('Пароли не совпадают');return}save.disabled=true;try{await api('/api/admin/password','PUT',{current:old.value,password:next.value},true);token=next.value;sessionStorage.setItem('votemera-admin',token);form.reset();toast('Пароль изменён')}catch(err){toast(err.message)}finally{save.disabled=false}};
}
function keyManager(parent){
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Ключи терминалов'),el('p','muted','Один ключ можно использовать на нескольких ноутбуках. Ключ показывается только при создании или замене. Отключение не удаляет голоса.'));
 const form=el('form','access-form');panel.append(form);
 const name=field(form,'Название ключа','','text'),value=field(form,'Свой ключ (пусто — сгенерировать)','','password');name.required=true;name.maxLength=80;value.minLength=8;value.maxLength=128;value.autocomplete='new-password';
 const create=el('button','','Добавить ключ');create.type='submit';form.append(add(el('div','actions'),create));
 const revealed=el('div','notice key-reveal');revealed.hidden=true;panel.append(revealed);
 function reveal(key){revealed.hidden=false;const out=el('input');out.value=key;out.readOnly=true;out.setAttribute('aria-label','Новый ключ терминала');revealed.replaceChildren(el('strong','','Сохраните новый ключ'),out,add(el('div','actions'),btn('Копировать ключ',async()=>{try{if(navigator.clipboard)await navigator.clipboard.writeText(key);else{out.select();document.execCommand('copy')}toast('Ключ скопирован')}catch{out.select();toast('Скопируйте выделенный ключ вручную')}}),btn('Скрыть',()=>{revealed.replaceChildren();revealed.hidden=true})))}
 const list=el('div','management-list');panel.append(list);
 async function request(path,method,data){try{return await(await api(path,method,data,true)).json()}catch(e){toast(e.message);return null}}
 async function refresh(){const d=await request('/api/admin/keys','GET');if(!d||!list.isConnected)return;list.replaceChildren(el('p','muted',d.required?'Доступ к голосованию только по действующим ключам.':'Доступ без ключа: при добавлении первого ключа защита включится.'));if(!d.keys.length)list.append(el('p','muted','Ключей пока нет. Добавьте ключ для подключения терминалов.'));
 for(const k of d.keys){const row=el('div','management-row'),meta=el('div','management-meta');meta.append(el('strong','',k.label),el('span','muted',k.enabled?'Активен':'Отключён'));const actions=el('div','actions');
 actions.append(btn('Переименовать',async()=>{const label=prompt('Название ключа',k.label);if(label?.trim()&&await request('/api/admin/keys/'+k.id,'PUT',{label,enabled:!!k.enabled}))refresh()}),btn(k.enabled?'Отключить':'Включить',async()=>{if(k.enabled&&!confirm('Отключить этот ключ на всех терминалах?'))return;if(await request('/api/admin/keys/'+k.id,'PUT',{label:k.label,enabled:!k.enabled}))refresh()}),btn('Заменить ключ',async()=>{const v=prompt('Новый ключ (пусто — сгенерировать). Старый перестанет работать.','');if(v===null)return;const r=await request('/api/admin/keys/'+k.id+'/rotate','POST',{key:v||null});if(r){reveal(r.key);refresh()}}),btn('Удалить',async()=>{if(confirm('Удалить ключ «'+k.label+'»?')&&await request('/api/admin/keys/'+k.id,'DELETE'))refresh()},'danger'));row.append(meta,actions);list.append(row)}
 }
 form.onsubmit=async e=>{e.preventDefault();create.disabled=true;try{const r=await request('/api/admin/keys','POST',{label:name.value,key:value.value||null});if(r){reveal(r.key);form.reset();refresh()}}finally{create.disabled=false}};refresh();
}
function eventManager(parent,s,editor){
 const panel=el('section','panel');parent.append(panel);
 panel.append(add(el('div','management-heading'),el('h2','','Все мероприятия'),btn('+ Создать мероприятие',()=>{renderEditor(editor);editor.hidden=false;editor.scrollIntoView({block:'start',behavior:'smooth'})},''),btn('Мастер создания',()=>{renderEditor(editor,null,true);editor.hidden=false;editor.scrollIntoView({block:'start',behavior:'smooth'})})));
 const search=field(panel,'Поиск мероприятия','');const filter=select(panel,'Категория',[['all','Текущие'],['archive','Архив'],['templates','Шаблоны'],['everything','Все']], sessionStorage.getItem('votemera-event-filter')||'all');const list=el('div','management-list');panel.append(list);
 async function edit(id){try{const e=await(await api('/api/admin/event/'+id,'GET',undefined,true)).json();renderEditor(editor,e);editor.hidden=false;editor.scrollIntoView({block:'start'})}catch(e){toast(e.message)}}
 function draw(){list.replaceChildren();for(const e of s.events.filter(x=>x.title.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())&&(filter.value==='everything'||(filter.value==='archive'?x.archived:filter.value==='templates'?x.template:!x.archived&&(!x.template||x.id===s.event.id))))){const row=el('div','management-row'),meta=el('div','management-meta'),active=e.id===s.event.id;meta.append(el('strong','',e.title),el('span','muted',(active?'Активное · ':'')+(e.source==='yandex'?'Яндекс Формы · ':'')+(e.archived?'Архив · ':'')+(e.template?'Шаблон · ':'')+e.round_count+' этапов · '+e.total+' анкет'));const actions=el('div','actions');const choose=btn('Выбрать',async()=>{try{const item=await(await api('/api/admin/event/'+e.id,'GET',undefined,true)).json();if(s.opened&&!confirm('Закрыть текущее голосование и выбрать другое мероприятие?'))return;showAdminSection('overview');await action('/api/admin/control','POST',{event:e.id,round:item.rounds[0].id,opened:false,display:'hidden'})}catch(err){toast(err.message)}});choose.disabled=active||!!e.archived;const remove=btn('Удалить',async()=>{if(!confirm('Удалить мероприятие «'+e.title+'» и все его результаты?'))return;if(e.total&&!confirm('Будут безвозвратно удалены '+e.total+' анкет. Подтвердить?'))return;await action('/api/admin/event/'+e.id+'?confirm_results='+Boolean(e.total),'DELETE')},'danger');remove.disabled=active;remove.title=active?'Выберите другое активное мероприятие перед удалением':'';actions.append(choose);if(e.source==='yandex')actions.append(btn('Интеграция',()=>showAdminSection('integrations')));else actions.append(btn('Редактировать',()=>edit(e.id)),btn('Копировать',()=>action('/api/admin/event/'+e.id+'/copy','POST',{})));actions.append(btn('CSV',()=>downloadAdmin('/api/admin/export/'+e.id,'votemera-'+e.id+'.csv')),remove);actions.append(btn('Оформление',()=>eventOptions(e.id)),btn(e.archived?'Восстановить':'В архив',async()=>{if(e.archived||await askDialog('Архивировать мероприятие','Мероприятие и все результаты останутся доступны в архиве.','В архив'))updateEventMeta(e.id,{archived:!e.archived})}),btn(e.template?'Убрать из шаблонов':'В шаблоны',()=>updateEventMeta(e.id,{template:!e.template})));
 if(!sandbox)actions.append(btn('PDF',()=>downloadAdmin('/api/admin/report/'+e.id+'/pdf','votemera-'+e.id+'.pdf')),btn('Excel',()=>downloadAdmin('/api/admin/report/'+e.id+'/xlsx','votemera-'+e.id+'.xlsx')));
 row.append(meta,actions);list.append(row)}if(!list.children.length)list.append(el('p','muted','Мероприятий не найдено'))}search.oninput=draw;filter.onchange=()=>{sessionStorage.setItem('votemera-event-filter',filter.value);draw()};draw();
}
function adminControls(parent,s,editor){if(s.external){const p=el('section','panel');parent.append(p);p.append(el('h2','','Яндекс Формы · управление показом'),el('p','muted','Приём ответов управляется в Яндекс Формах, Votemera отображает синхронизированные результаты.'),add(el('div','actions'),btn('Показать результаты',()=>action('/api/admin/live','PATCH',{display:'live'}),''),btn('Скрыть результаты',()=>action('/api/admin/live','PATCH',{display:'hidden'})),btn('Интеграции',()=>showAdminSection('integrations')),btn('Выбрать другое мероприятие',()=>showAdminSection('events'))));return}const control=el('section','panel');control.append(el('h2','','Управление мероприятием'));parent.append(control);const event=select(control,'Мероприятие',s.events.filter(e=>!e.archived&&!e.template||e.id===s.event.id).map(e=>[e.id,e.title]),s.event.id);const round=select(control,'Этап',s.event.rounds.map(r=>[r.id,r.name]),s.round);const disp=select(control,'Экран результатов',[['live','Текущий этап'],['compare','Сравнение этапов'],['analysis','Связь ответов — текущий этап'],['analysis_compare','Связь ответов — все этапы'],['presentation','Сценарий показа'],['hidden','Скрыть результаты']],s.display);const status=el('p','notice',s.opened?'Сейчас голосование открыто':'Сейчас голосование закрыто');control.append(status);const duration=field(control,'Таймер, минут (0 — без ограничения)',0,'number');duration.min=0;duration.max=1440;let selected=s.event;let requestId=0;event.onchange=async()=>{const id=++requestId;round.disabled=true;try{const next=await(await api('/api/admin/event/'+event.value,'GET',undefined,true)).json();if(id!==requestId)return;selected=next;round.replaceChildren(...selected.rounds.map(r=>{const o=el('option','',r.name);o.value=r.id;return o}));round.disabled=false}catch(e){toast(e.message)}};const set=open=>{if(round.disabled)return toast('Дождитесь загрузки этапов');const seconds=Math.round(Number(duration.value)*60);if(!Number.isFinite(seconds)||seconds<0||seconds>86400)return toast('Неверное время таймера');action('/api/admin/control','POST',{event:+event.value,round:+round.value,opened:open,display:disp.value,duration:open?seconds:0})};control.append(add(el('div','actions'),btn('Открыть этап',()=>set(true),''),btn('Закрыть этап',()=>set(false)),btn('Только режим экрана',()=>action('/api/admin/live','PATCH',{display:disp.value}))));control.append(el('p','muted','Выбор мероприятия или этапа применяется кнопкой открытия/закрытия.'));const hist=el('div');for(const r of s.event.rounds){hist.append(add(el('div','row'),el('span','',r.name+': '+r.total),btn('Очистить',()=>{if(confirm('Удалить все голоса этапа «'+r.name+'»?'))action('/api/admin/round/'+r.id+'/votes','DELETE')},'danger')))}control.append(hist,add(el('div','actions'),btn('Экспорт CSV',async()=>{try{const r=await api('/api/admin/export/'+event.value,'GET',undefined,true);const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download='votemera-'+event.value+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(e.message)}}),btn('Редактировать выбранное',async()=>{try{const e=await(await api('/api/admin/event/'+event.value,'GET',undefined,true)).json();showAdminSection('events');editor.hidden=false;renderEditor(editor,e)}catch(e){toast(e.message)}})));}
function adminSettings(parent,s){const cfg=el('section','panel');parent.append(cfg);cfg.append(el('h2','','Настройки оформления и терминалов'));const c=s.settings;const backupEnabled=check(cfg,'Ежедневные резервные копии',c.backup_enabled??true),backupRetention=field(cfg,'Хранить ежедневных копий',c.backup_retention??14,'number');backupRetention.min=1;backupRetention.max=90;const idle=field(cfg,'Сброс незавершённой анкеты, секунд (0 — выключено)',c.idle_timeout??90,'number');const brand=field(cfg,'Название системы',c.brand),subtitle=field(cfg,'Подзаголовок',c.subtitle),theme=select(cfg,'Тема',[['dark','Тёмная'],['light','Светлая']],c.theme),accent=field(cfg,'Акцентный цвет',c.accent,'color'),cool=field(cfg,'Пауза после голоса, мс (500–10000)',c.cooldown,'number'),thank=field(cfg,'Сообщение после ответа',c.thank_you),chart=select(cfg,'Тип диаграммы',[['stack','Общая шкала'],['bars','Отдельные полосы'],['donut','Кольцевая диаграмма']],c.chart),count=check(cfg,'Показывать количество ответов',c.show_count),percent=check(cfg,'Показывать проценты',c.show_percent),pageSize=select(cfg,'Вопросов на одном экране',[[1,'1 — крупный план'],[2,'2 — рядом'],[4,'4 — обзор']],c.display_page_size),rotate=field(cfg,'Автопереключение страниц, секунд (0 — выключено)',c.display_rotate,'number'),voteLayout=select(cfg,'Расположение кнопок терминала',[['auto','Автоматически'],['grid','Плитки'],['list','Список']],c.vote_layout),voteStyle=select(cfg,'Оформление кнопок терминала',[['contrast','Высокая контрастность'],['soft','Спокойное оформление']],c.vote_style||'contrast'),motion=select(cfg,'Анимации интерфейса',[['full','Плавные и выразительные'],['subtle','Минимальные'],['off','Выключены']],c.motion||'full');cfg.append(add(el('div','actions'),btn('Сохранить настройки',()=>action('/api/admin/settings','PUT',{brand:brand.value,subtitle:subtitle.value,theme:theme.value,accent:accent.value,cooldown:+cool.value,thank_you:thank.value,chart:chart.value,show_count:count.checked,show_percent:percent.checked,display_page_size:+pageSize.value,display_rotate:+rotate.value,vote_layout:voteLayout.value,vote_style:voteStyle.value,motion:motion.value,idle_timeout:+idle.value,backup_enabled:backupEnabled.checked,backup_retention:+backupRetention.value}),'')));}
function renderAdmin(){
 const s=adminState;main.className='admin admin-shell';main.replaceChildren();adminSections=new Map();adminNav=new Map();
 const side=el('aside','admin-sidebar'),nav=el('nav','admin-nav');nav.setAttribute('aria-label','Разделы управления');side.append(el('div','eyebrow','Управление'),nav);
 const content=el('div','admin-content'),heading=el('div','admin-heading');adminTitle=el('h1');adminDescription=el('p','muted');const links=el('div','links');for(const [url,t] of [['/vote','Открыть терминал'],['/display','Открыть экран'],['/presenter','Пульт ведущего']]){const a=el('a','',t);a.href=url;a.target='_blank';a.rel='noopener';links.append(a)}
 heading.append(add(el('div'),adminTitle,adminDescription),add(el('div','actions'),btn('Обновить',loadAdmin),btn('Выйти',()=>{sessionStorage.removeItem('votemera-admin');token='';clearInterval(adminPoll);login()})));content.append(heading,links);main.append(side,content);
 for(const [id,title] of ADMIN_SECTIONS){const b=btn(title,()=>showAdminSection(id),'admin-nav-button secondary');nav.append(b);adminNav.set(id,b);const section=el('section','admin-section');section.dataset.section=id;section.hidden=true;content.append(section);adminSections.set(id,section)}
 const at=id=>adminSections.get(id);const editor=el('section','panel');eventManager(at('events'),s,editor);at('events').append(editor);editor.hidden=true;adminControls(at('overview'),s,editor);if(!s.external)liveAdmin(at('overview'),s,at('terminals'));
 if(s.external)at('display').append(add(el('section','panel'),el('h2','','Экран результатов Яндекс Форм'),el('p','muted','Отображаются отдельные полосы для каждого ответа. Скрытие и показ доступны в обзоре.')));else presentationAdmin(at('display'),s);analysisAdmin(at('analysis'),s);if(s.event.analysis)at('analysis').append(btn('Экспорт перекрёстной аналитики CSV',()=>downloadAdmin('/api/admin/cross-export/'+s.event.id,'votemera-cross.csv')));
 keyManager(at('terminals'));adminSettings(at('settings'),s);accessPanel(at('security'));maintenancePanel(at('maintenance'));if(!sandbox){diagnosticPanel(at('maintenance'));auditPanel(at('security'));qrPanel(at('terminals'));}else at('integrations').append(add(el('section','panel'),el('h2','','Яндекс Формы'),el('p','muted','Подключение доступно на локальном сервере. Не вводите реальные OAuth-токены в демоверсии.')));showAdminSection(sessionStorage.getItem('votemera-admin-section')||activeAdminSection);
}

function askDialog(title,message,yes='Подтвердить'){return new Promise(resolve=>{let accepted=false;const d=dialogForm(title,node=>{node.append(el('p','',message),add(el('div','actions'),btn(yes,()=>{accepted=true;node.close()},'danger'),btn('Отмена',()=>node.close())))});d.addEventListener('close',()=>resolve(accepted),{once:true})})}
function dialogForm(title,fill){const d=el('dialog','preview-dialog');d.append(el('h2','',title));d.addEventListener('close',()=>d.remove());document.body.append(d);fill(d);d.showModal();return d}
async function updateEventMeta(id,patch,refresh=true){try{const e=await(await api('/api/admin/event/'+id,'GET',undefined,true)).json();return await action('/api/admin/event/'+id+'/meta','PUT',{...(e.meta||{}),...patch},refresh)}catch(e){toast(e.message)}}
async function eventOptions(id){try{const e=await(await api('/api/admin/event/'+id,'GET',undefined,true)).json(),m=e.meta||{};dialogForm('Оформление мероприятия',d=>{const brand=field(d,'Название на экранах (пусто — общее)',m.brand||''),accent=field(d,'Акцентный цвет (пусто — общий)',m.accent||'');let logo=m.logo||'';const image=el('img','logo-preview');image.hidden=!logo;if(logo)image.src=logo;const upload=el('input');upload.type='file';upload.accept='image/png,image/jpeg,image/webp';upload.onchange=()=>{const file=upload.files[0];if(!file)return;if(file.size>200000){toast('Логотип не должен превышать 200 КБ');return}const reader=new FileReader();reader.onload=()=>{logo=reader.result;image.src=logo;image.hidden=false};reader.readAsDataURL(file)};d.append(el('label','','Логотип PNG, JPEG или WebP до 200 КБ'),image,upload,add(el('div','actions'),btn('Убрать логотип',()=>{logo='';image.hidden=true}),btn('Сохранить',async()=>{if(await updateEventMeta(id,{brand:brand.value,accent:accent.value,logo}))d.close()},''),btn('Отмена',()=>d.close())))})}catch(e){toast(e.message)}}
async function deviceAction(x,patch){await action('/api/admin/terminal/'+x.device_id,'PUT',{label:x.label||'',locked:!!x.locked,event_id:x.event_id??null,reload:false,...patch},false)}
function deviceOptions(x){dialogForm('Настройка терминала',d=>{const label=field(d,'Название устройства',x.label||''),assigned=select(d,'Разрешённое мероприятие',[['','Любое активное'],...adminState.events.filter(e=>!e.archived).map(e=>[e.id,e.title])],x.event_id??'');d.append(add(el('div','actions'),btn('Сохранить',async()=>{await deviceAction(x,{label:label.value,event_id:assigned.value?+assigned.value:null});d.close()},''),btn('Отмена',()=>d.close())))})}
function qrPanel(parent){const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','QR-код подключения'),el('p','muted','Код содержит только ссылку, ключ вводится отдельно.'));const input=field(panel,'Адрес терминала',location.origin+'/vote');const img=el('img','qr-image');img.hidden=true;panel.append(img,add(el('div','actions'),btn('Показать QR-код',async()=>{try{const r=await api('/api/admin/qr?url='+encodeURIComponent(input.value),'GET',undefined,true);const reader=new FileReader();reader.onload=()=>{img.src=reader.result;img.hidden=false};reader.readAsDataURL(await r.blob())}catch(e){toast(e.message)}}),btn('Открыть терминал',()=>window.open(pageUrl('vote'),'_blank','noopener'))))}
function diagnosticPanel(parent){const panel=el('section','panel'),output=el('div','management-list');parent.append(panel);panel.append(el('h2','','Проверка перед мероприятием'),el('p','muted','Проверка сервера, базы и подключений. Изображение и касание проверяются на самих устройствах.'),output,add(el('div','actions'),btn('Проверить',async()=>{try{const d=await(await api('/api/admin/diagnostics','GET',undefined,true)).json();output.replaceChildren(...[['База данных',d.database_ok?'Исправна':'Ошибка'],['Свободное место',(d.disk_free/1073741824).toFixed(1)+' ГБ'],['Терминалы онлайн',d.terminals],['Экраны результатов онлайн',d.displays],['Защита терминалов',d.access_required?'По ключам':'Без ключей'],['Служба обновления',d.update],['Версия',d.version],['Последняя резервная копия',d.backup?d.backup.state+' · '+new Date(d.backup.time*1000).toLocaleString('ru-RU'):'Ещё не создана']].map(([k,v])=>add(el('div','management-row'),el('strong','',k),el('span','',String(v)))))}catch(e){toast(e.message)}},''),btn('Проверить экран',()=>window.open(pageUrl('display'),'_blank','noopener')),btn('Проверить терминал',()=>window.open(pageUrl('vote'),'_blank','noopener'))))}
function auditPanel(parent){const panel=el('section','panel'),list=el('div','management-list');parent.append(panel);panel.append(el('h2','','Журнал действий'),btn('Обновить журнал',async()=>{try{const d=await(await api('/api/admin/audit','GET',undefined,true)).json();list.replaceChildren(...d.entries.map(x=>add(el('div','management-row'),add(el('div','management-meta'),el('strong','',x.action),el('span','muted',x.detail)),el('span','muted',new Date(x.created*1000).toLocaleString('ru-RU')))));if(!d.entries.length)list.append(el('p','muted','Записей пока нет'))}catch(e){toast(e.message)}}),list)}
function renderPresenter(){const s=adminState;if(s.external){clearInterval(adminPoll);main.className='admin presenter';main.replaceChildren(el('h1','','Пульт ведущего · Яндекс Формы'),el('p','muted',s.event.title),add(el('div','actions'),btn('Показать',()=>action('/api/admin/live','PATCH',{display:'live'}),''),btn('Скрыть',()=>action('/api/admin/live','PATCH',{display:'hidden'})),btn('Админка',()=>location.href=pageUrl('admin'))));return}clearInterval(adminPoll);main.className='admin presenter';main.replaceChildren(el('h1','','Пульт ведущего'));const current=el('div','panel'),controls=el('div','panel'),preview=el('div','panel');main.append(current,controls,preview);
 const apply=async patch=>{try{adminState=await(await api('/api/admin/live','PATCH',patch,true)).json();appearance(adminState);renderPresenter()}catch(e){toast(e.message)}};
 const update=f=>{current.replaceChildren(el('div','eyebrow',f.opened?'Голосование открыто':'Голосование закрыто'),el('h2','',f.event.title),el('p','muted',f.event.rounds.find(r=>r.id===f.round)?.name||''),el('p','',f.opened&&f.deadline?'Осталось '+countdownText(f.deadline):''));preview.replaceChildren(el('h2','','Предпросмотр результатов'),...f.event.rounds.map(r=>add(el('div','management-row'),el('strong','',r.name),el('span','',String(r.total)+' анкет'))));const active=f.event.rounds.find(r=>r.id===f.round);if(active?.results)for(const [i,q] of f.event.questions.entries()){const res=active.results[i],group=el('div','presenter-preview');group.append(el('strong','',q.question));q.options.forEach((o,j)=>{const n=res.counts[j],bar=el('div','bar'),seg=el('div','segment');seg.style.background=o.color;seg.style.width=pct(n,res.total)+'%';bar.append(seg);group.append(add(el('div','management-heading'),el('span','',o.label),el('span','muted',n+' · '+fmt(pct(n,res.total))+'%')),bar)});preview.append(group)}};
 update(s);const round=select(controls,'Этап',s.event.rounds.map(r=>[r.id,r.name]),s.round),duration=field(controls,'Таймер, минут',0,'number');duration.min=0;duration.max=1440;const screen=select(controls,'Экран результатов',[['live','Текущий этап'],['compare','Сравнение'],['analysis','Перекрёстный анализ'],['analysis_compare','Сравнение аналитики'],['presentation','Сценарий'],['hidden','Скрыть']],s.display);
 controls.append(add(el('div','presenter-buttons'),btn('Открыть этап',()=>apply({opened:true,round:+round.value,duration:Math.round(+duration.value*60),display:screen.value}),''),btn('Закрыть этап',()=>apply({opened:false})),btn('Изменить экран',()=>apply({display:screen.value})),btn('Скрыть результаты',()=>apply({display:'hidden'})),btn('На экран',()=>window.open(pageUrl('display'),'_blank','noopener')),btn('Админка',()=>location.href=pageUrl('admin'))));
 adminPoll=setInterval(async()=>{if(!current.isConnected||document.hidden)return;try{const f=await(await api('/api/admin/state','GET',undefined,true)).json();update(f);timerEnd=f.opened?f.deadline:null;clockTick();conn(true)}catch{conn(false)}},2000);
}

function setupWizard(node,data,title,rounds,list,tools){
 const heading=node.firstElementChild,save=node.querySelector('.editor-save'),summary=el('div','panel'),step1=el('div'),step2=el('div'),step3=el('div'),steps=[step1,step2,step3];
 heading.textContent='Мастер создания мероприятия';
 step1.append(title.previousElementSibling,title,rounds.previousElementSibling,rounds);
 step2.append(list,save.firstElementChild);
 step3.append(summary,tools,save);
 const progress=el('div','eyebrow'),nav=el('div','actions');let i=0;
 const previous=btn('← Назад',()=>{i--;show()}),next=btn('Далее →',()=>{const d=data();if(i===0&&(!d.title.trim()||!d.rounds.length)){toast('Введите название и хотя бы один этап');return}if(i===1&&d.questions.some(q=>!q.question.trim()||q.options.some(o=>!o.label.trim()))){toast('Заполните вопросы и варианты');return}i++;show()},'');
 nav.append(previous,next,btn('Отменить создание',()=>{if(confirm('Отменить создание мероприятия?')){node.hidden=true;node.replaceChildren()}}));
 node.replaceChildren(heading,progress,...steps,nav);
 function show(){progress.textContent='Шаг '+(i+1)+' из 3 · '+['Название и этапы','Вопросы и ответы','Проверка и сохранение'][i];steps.forEach((s,j)=>s.hidden=i!==j);previous.hidden=i===0;next.hidden=i===2;if(i===2){const d=data();summary.replaceChildren(el('h3','',d.title),el('p','',d.rounds.join(' → ')),el('p','muted',d.questions.length+' вопросов'),...d.questions.map((q,n)=>el('p','',(n+1)+'. '+q.question+' ('+q.options.length+' вариантов)')))}}
 show();
}
function yandexPanel(parent){

 const panel=el('section','panel'),status=el('p','notice','Загрузка состояния…'),list=el('div','management-list');parent.append(panel);panel.append(el('h2','','Яндекс Формы для бизнеса'),status,el('p','muted','OAuth-токен хранится только на сервере. Используйте HTTPS.'));
 const org=field(panel,'ID организации',''),tokenInput=field(panel,'OAuth-токен','','password');tokenInput.autocomplete='off';
 async function req(path,method='GET',data){try{return await(await api(path,method,data,true)).json()}catch(e){toast(e.message);return null}}
 const save=btn('Проверить и подключить',async()=>{save.disabled=true;try{if(await req('/api/admin/yandex','PUT',{org:org.value.trim(),token:tokenInput.value.trim()})){tokenInput.value='';toast('Подключение проверено');await refresh()}}finally{save.disabled=false}},'');
 panel.append(add(el('div','actions'),save,btn('Отключить',async()=>{if(await askDialog('Отключить Яндекс Формы','Сохранённые результаты останутся, синхронизация остановится.','Отключить')){await req('/api/admin/yandex','DELETE');refresh()}})));
 const create=el('section','panel');parent.append(create);create.append(el('h2','','Создать мероприятие из формы'));
 const search=field(create,'Поиск формы',''),form=select(create,'Форма',[],'');const next=btn('Следующие 50',()=>loadForms(offset+50)),prev=btn('Предыдущие 50',()=>loadForms(Math.max(0,offset-50)));
 const title=field(create,'Название импортируемого опроса',''),from=field(create,'Ответы начиная с (местное время)','','datetime-local'),to=field(create,'Ответы до (местное время)','','datetime-local'),interval=field(create,'Интервал синхронизации, секунд',5,'number');interval.min=5;interval.max=3600;
 const questions=el('div','management-list');create.append(el('h3','','Вопросы для отображения'),questions);
 let forms=[],offset=0,chosen=[],generation=0;
 async function loadQuestions(){const sid=form.value,g=++generation;questions.replaceChildren();chosen=[];if(!sid)return;title.value=forms.find(f=>f.id===sid)?.name||'';const data=await req('/api/admin/yandex/forms/'+sid);if(!data||g!==generation)return;
 for(const q of data.questions){const row=el('div','management-row'),label=el('div','management-meta');label.append(el('strong','',q.label),el('span','muted',q.supported?q.options.map(o=>o.label).join(' · '):'Тип или количество вариантов пока не поддерживается'));const input=el('input');input.type='checkbox';input.disabled=!q.supported;input.setAttribute('aria-label',q.label);chosen.push({id:q.id,input});row.append(label,input);questions.append(row)}
 }
 async function loadForms(n=0){const data=await req('/api/admin/yandex/forms?search='+encodeURIComponent(search.value)+'&offset='+n);if(!data)return;offset=n;forms=data.forms;form.replaceChildren(...forms.map(f=>{const o=el('option','',f.name+(f.published?'':' (не опубликована)'));o.value=f.id;return o}));next.disabled=forms.length<50;prev.disabled=!offset;await loadQuestions()}
 form.onchange=loadQuestions;create.insertBefore(add(el('div','actions'),btn('Загрузить формы',()=>loadForms(0)),prev,next),title.previousElementSibling);
 const addEvent=btn('Создать из формы',async()=>{const ids=chosen.filter(x=>x.input.checked).map(x=>x.id);if(!ids.length||ids.length>12)return toast('Выберите от 1 до 12 вопросов');if(!form.value)return toast('Выберите форму');addEvent.disabled=true;try{const d=await req('/api/admin/yandex/events','POST',{form_id:form.value,title:title.value,questions:ids,date_from:from.value?new Date(from.value).toISOString():'',date_to:to.value?new Date(to.value).toISOString():'',interval:+interval.value});if(d){await req('/api/admin/yandex/events/'+d.id+'/sync','POST',{});toast('Мероприятие создано');await loadAdmin()}}finally{addEvent.disabled=false}},'');create.append(add(el('div','actions'),addEvent));
 const sources=el('section','panel');parent.append(sources);sources.append(el('h2','','Подключённые мероприятия'),list);
 async function refresh(){const d=await req('/api/admin/yandex');if(!d||!panel.isConnected)return;status.textContent=d.connected?'Подключение сохранено · организация '+d.org:'Подключение не настроено';if(!org.value)org.value=d.org;create.hidden=!d.connected;list.replaceChildren();
 for(const x of d.sources){const row=el('div','management-row'),meta=el('div','management-meta');meta.append(el('strong','',x.title),el('span','muted',x.total+' анкет · '+(x.last_sync?new Date(x.last_sync*1000).toLocaleString('ru-RU'):'ещё не синхронизировано')+' · '+(x.error||(!x.enabled?'Остановлено':'Интервал '+x.interval+' с'))));const link=el('a','', 'Открыть форму');link.href='https://forms.yandex.ru/cloud/u/'+x.form_id+'/';link.target='_blank';link.rel='noopener noreferrer';meta.append(link);row.append(meta,add(el('div','actions'),btn('Синхронизировать',async()=>{if(await req('/api/admin/yandex/events/'+x.event_id+'/sync','POST',{}))toast('Синхронизация поставлена в очередь')}),btn(x.enabled?'Приостановить':'Возобновить',async()=>{await req('/api/admin/yandex/events/'+x.event_id,'PUT',{enabled:!x.enabled,interval:x.interval});refresh()}),btn('Интервал',async()=>{const value=prompt('Интервал, секунд (5–3600)',x.interval);if(value!==null){await req('/api/admin/yandex/events/'+x.event_id,'PUT',{enabled:x.enabled,interval:+value});refresh()}}),btn('QR-код',async()=>{try{const r=await api('/api/admin/yandex/events/'+x.event_id+'/qr','GET',undefined,true),blob=await r.blob();dialogForm('QR-код формы',d=>{const img=el('img','qr-image');const reader=new FileReader();reader.onload=()=>img.src=reader.result;reader.readAsDataURL(blob);d.append(img,btn('Закрыть',()=>d.close()))})}catch(e){toast(e.message)}})));list.append(row)}
 if(!d.sources.length)list.append(el('p','muted','Нет подключённых мероприятий'));
 }
 refresh();const ticker=setInterval(()=>{if(!parent.isConnected){clearInterval(ticker);return}if(!parent.hidden&&!document.hidden)refresh()},5000);

}
function renderEditor(node,e=null,wizard=false){
 node.replaceChildren(el('h2','',e?'Редактирование мероприятия':'Конструктор анкеты'));const title=field(node,'Название мероприятия',e?.title||''),rounds=field(node,'Этапы, по одному на строку',e?.rounds.map(r=>r.name).join('\n')||'До мероприятия\nПосле мероприятия','textarea');
 const list=el('div','question-editor');const palette=['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4'];
 const templates={'binary':['Да','Нет'],'neutral':['Да','Не уверен','Нет'],'frequency':['Ежедневно','Иногда','Редко','Никогда'],'scale':['1','2','3','4','5']};
 const data=()=>({title:title.value,rounds:rounds.value.split('\n').map(x=>x.trim()).filter(Boolean),questions:[...list.children].map(x=>x.getData())});
 const renumber=()=>[...list.children].forEach((b,i)=>b.querySelector('.question-number').textContent='Вопрос '+(i+1));
 function captureRules(){const blocks=[...list.children];return blocks.filter(b=>b.rule?.conditional.checked).map(b=>{const target=blocks[+b.rule.source.value-1];return {b,target,option:target?.rule?.options.children[+b.rule.sourceChoice.value-1]}})}
 function restoreRules(rules){const blocks=[...list.children];for(const {b,target,option} of rules){if(!blocks.includes(b))continue;const i=blocks.indexOf(target),j=target?.rule?[...target.rule.options.children].indexOf(option):-1;if(i<0||i>=blocks.indexOf(b)||j<0){toast('Проверьте условие зависимого вопроса');b.rule.source.value=0;b.rule.sourceChoice.value=0}else{b.rule.source.value=i+1;b.rule.sourceChoice.value=j+1}}renumber()}
 function rearrange(fn){const rules=captureRules();fn();restoreRules(rules)}
 function dragHandle(node,parent,label){const handle=el('span','drag-handle','⠿');handle.title=label;handle.draggable=true;handle.setAttribute('aria-label',label);handle.ondragstart=e=>{parent._drag=node;e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain','move')};handle.ondragend=()=>{parent._drag=null};node.ondragover=e=>{if(parent._drag&&parent._drag!==node){e.preventDefault();e.dataTransfer.dropEffect='move'}};node.ondrop=e=>{if(!parent._drag||parent._drag===node)return;e.preventDefault();e.stopPropagation();const moving=parent._drag;rearrange(()=>{if(e.clientY>node.getBoundingClientRect().top+node.offsetHeight/2)node.after(moving);else node.before(moving)});parent._drag=null};return handle}
 
 function question(q,after=null){if(list.children.length>=12){toast('Не более 12 вопросов');return}const block=el('section','question-edit');block.append(add(el('div','management-heading'),el('h3','question-number'),dragHandle(block,list,'Перетащить вопрос')));
 const input=field(block,'Текст вопроса',q?.question||'','textarea'),desc=field(block,'Пояснение (необязательно)',q?.description||''),confirmation=check(block,'Подтверждать выбор отдельной кнопкой',q?.confirm||false),options=el('div','option-list');
 const conditional=check(block,'Показывать только при определённом предыдущем ответе',!!q?.show_if),source=field(block,'Номер предыдущего вопроса',q?.show_if?q.show_if.question+1:1,'number'),sourceChoice=field(block,'Номер варианта предыдущего вопроса',q?.show_if?q.show_if.choice+1:1,'number');block.rule={conditional,source,sourceChoice,options};const updateRule=()=>{source.disabled=sourceChoice.disabled=!conditional.checked};conditional.onchange=updateRule;updateRule();
 const preset=select(block,'Шаблон вариантов',[['','Выберите шаблон'],['binary','Да / Нет'],['neutral','Да / Не уверен / Нет'],['frequency','Частота использования'],['scale','Шкала от 1 до 5']], '');
 block.append(el('label','','Варианты ответа'),options);
 function option(o){if(options.children.length>=6){toast('Не более 6 вариантов');return}const row=el('div','option-row'),label=el('input'),color=el('input');label.placeholder='Вариант ответа';label.value=o?.label||'';label.setAttribute('aria-label','Название варианта');color.type='color';color.value=o?.color||palette[options.children.length];color.setAttribute('aria-label','Цвет варианта');row.append(label,color,btn('↑',()=>{if(row.previousElementSibling)rearrange(()=>options.insertBefore(row,row.previousElementSibling))}),btn('↓',()=>{if(row.nextElementSibling)rearrange(()=>options.insertBefore(row.nextElementSibling,row))}),btn('×',()=>{if(options.children.length>2)rearrange(()=>row.remove())}),dragHandle(row,options,'Перетащить вариант'));options.append(row)}
 (q?.options||templates.binary.map((label,i)=>({label,color:palette[i]}))).forEach(option);
 preset.onchange=()=>{if(preset.value&&confirm('Заменить варианты ответа шаблоном?'))rearrange(()=>{options.replaceChildren();templates[preset.value].forEach((label,i)=>option({label,color:palette[i]}))});preset.value=''};
 block.getData=()=>({question:input.value,description:desc.value,confirm:confirmation.checked,...(conditional.checked?{show_if:{question:Number(source.value)-1,choice:Number(sourceChoice.value)-1}}:{}),options:[...options.children].map(r=>({label:r.children[0].value,color:r.children[1].value}))});
 block.append(add(el('div','actions'),btn('+ Вариант',()=>option()),btn('↑ Вопрос',()=>{if(block.previousElementSibling)rearrange(()=>list.insertBefore(block,block.previousElementSibling))}),btn('↓ Вопрос',()=>{if(block.nextElementSibling)rearrange(()=>list.insertBefore(block.nextElementSibling,block))}),btn('Копировать',()=>rearrange(()=>question(block.getData(),block))),btn('Удалить вопрос',()=>{if(list.children.length>1)rearrange(()=>block.remove())},'danger')));
 if(after)after.after(block);else list.append(block);renumber()}
 const tools=el('div','actions');tools.append(btn('Предпросмотр',()=>{const d=data(),preview=el('dialog','preview-dialog');let idx=0;function show(){const q=d.questions[idx];preview.replaceChildren(el('div','eyebrow',`Вопрос ${idx+1} из ${d.questions.length}`),el('h2','',q.question||'Текст вопроса'),el('p','muted',q.description));if(q.show_if)preview.append(el('p','muted',`Условие: вопрос ${q.show_if.question+1}, вариант ${q.show_if.choice+1}`));const c=el('div','choices choice-grid choices-'+q.options.length);q.options.forEach((o,i)=>{const b=btn(o.label||'Вариант '+(i+1),()=>{},'choice');b.disabled=true;b.style.setProperty('--choice',o.color);c.append(b)});preview.append(c,add(el('div','actions'),btn('←',()=>{idx=Math.max(0,idx-1);show()}),btn('→',()=>{idx=Math.min(d.questions.length-1,idx+1);show()}),btn('Закрыть',()=>preview.close())))}preview.addEventListener('close',()=>preview.remove());document.body.append(preview);show();preview.showModal()}),btn('Экспорт шаблона JSON',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(data(),null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='votemera-template.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}),btn('Импорт шаблона JSON',()=>{const f=el('input');f.type='file';f.accept='.json,application/json';f.onchange=async()=>{try{const file=f.files[0];if(!file||file.size>100000)throw Error('Файл слишком большой');const d=JSON.parse(await file.text());if(typeof d.title!=='string'||!Array.isArray(d.rounds)||d.rounds.length<1||d.rounds.length>12||!d.rounds.every(x=>typeof x==='string')||!Array.isArray(d.questions)||d.questions.length<1||d.questions.length>12||!d.questions.every(q=>typeof q.question==='string'&&Array.isArray(q.options)&&q.options.length>=2&&q.options.length<=6&&q.options.every(o=>typeof o.label==='string'&&/^#[0-9a-f]{6}$/i.test(o.color))))throw Error('Неверный формат шаблона');if(!confirm('Заменить текущую анкету?'))return;title.value=d.title;rounds.value=d.rounds.join('\n');list.replaceChildren();d.questions.forEach(q=>question(q))}catch(e){toast(e.message)}};f.click()}));node.append(tools,list);
 (e?.questions||[null]).forEach(q=>question(q));node.append(add(el('div','actions editor-save'),btn('+ Вопрос',()=>question()),btn(e?'Сохранить изменения':'Создать',()=>action('/api/admin/event'+(e?'/'+e.id:''),e?'PUT':'POST',data()),''),...(e?[btn('Создать копию мероприятия',()=>{const d=data();d.title+=' — копия';action('/api/admin/event','POST',d)})]:[]),btn('Отмена',()=>{node.hidden=true;node.replaceChildren()})));
 if(wizard)setupWizard(node,data,title,rounds,list,tools);
}
initFit();
setInterval(clockTick,500);
if(mode==='display'&&new URLSearchParams(location.search).get('demo')==='1')demoDisplay();else if(mode==='vote'||mode==='display')startStream();else if(mode==='admin'){token?loadAdmin():login()}else if(mode==='presenter'){sandbox?loadAdmin():token?loadAdmin():login()}else{main.replaceChildren(el('h1','center','Votemera'),el('p','center muted','Локальная система интерактивного голосования'));const row=el('div','row');for(const [p,t] of [['vote','Терминал'],['display','Экран результатов'],['admin','Администрирование'],['presenter','Пульт ведущего']])row.append(btn(t,()=>location.href='/'+p,''));main.append(row);api('/api/state').then(r=>r.json()).then(s=>{appearance(s);conn(true)}).catch(()=>conn(false))}
