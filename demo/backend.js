'use strict';
// A static, deliberately non-secure sandbox. No requests are made to a real Votemera server.
const Demo=(()=>{
 const STORE='votemera-pages-demo-v1';
 const VERSION='__DEMO_VERSION__';
 const defaults={brand:'Votemera · ДЕМО',subtitle:'Локальная демонстрация',theme:'dark',accent:'#43c8bd',cooldown:900,show_percent:true,show_count:true,chart:'stack',thank_you:'Спасибо за ваш голос!',display_page_size:4,display_rotate:12,vote_layout:'auto',motion:'full',idle_timeout:90};
 const opts=(labels)=>labels.map((label,i)=>({label,color:['#0f9f78','#df5363','#397dd8','#bd8c22','#895bd4','#168da4'][i]}));
 const uid=()=>crypto.randomUUID().replaceAll('-','');
 function initial(){
  const questions=[{question:'Используете ли вы ИИ в работе?',options:opts(['Да','Иногда','Нет'])},{question:'Помогает ли ИИ экономить время?',options:opts(['Да','Не уверен','Нет'])},{question:'Готовы ли вы применять новые инструменты?',options:opts(['Да','Нет'])}];
  const events=[{id:1,title:'Демонстрационный педагогический совет',created:Date.now()/1000,questions,rounds:[{id:1,name:'До педсовета'},{id:2,name:'После педсовета'}],analysis:{first:0,second:1,chart:'stack'},presentation:{order:[0,1,2],slides:[],seconds:15}}];
  const ballots=[];for(let r=1;r<=2;r++)for(let i=0;i<(r===1?32:24);i++){let a=(i+r)%3;ballots.push({round:r,choices:[a,(i%4===0?1:a),i%5===0?1:0],nonce:'demo-'+r+'-'+i,terminal:'sample',created:0})}
  return {settings:{...defaults},events,ballots,active:1,round:2,opened:true,display:'live',epoch:uid(),nextEvent:2,nextRound:3,terminals:{}};
 }
 function load(){try{const d=JSON.parse(localStorage.getItem(STORE)||'null');if(d&&Array.isArray(d.events)&&d.events.length)return d}catch{}const d=initial();save(d);return d}
 function save(d){localStorage.setItem(STORE,JSON.stringify(d))}
 function reset(){localStorage.removeItem(STORE);location.reload()}
 function url(view,extra=''){return './?view='+encodeURIComponent(view)+extra}
 function event(d,id=d.active){const e=d.events.find(x=>x.id===Number(id));if(!e)throw [404,'Мероприятие не найдено'];return e}
 function detail(e){return {...e,question:e.questions[0].question,options:e.questions[0].options}}
 function visible(q,choices){return !q.show_if||choices[q.show_if.question]===q.show_if.choice}
 function results(d,e,r){
  const rows=d.ballots.filter(b=>b.round===r.id),results=e.questions.map((q,i)=>{const counts=q.options.map((_,j)=>rows.filter(b=>b.choices[i]===j).length);return {counts,total:counts.reduce((a,b)=>a+b,0)}});
  const item={...r,total:rows.length,counts:results[0].counts,results};
  if(e.analysis){const {first,second}=e.analysis,matrix=e.questions[first].options.map((_,i)=>e.questions[second].options.map((_,j)=>rows.filter(b=>b.choices[first]===i&&b.choices[second]===j).length));const totals=matrix.map(r=>r.reduce((a,b)=>a+b,0)),total=totals.reduce((a,b)=>a+b,0);item.cross={matrix,totals,total,unpaired:rows.length-total}}
  return item;
 }
 function snapshot(d,priv=false,vote=false){
  const e=event(d),rounds=e.rounds.map(r=>{const include=priv||(!vote&&d.display!=='hidden'&&(['compare','analysis_compare','presentation'].includes(d.display)||r.id===d.round));return include?results(d,e,r):{...r}});
  const item={...detail(e),rounds};
  if(vote){delete item.analysis;delete item.presentation}else if(!priv&&!['analysis','analysis_compare','presentation'].includes(d.display))delete item.analysis;
  const s={settings:{...d.settings,...(priv?{}:{...(e.meta?.brand?{brand:e.meta.brand}:{}),...(e.meta?.accent?{accent:e.meta.accent}:{})})},event:item,round:d.round,opened:d.opened&&(!d.deadline||d.deadline>Date.now()/1000),deadline:d.deadline||null,logo:e.meta?.logo||'',epoch:d.epoch,display:d.display,version:VERSION,terminal_key_required:false};
  if(priv)s.events=d.events.map(e=>({id:e.id,title:e.title,question:e.questions[0].question,created:e.created,round_count:e.rounds.length,archived:e.meta?.archived||false,template:e.meta?.template||false,total:d.ballots.filter(b=>e.rounds.some(r=>r.id===b.round)).length})).reverse();return s;
 }
 const json=(v,status=200)=>new Response(JSON.stringify(v),{status,headers:{'Content-Type':'application/json'}});
 const ok=()=>json({ok:true});
 const error=(code,msg)=>{throw [code,msg]};
 function validate(v){if(!v?.title?.trim()||!v.rounds?.length||!v.questions?.length||v.questions.length>12||v.rounds.length>12)error(422,'Заполните название, этапы и вопросы');for(const [i,q] of v.questions.entries()){if(!q.question.trim()||q.options.length<2||q.options.length>6||q.options.some(o=>!o.label.trim()))error(422,'Заполните текст вопросов и варианты');if(q.show_if&&(q.show_if.question>=i||q.show_if.question<0||q.show_if.choice>=v.questions[q.show_if.question].options.length||q.show_if.choice<0))error(422,'Неверное условие показа')} }
 function newEvent(d,v){validate(v);const e={id:d.nextEvent++,title:v.title.trim(),questions:v.questions,created:Date.now()/1000,rounds:v.rounds.map(name=>({id:d.nextRound++,name})),analysis:v.questions.length>1?{first:0,second:1,chart:'stack'}:null,presentation:{order:v.questions.map((_,i)=>i),slides:[],seconds:15}};d.events.push(e);return e}
 function csv(d,e,cross){const rows=cross?[['Этап','Группа','Ответ','Количество']]:[['Мероприятие','Вопрос','Этап','Вариант','Голоса','Доля (%)']];for(const r of e.rounds){const rs=results(d,e,r);if(cross){if(e.analysis)rs.cross.matrix.forEach((row,i)=>row.forEach((n,j)=>rows.push([r.name,e.questions[e.analysis.first].options[i].label,e.questions[e.analysis.second].options[j].label,n])))}else e.questions.forEach((q,i)=>q.options.forEach((o,j)=>rows.push([e.title,q.question,r.name,o.label,rs.results[i].counts[j],rs.results[i].total?(100*rs.results[i].counts[j]/rs.results[i].total).toFixed(1):0])))}const escape=v=>{let s=String(v);if(/^[=+\-@\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"'};return new Response('\ufeff'+rows.map(r=>r.map(escape).join(';')).join('\n'),{headers:{'Content-Type':'text/csv;charset=utf-8'}})}
 function dispatch(path,method,v){
  const d=load(),u=new URL(path,'https://demo.invalid'),p=u.pathname;let m,r;
  if(p==='/api/state')return json(snapshot(d,false,u.searchParams.get('view')==='vote'));
  if(p==='/api/admin/state')return json(snapshot(d,true));
  if(p==='/api/admin/terminals') {const now=Date.now(),terminals=Object.entries(d.terminals).filter(([,t])=>now-t<300000).map(([id,t])=>({id:id.slice(-8),seconds_ago:Math.floor((now-t)/1000),online:now-t<25000,key_name:'Демо'}));return json({online:terminals.filter(t=>t.online).length,terminals})}
  if(p==='/api/terminal/heartbeat'){d.terminals[v.terminal]=Date.now();save(d);return ok()}
  if(p==='/api/ballot'){const e=event(d);if(d.ballots.some(b=>b.nonce===v.nonce))return json({ok:true,duplicate:true});if(!d.opened||(d.deadline&&d.deadline<=Date.now()/1000)||d.round!==v.round||d.epoch!==v.epoch)error(409,'Этап изменился');if(v.choices.length!==e.questions.length||e.questions.some((q,i)=>visible(q,v.choices)?!Number.isInteger(v.choices[i])||v.choices[i]<0||v.choices[i]>=q.options.length:v.choices[i]!==null))error(422,'Неверные ответы');d.ballots.push({...v,created:Date.now()});save(d);return ok()}
  if(p==='/api/admin/live'){v={event:d.active,round:v.round??d.round,opened:v.opened??(d.opened&&(!d.deadline||d.deadline>Date.now()/1000)),display:v.display??d.display,...v};return dispatch('/api/admin/control','POST',v)}
  if(p==='/api/admin/control'){const e=event(d,v.event);if(e.meta?.archived)error(409,'Мероприятие в архиве');if(!e.rounds.some(r=>r.id===v.round))error(422,'Неверный этап');if(d.active!==v.event||d.round!==v.round||d.opened!==v.opened||(d.deadline&&d.deadline<=Date.now()/1000&&v.opened)){d.epoch=uid();d.deadline=null}if(!v.opened||v.duration!==undefined)d.deadline=v.opened&&v.duration?Date.now()/1000+v.duration:null;Object.assign(d,{active:v.event,round:v.round,opened:v.opened,display:v.display});save(d);return json(snapshot(d,true))}
  if(p==='/api/admin/settings'){d.settings={...defaults,...v};save(d);return ok()}
  if(p==='/api/admin/event'&&method==='POST'){const e=newEvent(d,v);save(d);return json({id:e.id,round:e.rounds[0].id})}
  if((m=p.match(/^\/api\/admin\/event\/(\d+)\/meta$/))){const e=event(d,m[1]);if(v.archived&&e.id===d.active)error(409,'Сначала выберите другое мероприятие');e.meta={...v};save(d);return ok()}
  if((m=p.match(/^\/api\/admin\/event\/(\d+)$/))){const e=event(d,m[1]);if(method==='GET')return json(detail(e));if(method==='DELETE'){if(e.id===d.active)error(409,'Сначала выберите другое мероприятие');if(d.ballots.some(b=>e.rounds.some(r=>r.id===b.round))&&u.searchParams.get('confirm_results')!=='true')error(409,'Подтвердите удаление результатов');d.ballots=d.ballots.filter(b=>!e.rounds.some(r=>r.id===b.round));d.events=d.events.filter(x=>x.id!==e.id);save(d);return ok()}if(method==='PUT'){validate(v);if((d.active===e.id&&d.opened)||d.ballots.some(b=>e.rounds.some(r=>r.id===b.round)))error(409,'Сначала закройте голосование и очистите результаты');e.title=v.title;e.questions=v.questions;e.rounds=v.rounds.map(name=>({id:d.nextRound++,name}));e.analysis=v.questions.length>1?{first:0,second:1,chart:'stack'}:null;e.presentation={order:v.questions.map((_,i)=>i),slides:[],seconds:15};if(d.active===e.id){d.round=e.rounds[0].id;d.epoch=uid()}save(d);return json({id:e.id,round:e.rounds[0].id})}}
  if((m=p.match(/^\/api\/admin\/(?:event\/(\d+)\/copy|rehearsal\/(\d+))$/))){const old=event(d,m[1]||m[2]),e=newEvent(d,{title:(m[1]?'Копия: ':'[Репетиция] ')+old.title,questions:structuredClone(old.questions),rounds:old.rounds.map(r=>r.name)});e.analysis=structuredClone(old.analysis);e.presentation=structuredClone(old.presentation);if(old.meta)e.meta={...structuredClone(old.meta),template:false,archived:false};save(d);return json({id:e.id,round:e.rounds[0].id})}
  if((m=p.match(/^\/api\/admin\/(analysis|presentation)\/(\d+)$/))){const e=event(d,m[2]);if(m[1]==='analysis'&&(v.first===v.second||!e.questions[v.first]||!e.questions[v.second]))error(422,'Выберите два разных вопроса');if(m[1]==='presentation'&&(v.order.some(i=>!e.questions[i])||new Set(v.order).size!==v.order.length))error(422,'Неверный порядок вопросов');e[m[1]]=v;save(d);return ok()}
  if((m=p.match(/^\/api\/admin\/round\/(\d+)\/votes$/))){const id=+m[1];if(d.opened&&d.round===id)error(409,'Сначала закройте этап');d.ballots=d.ballots.filter(b=>b.round!==id);if(d.round===id)d.epoch=uid();save(d);return ok()}
  if((m=p.match(/^\/api\/admin\/(export|cross-export)\/(\d+)$/)))return csv(d,event(d,m[2]),m[1]==='cross-export');
  return json({detail:'В демонстрации эта серверная функция недоступна'},503);
 }
 async function fetch(path,options={}){try{const method=options.method||'GET',data=options.body?JSON.parse(options.body):undefined;const run=()=>dispatch(path,method,data);return method!=='GET'&&navigator.locks?await navigator.locks.request(STORE,run):run()}catch(e){return json({detail:Array.isArray(e)?e[1]:'Ошибка локальной демонстрации'},Array.isArray(e)?e[0]:500)}}
 class Stream{
  constructor(path){let last='';const emit=()=>{try{const s=JSON.stringify(snapshot(load(),false,path.includes('view=vote')));if(s!==last){last=s;this.onmessage?.({data:s})}}catch{this.onerror?.()}};this.timer=setInterval(emit,400);setTimeout(()=>{this.onopen?.();emit()},0);window.addEventListener('storage',emit);this.close=()=>{clearInterval(this.timer);window.removeEventListener('storage',emit)}}
 }
 return {fetch,Stream,url,reset};
})();
