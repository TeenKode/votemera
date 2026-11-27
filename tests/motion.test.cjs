// Deterministic tests of the same counter code used in the browser.
const {test}=require('node:test'), assert=require('node:assert/strict');
const vm=require('node:vm'),fs=require('node:fs');
function env(){
 let now=0,id=0;const frames=new Map(),body={dataset:{motion:'full'}},pref={matches:false,addEventListener(){}};
 const doc={body,hidden:false,getElementById(){return {}},addEventListener(){}};
 const ctx=vm.createContext({document:doc,location:{pathname:'/display'},matchMedia:()=>pref,performance:{now:()=>now},requestAnimationFrame:fn=>{frames.set(++id,fn);return id},cancelAnimationFrame:i=>frames.delete(i)});
 vm.runInContext(fs.readFileSync('static/app.js','utf8').split('let answers=[]')[0],ctx);
 return {body,pref,frames,doc,run:s=>vm.runInContext(s,ctx),step:t=>{now=t;const batch=[...frames.values()];frames.clear();batch.forEach(fn=>fn(now))}};
}
test('retarget from visible value and reach exact result',()=>{
 const e=env();e.run("var n={isConnected:true,textContent:''};numeric(n,100,countText)");e.step(200);
 const shown=e.run('n._shown');assert.ok(shown>0&&shown<100);
 e.run('numeric(n,200,countText)');assert.equal(e.run('n._shown'),shown);assert.equal(e.frames.size,1);
 e.step(720);assert.equal(e.run('n.textContent'),'200');assert.equal(e.frames.size,0);
 e.run('numeric(n,0,countText)');e.step(1240);assert.equal(e.run('n.textContent'),'0');
});
test('reduced motion and disabled mode update immediately',()=>{
 const e=env();e.body.dataset.motion='off';e.run("var n={isConnected:true,textContent:''};numeric(n,23,countText)");assert.equal(e.run('n.textContent'),'23');assert.equal(e.frames.size,0);
 e.body.dataset.motion='full';e.pref.matches=true;e.run('numeric(n,81,countText)');assert.equal(e.run('n.textContent'),'81');assert.equal(e.frames.size,0);
});
test('subtle duration, cancellation on disconnect and stopping counters',()=>{
 const e=env();e.body.dataset.motion='subtle';e.run("var n={isConnected:true,textContent:''};numeric(n,90,countText)");e.step(160);assert.equal(e.run('n.textContent'),'90');
 e.run('numeric(n,40,countText);n.isConnected=false');e.step(180);assert.equal(e.frames.size,0);
 e.run('n.isConnected=true;numeric(n,70,countText);stopCounters()');assert.equal(e.run('n.textContent'),'70');assert.equal(e.frames.size,0);
});
