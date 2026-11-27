'use strict';
document.body.dataset.mode=location.pathname.slice(1)||'home';
const $=id=>document.getElementById(id), main=$('main'), mode=location.pathname.slice(1)||'home';
// Only public screens are fitted. Admin keeps normal scrolling.
const kiosk=mode==='vote'||mode==='display';
let fitStage=null,fitFrame=0,fitViewport='',fitScale=1;
function scheduleFit(){if(kiosk&&!fitFrame)fitFrame=requestAnimationFrame(fitScreen)}
function fitScreen(){
 fitFrame=0;if(!fitStage||!fitStage.clientWidth||!fitStage.clientHeight)return;
 const w=fitStage.clientWidth,h=fitStage.clientHeight;
 const size=w+':'+h;
 if(fitViewport!==size){fitViewport=size;if(mode==='display'&&state)renderDisplay()}
 // Layout dimensions are untransformed, so shrinking never feeds back into measurement.
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
function conn(v){online=v;$('connection').textContent=v?'В сети':'Связь потеряна';$('connection').classList.toggle('online',v)}
function appearance(s){const c=s.settings;$('brand').textContent=c.brand;$('subtitle').textContent=c.subtitle;$('footer').textContent=c.brand;document.title=c.brand+' · '+({vote:'Голосование',display:'Результаты',admin:'Управление'}[mode]||'Главная');$('version').textContent='v'+s.version;if(document.body.dataset.motion!==c.motion){document.body.dataset.motion=c.motion||'full';stopCounters()}document.body.classList.toggle('light',c.theme==='light');document.documentElement.style.setProperty('--accent',c.accent)}
$('fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen?.().catch(()=>toast('Используйте F11'))};
async function api(path,method='GET',data,auth=false){const headers={};if(data!==undefined)headers['Content-Type']='application/json';if(auth)headers.Authorization='Bearer '+token;else if(key)headers['X-Terminal-Key']=key;const r=await fetch(path,{method,headers,body:data===undefined?undefined:JSON.stringify(data),cache:'no-store',signal:AbortSignal.timeout?AbortSignal.timeout(path.startsWith('/api/admin/update')?20000:8000):undefined});if(!r.ok){let msg='Ошибка '+r.status;try{const j=await r.json();msg=typeof j.detail==='string'?j.detail:msg}catch{}const e=new Error(msg);e.status=r.status;throw e}return r}
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
 if(s.terminal_key_required&&!key){main.append(add(el('div','center wait'),el('h1','','Подключение терминала'),btn('Подключить терминал',()=>{key=prompt('Ключ терминала')||'';sessionStorage.setItem('votemera-key',key);heartbeat();renderVote()},'')));return}
 if(pending){main.append(add(el('div','center wait'),el('h2','','Ответ не подтверждён'),el('p','muted','Повторная отправка не создаст второй голос.'),btn('Повторить отправку',()=>send(pending),''),btn('Отменить',()=>{if(confirm('Ответ мог быть записан. Отменить повторную отправку?')){clearDraft();renderVote()}})));return}
 if(!s.opened||!online){main.append(add(el('div','center wait'),el('h1','',online?'Голосование закрыто':'Нет связи с сервером'),el('p','muted',online?'Ожидайте начала этапа':'Ответы временно не принимаются')));return}
 main.append(el('h1','vote-question',q.question));if(q.description)main.append(el('p','question-description',q.description));
 const accept=i=>{if(busy||ballotScope!==scope||answers.length>=qs.length||(!q.confirm&&Date.now()<nextTapAt))return;nextTapAt=Date.now()+300;answers.push(i);selectedChoice=null;skipHidden(qs);if(answers.length===qs.length)send({round:s.round,epoch:s.epoch,choices:[...answers],nonce:hex(),terminal});else {saveDraft();renderVote()}};
 const layout=s.settings.vote_layout==='auto'?(q.options.some(o=>o.label.length>35)?'list':'grid'):s.settings.vote_layout;
 const choices=el('div','choices choice-'+layout+' choices-'+q.options.length);
 q.options.forEach((o,i)=>{const b=btn('',()=>{if(q.confirm){selectedChoice=i;saveDraft();resetIdle();choices.querySelectorAll('.choice').forEach((choice,j)=>{choice.classList.toggle('selected',j===i);choice.setAttribute('aria-pressed',String(j===i));choice.querySelector('.choice-indicator').textContent=j===i?'✓':'›'});main.querySelector('.vote-next').disabled=false}else accept(i)},'choice'+(q.confirm&&selectedChoice===i?' selected':''));b.style.setProperty('--choice',o.color);b.style.setProperty('--order',i);b.setAttribute('aria-label',o.label);b.append(el('span','choice-index',String(i+1).padStart(2,'0')),el('span','choice-label',o.label),el('span','choice-indicator',q.confirm&&selectedChoice===i?'✓':'›'));if(q.confirm)b.setAttribute('aria-pressed',String(selectedChoice===i));choices.append(b)});
 main.append(choices);const nav=el('div','vote-nav');if(answers.length)nav.append(btn('← Назад',()=>{do{selectedChoice=answers.pop()}while(selectedChoice===null&&answers.length);saveDraft();renderVote()}),btn('Начать заново',()=>{clearDraft();renderVote()}));else nav.append(el('span','muted','Выберите один вариант'));
 if(q.confirm){const next=btn(answers.length===qs.length-1?'Отправить ответы':'Далее →',()=>accept(selectedChoice),'');next.classList.add('vote-next');next.disabled=selectedChoice===null;nav.append(next)}main.append(nav);
}
async function send(v){if(busy)return;busy=true;pending=v;saveDraft();clearTimeout(idleTimer);main.querySelectorAll('button').forEach(b=>b.disabled=true);try{await api('/api/ballot','POST',v);clearDraft();main.replaceChildren(add(el('div','center wait'),el('div','success','✓'),el('h1','',state.settings.thank_you),el('p','muted','Терминал скоро будет готов к следующему участнику')));setTimeout(()=>{busy=false;renderVote()},state.settings.cooldown)}catch(e){busy=false;if(e.status===409||e.status===422){clearDraft()}else if(e.status===403){key='';sessionStorage.removeItem('votemera-key')}if(pending)saveDraft();toast(e.message||'Не удалось подтвердить голос');renderVote()}}
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
function analysisAdmin(parent,s){
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Перекрёстный анализ'),el('p','muted','Выберите два вопроса активного мероприятия. Результаты показывают распределение внутри групп, без идентификации участников.'));
 const ac=s.event.analysis;if(!ac){panel.append(el('p','muted','Добавьте минимум два вопроса.'));return}
 const opts=s.event.questions.map((q,i)=>[i,(i+1)+'. '+q.question]);const a=select(panel,'Группировать по вопросу',opts,ac.first),b=select(panel,'Анализировать ответы на вопрос',opts,ac.second),chart=select(panel,'Визуализация',[['stack','Групповые шкалы'],['heatmap','Тепловая таблица']],ac.chart);
 panel.append(add(el('div','actions'),btn('Сохранить пару вопросов',()=>action('/api/admin/analysis/'+s.event.id,'PUT',{first:+a.value,second:+b.value,chart:chart.value}),'')));
 const preview=el('div','cross-rounds');s.event.rounds.forEach(r=>preview.append(crossPanel(s,r)));panel.append(preview);
}

let scenarioIndex=0,scenarioTimer=null,scenarioSignature='';
function scenarioState(s){const p=s?.event?.presentation;const active=s?.display==='presentation';const sig=JSON.stringify([active,s?.event?.id,p]);if(sig!==scenarioSignature){scenarioSignature=sig;scenarioIndex=0;clearInterval(scenarioTimer);if(active&&p?.slides.length>1)scenarioTimer=setInterval(()=>{scenarioIndex=(scenarioIndex+1)%p.slides.length;renderDisplay()},p.seconds*1000)}if(!active)return s;const slide=p?.slides[scenarioIndex]||{mode:'live',question:-1};return {...s,display:slide.mode,scenarioQuestion:slide.question,scenario:true}}
function renderDisplay(){
 const s=scenarioState(state);if(!s?.event){main.replaceChildren(el('h1','center','Ожидание мероприятия'));return}
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
 const sig=JSON.stringify([s.event.id,s.event.title,qs,s.display,s.settings,rounds.map(r=>[r.id,r.name]),s.opened,displayFocus,displayPage,ordered,s.scenarioQuestion,s.scenario,pageSize,pages]);
 if(main.dataset.sig===sig){if(s.display!=='hidden'){for(const i of indices){for(const r of rounds)updateChart(r,s,i);updateDelta(s,rounds,i)}updateTotals(rounds)}return}
 clearInterval(rotateTimer);main.dataset.sig=sig;main.className='display-main'+(indices.length===1?' spotlight':'')+(s.display==='compare'?' compare-mode':'');
 const top=el('div','display-heading');const labels=el('div');labels.append(el('div','eyebrow',s.display==='compare'?'Сравнение этапов':s.opened?'Голосование открыто':'Голосование завершено'),el('h1','',s.event.title));top.append(labels);
 main.replaceChildren(top);
 if(s.display==='hidden'){main.append(add(el('div','center wait'),el('h2','','Результаты пока скрыты'),el('p','muted','Они появятся по решению организатора')));return}
 const totals=el('div','display-totals');for(const r of rounds){const tile=el('div','total-tile');tile.append(el('span','',r.name),el('strong','total-number','0'),el('small','','Участников'));tile.dataset.round=r.id;totals.append(tile)}top.append(totals);updateTotals(rounds);
 if((qs.length>1&&!s.scenario)||pages>1){const controls=el('div','display-controls');if(qs.length>1&&!s.scenario){const picker=select(controls,'Вопрос',[[-1,'Обзор вопросов'],...ordered.map(i=>[i,(i+1)+'. '+qs[i].question])],displayFocus);picker.onchange=()=>{displayFocus=+picker.value;displayPage=0;renderDisplay()}}if(pages>1){const nav=el('div','display-nav');nav.append(btn('‹',()=>{displayPage=(displayPage+pages-1)%pages;renderDisplay()}),el('span','muted',`${displayPage+1} / ${pages}`),btn('›',()=>{displayPage=(displayPage+1)%pages;renderDisplay()}));controls.append(nav);if(s.settings.display_rotate>0)rotateTimer=setInterval(()=>{displayPage=(displayPage+1)%pages;renderDisplay()},s.settings.display_rotate*1000)}main.append(controls)}
 const grid=el('div',indices.length>1?'question-grid':'question-single');grid.dataset.size=indices.length;main.append(grid);
 for(const i of indices){const q=qs[i],group=el('section','question-group');group.style.setProperty('--order',indices.indexOf(i));group.append(el('div','question-kicker','ВОПРОС '+String(i+1).padStart(2,'0')),el('h2','',q.question));
 const panels=el('div','round-panels');group.append(panels);grid.append(group);
 for(const r of rounds){const panel=el('section','panel result-panel');panel.id='round-'+r.id+'-'+i;panel.append(add(el('div','round-title'),el('strong','',r.name),el('span','total')));
 const stats=el('div','stats result-stats');for(const o of q.options){const metric=el('div','metric');metric.style.setProperty('--option-color',o.color);metric.append(add(el('span','metric-label'),el('i','swatch'),el('span','',o.label)),add(el('div','metric-values'),el('b','count'),el('small','share')));if(s.settings.chart==='bars'){const bar=el('div','bar'),seg=el('div','segment');seg.style.background=o.color;seg.style.width='0%';bar.append(seg);metric.append(bar)}stats.append(metric)}panel.append(stats);
 if(s.settings.chart==='stack'){const bar=el('div','stack');for(const o of q.options){const seg=el('div','segment');seg.style.background=o.color;seg.style.width='0%';bar.append(seg)}panel.append(bar)}panels.append(panel);updateChart(r,s,i)}
 if(rounds.length>1){const delta=el('section','delta-panel');delta.id='delta-'+i;group.append(delta);updateDelta(s,rounds,i)}}
}
function updateTotals(rounds){for(const t of main.querySelectorAll('.total-tile')){const r=rounds.find(x=>String(x.id)===t.dataset.round);numeric(t.querySelector('.total-number'),r?.total??0,countText)}}
function updateChart(r,s,i){const panel=$('round-'+r.id+'-'+i),res=r.results?.[i];if(!panel||!res)return;panel.querySelector('.total').textContent='Ответили: '+res.total+' из '+r.total;panel.querySelectorAll('.count').forEach((n,j)=>{n.hidden=!s.settings.show_count;numeric(n,res.counts[j],countText)});panel.querySelectorAll('.share').forEach((n,j)=>{n.hidden=!s.settings.show_percent;numeric(n,pct(res.counts[j],res.total),shareText)});panel.querySelectorAll('.segment').forEach((n,j)=>{n.style.width=pct(res.counts[j],res.total)+'%'});}
function updateDelta(s,rounds,i){const node=$('delta-'+i);if(!node)return;const a=rounds[0],b=rounds.at(-1),ar=a.results[i],br=b.results[i],view=el('section','delta-panel');view.id=node.id;view.append(el('h3','','Изменение: '+a.name+' → '+b.name));if(!ar.total||!br.total){view.append(el('p','muted','Для сравнения нужны голоса в обоих этапах'));patchView(node,view);return}const row=el('div','delta');s.event.questions[i].options.forEach((o,j)=>{const diff=br.counts[j]-ar.counts[j],dp=pct(br.counts[j],br.total)-pct(ar.counts[j],ar.total);row.append(add(el('div'),el('span','',o.label),el('b','',(diff>0?'+':'')+diff+' голосов'),el('small','muted',(dp>0?'+':'')+fmt(dp)+' п. п.')))});view.append(row,el('p','muted','Разница общих результатов, не число участников, изменивших мнение.'));patchView(node,view)}
function heartbeat(){if(mode==='vote'&&online&&(!state?.terminal_key_required||key))api('/api/terminal/heartbeat','POST',{terminal}).catch(()=>{})}
if(mode==='vote')setInterval(heartbeat,10000);
function demoDisplay(){let current=null;const es=new EventSource('/api/stream?view=vote');es.onmessage=e=>{const s=JSON.parse(e.data);if(!s.event)return;const id=s.event.id;if(current?.event.id!==id){current=s;current.event.title='ДЕМО · '+s.event.title;current.display='compare';current.opened=true;for(const r of current.event.rounds){r.results=s.event.questions.map(q=>({counts:q.options.map(()=>0),total:0}));r.total=0}}state=current;appearance(state);$('subtitle').textContent='Демонстрация · данные вымышлены';conn(true);renderDisplay()};es.onerror=()=>conn(false);setInterval(()=>{if(!current)return;for(const r of current.event.rounds){r.total++;for(const q of r.results){q.counts[Math.floor(Math.random()*q.counts.length)]++;q.total++}}renderDisplay()},900)}
function startStream(){const es=new EventSource('/api/stream?view='+mode);es.onopen=()=>conn(true);es.onmessage=e=>{state=JSON.parse(e.data);conn(true);appearance(state);mode==='vote'?renderVote():renderDisplay()};es.onerror=()=>{conn(false);if(mode==='vote')renderVote()}}
function field(parent,label,value,type='text'){const id='f-'+hex();const l=el('label','',label);l.htmlFor=id;const n=el(type==='textarea'?'textarea':'input');n.id=id;if(type!=='textarea')n.type=type;n.value=value;parent.append(l,n);return n}
function select(parent,label,items,value){const l=el('label','',label),n=el('select');n.id='f-'+hex();l.htmlFor=n.id;items.forEach(([v,t])=>{const o=el('option','',t);o.value=v;n.append(o)});n.value=value;parent.append(l,n);return n}
function check(parent,label,value){const row=el('div','check'),n=el('input');n.type='checkbox';n.checked=value;n.id='f-'+hex();const l=el('label','',' '+label);l.htmlFor=n.id;row.append(n,l);parent.append(row);return n}
let adminState=null, editorId=null;
async function loadAdmin(){try{adminState=await (await api('/api/admin/state','GET',undefined,true)).json();conn(true);appearance(adminState);renderAdmin()}catch(e){conn(false);if(e.status===401||e.status===503){token='';sessionStorage.removeItem('votemera-admin');login(e.message)}else{main.replaceChildren(el('h2','',e.message),btn('Повторить',loadAdmin))}}}
function login(msg=''){main.replaceChildren();const p=el('form','panel login');p.append(el('h2','','Управление голосованием'));if(msg)p.append(el('p','muted',msg));const f=field(p,'Пароль администратора','','password');f.required=true;const b=el('button','','Войти');b.type='submit';p.append(add(el('div','actions'),b));p.onsubmit=e=>{e.preventDefault();token=f.value;sessionStorage.setItem('votemera-admin',token);loadAdmin()};main.append(p)}
async function action(path,method,data,refresh=true){try{const r=await api(path,method,data,true);toast('Сохранено');if(refresh)await loadAdmin();return r}catch(e){toast(e.message);if(e.status===401)login(e.message);return null}}
function downloadAdmin(path,name){return api(path,'GET',undefined,true).then(async r=>{const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}).catch(e=>toast(e.message))}
function maintenancePanel(parent){
 const panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Обновление и резервное копирование'));const status=el('p','muted','Проверка службы…');panel.append(status);let timer=null;
 const labels={unavailable:'Служба обновления не установлена. Запустите установщик один раз.',idle:'Готово к обновлению',queued:'Обновление поставлено в очередь',running:'Устанавливается обновление…',success:'Обновление завершено',failed:'Обновление не удалось. Проверьте журнал службы.'};
 const apply=btn('Установить обновление',async()=>{if(!confirm('Сервер будет кратковременно недоступен. Продолжить?'))return;try{await api('/api/admin/update','POST',{},true);apply.disabled=true;status.textContent='Обновление запущено…';poll()}catch(e){toast(e.message)}},'');apply.disabled=true;
 const refresh=async(check=false)=>{if(!panel.isConnected){clearTimeout(timer);return}try{const d=await(await api('/api/admin/update'+(check?'?check=true':''),'GET',undefined,true)).json();status.textContent=(labels[d.status]||d.status)+' · v'+d.version+(d.error?' · '+d.error:d.available===true?' · Доступна новая версия':d.available===false?' · Версия актуальна':'');apply.disabled=d.available!==true||['queued','running','unavailable'].includes(d.status);if(d.version!==adminState.version){location.reload();return}if(['queued','running'].includes(d.status))poll()}catch(e){status.textContent='Сервер временно недоступен';poll()}};
 function poll(){clearTimeout(timer);timer=setTimeout(()=>refresh(),2000)}
 panel.append(add(el('div','actions'),btn('Проверить обновления',()=>refresh(true)),apply,btn('Скачать резервную копию',()=>downloadAdmin('/api/admin/backup','votemera-backup.db')),btn('Восстановить копию',()=>{const f=el('input');f.type='file';f.accept='.db,.sqlite,.sqlite3';f.onchange=async()=>{const file=f.files[0];if(!file)return;if(file.size>64*1024*1024){toast('Файл превышает 64 МБ');return}if(!confirm('Восстановить базу? Текущие данные будут заменены. Голосование должно быть закрыто.'))return;try{const r=await fetch('/api/admin/restore',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/octet-stream'},body:file});if(!r.ok){const j=await r.json();throw Error(j.detail||'Ошибка восстановления')}toast('Копия восстановлена. Голосование закрыто.');await loadAdmin()}catch(e){toast(e.message)}};f.click()},'danger')));refresh()
}
let adminPoll=null;
function liveAdmin(panel,s){const totals=el('div','panel');totals.append(el('h2','','Терминалы и текущая статистика'));const rows=el('div');totals.append(rows);panel.prepend(totals);clearInterval(adminPoll);let active=false;const tick=async()=>{if(active||!totals.isConnected||!token||document.hidden)return;active=true;try{const [fresh,t]=await Promise.all([api('/api/admin/state','GET',undefined,true).then(r=>r.json()),api('/api/admin/terminals','GET',undefined,true).then(r=>r.json())]);const view=el('div');view.append(el('p','',`Терминалов онлайн: ${t.online}`),el('p','muted',fresh.event.title+' · '+(fresh.opened?'Голосование открыто':'Голосование закрыто')));fresh.event.rounds.forEach(r=>view.append(el('p','',r.name+': '+r.total+' анкет')));if(t.terminals.length)view.append(el('p','muted',t.terminals.map(x=>x.id+' — '+(x.online?'в сети':'не в сети')).join(' · ')));rows.replaceChildren(...view.childNodes);conn(true)}catch{conn(false)}finally{active=false}};tick();adminPoll=setInterval(tick,3000)}
function presentationAdmin(parent,s){const p=s.event.presentation||{order:[],slides:[],seconds:15},panel=el('section','panel');parent.append(panel);panel.append(el('h2','','Конструктор экрана результатов'),el('p','muted','Порядок вопросов задаёт обзор, сценарий переключает режимы автоматически.'));const order=field(panel,'Номера вопросов в порядке показа (через запятую; пусто — все)',p.order.map(i=>i+1).join(', '));const seconds=field(panel,'Время показа слайда, секунд',p.seconds,'number');const list=el('div');panel.append(list);const modes=[['live','Текущий этап'],['compare','Сравнение этапов'],['analysis','Перекрёстный анализ'],['analysis_compare','Перекрёстный анализ этапов']];function slide(d={mode:'live',question:-1}){if(list.children.length>=24)return;const row=el('div','panel');const m=select(row,'Режим',modes,d.mode),q=select(row,'Вопрос',[[-1,'Обзор'],...s.event.questions.map((x,i)=>[i,(i+1)+'. '+x.question])],d.question);row.getData=()=>({mode:m.value,question:+q.value});row.append(add(el('div','actions'),btn('↑',()=>{if(row.previousElementSibling)list.insertBefore(row,row.previousElementSibling)}),btn('↓',()=>{if(row.nextElementSibling)list.insertBefore(row.nextElementSibling,row)}),btn('Удалить',()=>row.remove(),'danger')));list.append(row)}p.slides.forEach(slide);panel.append(add(el('div','actions'),btn('+ Слайд',()=>slide()),btn('Сохранить сценарий',()=>{const values=order.value.trim()?order.value.split(',').map(x=>Number(x.trim())-1):[];action('/api/admin/presentation/'+s.event.id,'PUT',{order:values,seconds:+seconds.value,slides:[...list.children].map(x=>x.getData())})},''),btn('Демонстрация',()=>window.open('/display?demo=1','_blank','noopener')),btn('Создать репетицию',async()=>{if(!confirm('Создать отдельную копию анкеты без голосов?'))return;const r=await action('/api/admin/rehearsal/'+s.event.id,'POST',{},false);if(r)await loadAdmin()})));}
function renderAdmin(){const s=adminState;main.className='admin';main.replaceChildren(add(el('div','row'),el('h1','','Управление'),btn('Обновить',loadAdmin),btn('Выйти',()=>{sessionStorage.removeItem('votemera-admin');token='';clearInterval(adminPoll);login()})));const links=el('div','links');for(const [url,t] of [['/vote','Открыть терминал'],['/display','Открыть экран']]){const a=el('a','',t);a.href=url;a.target='_blank';a.rel='noopener';links.append(a)}main.append(links);const grid=el('div','admin-grid');main.append(grid);const control=el('section','panel');control.append(el('h2','','Управление мероприятием'));grid.append(control);const event=select(control,'Мероприятие',s.events.map(e=>[e.id,e.title]),s.event.id);const round=select(control,'Этап',s.event.rounds.map(r=>[r.id,r.name]),s.round);const disp=select(control,'Экран результатов',[['live','Текущий этап'],['compare','Сравнение этапов'],['analysis','Связь ответов — текущий этап'],['analysis_compare','Связь ответов — все этапы'],['presentation','Сценарий показа'],['hidden','Скрыть результаты']],s.display);const status=el('p','notice',s.opened?'Сейчас голосование открыто':'Сейчас голосование закрыто');control.append(status);let selected=s.event;event.onchange=async()=>{try{selected=await(await api('/api/admin/event/'+event.value,'GET',undefined,true)).json();round.replaceChildren(...selected.rounds.map(r=>{const o=el('option','',r.name);o.value=r.id;return o}))}catch(e){toast(e.message)}};const set=open=>action('/api/admin/control','POST',{event:+event.value,round:+round.value,opened:open,display:disp.value});control.append(add(el('div','actions'),btn('Открыть этап',()=>set(true),''),btn('Закрыть этап',()=>set(false)),btn('Только режим экрана',()=>action('/api/admin/control','POST',{event:s.event.id,round:s.round,opened:s.opened,display:disp.value}))));control.append(el('p','muted','Выбор мероприятия или этапа применяется кнопкой открытия/закрытия.'));const hist=el('div');for(const r of s.event.rounds){hist.append(add(el('div','row'),el('span','',r.name+': '+r.total),btn('Очистить',()=>{if(confirm('Удалить все голоса этапа «'+r.name+'»?'))action('/api/admin/round/'+r.id+'/votes','DELETE')},'danger')))}control.append(hist,add(el('div','actions'),btn('Экспорт CSV',async()=>{try{const r=await api('/api/admin/export/'+event.value,'GET',undefined,true);const url=URL.createObjectURL(await r.blob()),a=el('a');a.href=url;a.download='votemera-'+event.value+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(e.message)}}),btn('Редактировать выбранное',async()=>{try{const e=await(await api('/api/admin/event/'+event.value,'GET',undefined,true)).json();renderEditor(editor,e)}catch(e){toast(e.message)}})));analysisAdmin(main,s);const editor=el('section','panel');main.append(editor);renderEditor(editor);const cfg=el('section','panel');main.append(cfg);cfg.append(el('h2','','Настройки оформления и терминалов'));const c=s.settings;const idle=field(cfg,'Сброс незавершённой анкеты, секунд (0 — выключено)',c.idle_timeout??90,'number');const brand=field(cfg,'Название системы',c.brand),subtitle=field(cfg,'Подзаголовок',c.subtitle),theme=select(cfg,'Тема',[['dark','Тёмная'],['light','Светлая']],c.theme),accent=field(cfg,'Акцентный цвет',c.accent,'color'),cool=field(cfg,'Пауза после голоса, мс (500–10000)',c.cooldown,'number'),thank=field(cfg,'Сообщение после ответа',c.thank_you),chart=select(cfg,'Тип диаграммы',[['stack','Общая шкала'],['bars','Отдельные полосы']],c.chart),count=check(cfg,'Показывать количество ответов',c.show_count),percent=check(cfg,'Показывать проценты',c.show_percent),pageSize=select(cfg,'Вопросов на одном экране',[[1,'1 — крупный план'],[2,'2 — рядом'],[4,'4 — обзор']],c.display_page_size),rotate=field(cfg,'Автопереключение страниц, секунд (0 — выключено)',c.display_rotate,'number'),voteLayout=select(cfg,'Расположение кнопок терминала',[['auto','Автоматически'],['grid','Плитки'],['list','Список']],c.vote_layout),motion=select(cfg,'Анимации интерфейса',[['full','Плавные и выразительные'],['subtle','Минимальные'],['off','Выключены']],c.motion||'full');cfg.append(add(el('div','actions'),btn('Сохранить настройки',()=>action('/api/admin/settings','PUT',{brand:brand.value,subtitle:subtitle.value,theme:theme.value,accent:accent.value,cooldown:+cool.value,thank_you:thank.value,chart:chart.value,show_count:count.checked,show_percent:percent.checked,display_page_size:+pageSize.value,display_rotate:+rotate.value,vote_layout:voteLayout.value,motion:motion.value,idle_timeout:+idle.value}),'')));presentationAdmin(main,s);maintenancePanel(main);liveAdmin(main,s);if(s.event.analysis)main.append(btn('Экспорт перекрёстной аналитики CSV',()=>downloadAdmin('/api/admin/cross-export/'+s.event.id,'votemera-cross.csv')));}
function renderEditor(node,e=null){
 node.replaceChildren(el('h2','',e?'Редактирование мероприятия':'Конструктор анкеты'));const title=field(node,'Название мероприятия',e?.title||''),rounds=field(node,'Этапы, по одному на строку',e?.rounds.map(r=>r.name).join('\n')||'До мероприятия\nПосле мероприятия','textarea');
 const list=el('div','question-editor');const palette=['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4'];
 const templates={'binary':['Да','Нет'],'neutral':['Да','Не уверен','Нет'],'frequency':['Ежедневно','Иногда','Редко','Никогда'],'scale':['1','2','3','4','5']};
 const data=()=>({title:title.value,rounds:rounds.value.split('\n').map(x=>x.trim()).filter(Boolean),questions:[...list.children].map(x=>x.getData())});
 const renumber=()=>[...list.children].forEach((b,i)=>b.querySelector('.question-number').textContent='Вопрос '+(i+1));
 function question(q,after=null){if(list.children.length>=12){toast('Не более 12 вопросов');return}const block=el('section','question-edit');block.append(el('h3','question-number'));
 const input=field(block,'Текст вопроса',q?.question||'','textarea'),desc=field(block,'Пояснение (необязательно)',q?.description||''),confirmation=check(block,'Подтверждать выбор отдельной кнопкой',q?.confirm||false),options=el('div','option-list');
 const conditional=check(block,'Показывать только при определённом предыдущем ответе',!!q?.show_if),source=field(block,'Номер предыдущего вопроса',q?.show_if?q.show_if.question+1:1,'number'),sourceChoice=field(block,'Номер варианта предыдущего вопроса',q?.show_if?q.show_if.choice+1:1,'number');const updateRule=()=>{source.disabled=sourceChoice.disabled=!conditional.checked};conditional.onchange=updateRule;updateRule();
 const preset=select(block,'Шаблон вариантов',[['','Выберите шаблон'],['binary','Да / Нет'],['neutral','Да / Не уверен / Нет'],['frequency','Частота использования'],['scale','Шкала от 1 до 5']], '');
 block.append(el('label','','Варианты ответа'),options);
 function option(o){if(options.children.length>=6){toast('Не более 6 вариантов');return}const row=el('div','option-row'),label=el('input'),color=el('input');label.placeholder='Вариант ответа';label.value=o?.label||'';label.setAttribute('aria-label','Название варианта');color.type='color';color.value=o?.color||palette[options.children.length];color.setAttribute('aria-label','Цвет варианта');row.append(label,color,btn('↑',()=>{if(row.previousElementSibling)options.insertBefore(row,row.previousElementSibling)}),btn('↓',()=>{if(row.nextElementSibling)options.insertBefore(row.nextElementSibling,row)}),btn('×',()=>{if(options.children.length>2)row.remove()}));options.append(row)}
 (q?.options||templates.binary.map((label,i)=>({label,color:palette[i]}))).forEach(option);
 preset.onchange=()=>{if(preset.value&&confirm('Заменить варианты ответа шаблоном?')){options.replaceChildren();templates[preset.value].forEach((label,i)=>option({label,color:palette[i]}))}preset.value=''};
 block.getData=()=>({question:input.value,description:desc.value,confirm:confirmation.checked,...(conditional.checked?{show_if:{question:Number(source.value)-1,choice:Number(sourceChoice.value)-1}}:{}),options:[...options.children].map(r=>({label:r.children[0].value,color:r.children[1].value}))});
 block.append(add(el('div','actions'),btn('+ Вариант',()=>option()),btn('↑ Вопрос',()=>{if(block.previousElementSibling)list.insertBefore(block,block.previousElementSibling);renumber()}),btn('↓ Вопрос',()=>{if(block.nextElementSibling)list.insertBefore(block.nextElementSibling,block);renumber()}),btn('Копировать',()=>question(block.getData(),block)),btn('Удалить вопрос',()=>{if(list.children.length>1)block.remove();renumber()},'danger')));
 if(after)after.after(block);else list.append(block);renumber()}
 const tools=el('div','actions');tools.append(btn('Предпросмотр',()=>{const d=data(),preview=el('dialog','preview-dialog');let idx=0;function show(){const q=d.questions[idx];preview.replaceChildren(el('div','eyebrow',`Вопрос ${idx+1} из ${d.questions.length}`),el('h2','',q.question||'Текст вопроса'),el('p','muted',q.description));if(q.show_if)preview.append(el('p','muted',`Условие: вопрос ${q.show_if.question+1}, вариант ${q.show_if.choice+1}`));const c=el('div','choices choice-grid choices-'+q.options.length);q.options.forEach((o,i)=>{const b=btn(o.label||'Вариант '+(i+1),()=>{},'choice');b.disabled=true;b.style.setProperty('--choice',o.color);c.append(b)});preview.append(c,add(el('div','actions'),btn('←',()=>{idx=Math.max(0,idx-1);show()}),btn('→',()=>{idx=Math.min(d.questions.length-1,idx+1);show()}),btn('Закрыть',()=>preview.close())))}preview.addEventListener('close',()=>preview.remove());document.body.append(preview);show();preview.showModal()}),btn('Экспорт шаблона JSON',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(data(),null,2)],{type:'application/json'})),a=el('a');a.href=url;a.download='votemera-template.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}),btn('Импорт шаблона JSON',()=>{const f=el('input');f.type='file';f.accept='.json,application/json';f.onchange=async()=>{try{const file=f.files[0];if(!file||file.size>100000)throw Error('Файл слишком большой');const d=JSON.parse(await file.text());if(typeof d.title!=='string'||!Array.isArray(d.rounds)||d.rounds.length<1||d.rounds.length>12||!d.rounds.every(x=>typeof x==='string')||!Array.isArray(d.questions)||d.questions.length<1||d.questions.length>12||!d.questions.every(q=>typeof q.question==='string'&&Array.isArray(q.options)&&q.options.length>=2&&q.options.length<=6&&q.options.every(o=>typeof o.label==='string'&&/^#[0-9a-f]{6}$/i.test(o.color))))throw Error('Неверный формат шаблона');if(!confirm('Заменить текущую анкету?'))return;title.value=d.title;rounds.value=d.rounds.join('\n');list.replaceChildren();d.questions.forEach(q=>question(q))}catch(e){toast(e.message)}};f.click()}));node.append(tools,list);
 (e?.questions||[null]).forEach(q=>question(q));node.append(add(el('div','actions editor-save'),btn('+ Вопрос',()=>question()),btn(e?'Сохранить изменения':'Создать',()=>action('/api/admin/event'+(e?'/'+e.id:''),e?'PUT':'POST',data()),''),...(e?[btn('Создать копию мероприятия',()=>{const d=data();d.title+=' — копия';action('/api/admin/event','POST',d)}),btn('Отмена',()=>renderEditor(node))]:[])));
}
initFit();
if(mode==='display'&&new URLSearchParams(location.search).get('demo')==='1')demoDisplay();else if(mode==='vote'||mode==='display')startStream();else if(mode==='admin'){token?loadAdmin():login()}else{main.replaceChildren(el('h1','center','Votemera'),el('p','center muted','Локальная система интерактивного голосования'));const row=el('div','row');for(const [p,t] of [['vote','Терминал'],['display','Экран результатов'],['admin','Администрирование']])row.append(btn(t,()=>location.href='/'+p,''));main.append(row);api('/api/state').then(r=>r.json()).then(s=>{appearance(s);conn(true)}).catch(()=>conn(false))}
