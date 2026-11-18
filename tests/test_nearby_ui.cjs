'use strict';
// Exercise the phone's actual Files event handlers without a native bridge.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const assets=path.join(__dirname,'../android/app/src/main/assets'),html=fs.readFileSync(path.join(assets,'index.html'),'utf8');
const code=fs.readFileSync(path.join(assets,'app.js'),'utf8'),calls=[],nodes=new Map();
function item(textContent=''){return {textContent,children:[],events:{},replaceChildren(...v){this.children=v;},
  querySelectorAll(){return this.children;},querySelector(){return this.children[0];},
  click(){if(!this.disabled)(this.onclick||this.events.click)?.();}};}
function node(id){if(!nodes.has(id)){
  const tag=html.match(new RegExp('<[^>]+id="'+id+'"[^>]*>'))?.[0];assert.ok(tag,'missing '+id);
  nodes.set(id,Object.assign(item(),{hidden:tag.includes(' hidden')}));
}return nodes.get(id);}
const state={device:{}},c=vm.createContext({state,$:node,el:(t,k,txt)=>item(txt),
  setTimeout:()=>1,clearTimeout:()=>{},bind:(id,event,fn)=>{node(id).events[event]=fn;}});
const run=s=>vm.runInContext(s,c),wait=()=>new Promise(r=>setImmediate(r));
run(code.slice(code.indexOf('let nearbyActive='),code.indexOf('function scheduleSync(')));
(async()=>{
  const req={id:'r1',name:'Custard',peer_id:'c',number:'013245',approved:true,direction:'outgoing'};
  let view={open:true,request:null,paired:[],devices:[{peer_id:'c',name:'Custard',address:'192.168.1.2'}]};
  c.api=async(a,v)=>{calls.push([a,v]);return {nearby:view};};
  const callMode=()=>calls[calls.length-1][1].mode;
  assert.equal(node('nearby-verify').hidden,true);
  node('pair-panel').open=true;node('pair-panel').events.toggle();await wait();assert.equal(callMode(),'open');
  let candidate=node('nearby-candidates').querySelector('button');assert.equal(candidate.textContent,'Custard');
  view={...view,request:req};candidate.click();await wait();assert.equal(callMode(),'request');
  assert.equal(calls.at(-1)[1].peer_id,'c');
  assert.doesNotMatch(html,/id="nearby-number"/);
  assert.equal(node('nearby-name').textContent,'Custard');
  await run("nearbyAction('poll')");assert.equal(callMode(),'poll');
  assert.equal(node('nearby-approve').hidden,true);assert.equal(calls.some(x=>x[1]?.mode==='approve'),false);
  assert.match(node('nearby-hint').textContent,/Waiting for Custard/);
  // Only an incoming request presents a confirmation.
  view={...view,request:{...req,direction:'incoming',approved:false}};await run("nearbyAction('poll')");
  assert.equal(node('nearby-approve').hidden,false);assert.equal(node('nearby-approve').disabled,false);
  assert.equal(node('nearby-hint').textContent,'Pair with this device?');
  view={...view,request:{...req,direction:'incoming',approved:true}};node('nearby-approve').click();await wait();
  assert.equal(callMode(),'approve');assert.equal(calls.at(-1)[1].request_id,'r1');
  assert.equal(node('nearby-approve').disabled,true);assert.match(node('nearby-hint').textContent,/Waiting/);
  view={...view,open:false,request:null,paired:['c']};await run("nearbyAction('poll')");await wait();
  assert.equal(callMode(),'close');assert.equal(node('nearby-verify').hidden,true);assert.match(node('nearby-message').textContent,/^Paired/);
  // Closing a panel while discovery is running must ignore the stale reply.
  let finish;c.api=async(a,v)=>{calls.push([a,v]);return v.mode==='open'?new Promise(r=>finish=r):{};};
  node('nearby-start').click();node('nearby-stop').click();
  finish({nearby:{open:true,request:req,devices:[]}});await wait();
  assert.equal(node('nearby-verify').hidden,true);assert.equal(node('nearby-start').hidden,false);
  console.log('Phone nearby pairing: one-tap discovery, receiver-only approval, and late reply checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
