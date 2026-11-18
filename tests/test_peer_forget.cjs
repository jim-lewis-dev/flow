'use strict';
// Use the actual native response handler, Forget callback and device renderer.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const assets=path.join(__dirname,'../android/app/src/main/assets'),html=fs.readFileSync(path.join(assets,'index.html'),'utf8');
const code=fs.readFileSync(path.join(assets,'app.js'),'utf8'),calls=[],nodes=new Map();
function item(textContent=''){return {textContent,children:[],append(...v){this.children.push(...v);},replaceChildren(...v){this.children=v;},
  querySelectorAll(){return this.children;},querySelector(){return this.children[0];},click(){if(!this.disabled)this.onclick?.();}};}
function node(id){if(!nodes.has(id)){assert.ok(html.includes('id="'+id+'"'),'missing '+id);nodes.set(id,item());}return nodes.get(id);}
const state={dataset:'flow',device:{enabled:true,running:true,files:[],peers:[{peer_id:'c',name:'Custard',address:'192.168.0.70'}],error:'Custard: connection refused'}};
const window={FlowNative:{request(id,payload){calls.push({id,...JSON.parse(payload)});}}};
const c=vm.createContext({state,window,pending:new Map(),session:'t',sequence:0,writeActions:new Set(),fileBusy:false,
 document:{querySelectorAll:()=>[]},$:node,el:(t,k,txt)=>item(txt),setTimeout:()=>1,clearTimeout:()=>{},
 bind:()=>{},notice:()=>{},scheduleSync:()=>{},accept:()=>{}});
const run=s=>vm.runInContext(s,c),wait=()=>new Promise(r=>setImmediate(r));
run(code.slice(code.indexOf('window.flowResponse='),code.indexOf('function notice(')));
run(code.slice(code.indexOf('let peerCatalog='),code.indexOf('function scheduleSync(')));
run('renderPeerDevice()');
(async()=>{
 const stale=run("api('peer_poll')");
 const forget=node('peer-devices').children[0].children[2];
 assert.equal(forget.textContent,'Forget');
 forget.click();await wait();
 assert.equal(calls[1].action,'peer_forget'); // A single click sends the operation.
 assert.equal(calls[1].args.peer_id,'c');
 const fresh={enabled:true,running:true,files:[],peers:[],error:''};
 window.flowResponse(calls[1].id,{ok:true,result:{device:fresh}});await wait();
 assert.equal(node('peer-devices').children.length,0);assert.equal(node('peer-devices-label').hidden,true);
 assert.doesNotMatch(node('peer-status').textContent,/Custard|refused/);
 window.flowResponse(calls[0].id,{ok:true,result:{device:{peers:[{peer_id:'c',name:'Custard'}],error:'Custard: connection refused'}}});
 const old=await stale;
 assert.equal(old.device,fresh); // A delayed poll cannot undo the confirmed Forget.
 c.result=old;run('applyDevice(result)');
 assert.equal(node('peer-devices').children.length,0);
 console.log('Single-tap Forget and rejection of stale device status passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
