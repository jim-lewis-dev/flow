'use strict';
// Exercise the actual Files controls and dataset IDs, including two equal display names.
const assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const {parseHTML}=require('linkedom');
const root=path.join(__dirname,'../android/app/src/main/assets');
const {document}=parseHTML(fs.readFileSync(path.join(root,'index.html'),'utf8'));
const code=fs.readFileSync(path.join(root,'app.js'),'utf8');
const state={dataset:'money.sqlite3',flows:[],peer_file:{},device:{enabled:true,running:true,peers:[],files:[]}};
const calls=[];
const c=vm.createContext({document,state,fileBusy:false,
  $:id=>document.getElementById(id),el:(tag,cls,txt)=>{const e=document.createElement(tag);e.className=cls;if(txt)e.textContent=txt;return e;},
  bind:(id,event,fn)=>document.getElementById(id).addEventListener(event,fn),
  notice:()=>{},accept:()=>{},fileControls:()=>{},switchDataset:async(a,v)=>calls.push([a,v]),fileAction:async(a,v)=>{calls.push([a,v]);return {};},
  api:async(a,v)=>{calls.push([a,v]);return {};}});
const run=s=>vm.runInContext(s,c), node=id=>document.getElementById(id);
run(code.slice(code.indexOf('let peerCatalog='),code.indexOf('function scheduleSync(')));
run(code.slice(code.indexOf('function renderFiles(){'),code.indexOf('async function switchDataset(')));
const wait=()=>new Promise(r=>setImmediate(r));
(async()=>{
  run('renderPeerFile()');
  assert.equal(node('peer-share').disabled,false);
  state.dataset_name='money.sqlite3';state.files=[
    {name:'money.sqlite3',key:'money.sqlite3',location:'Local - on this phone'},
    {name:'money.sqlite3',key:'shared:001122/money.sqlite3',location:'Shared copy - 001122'}];
  run('renderFiles()');
  const listed=node('dataset-list').querySelectorAll('.dataset-item');
  assert.equal(listed.length,2);assert.match(listed[0].textContent,/Local/);assert.match(listed[1].textContent,/Shared copy/);
  listed[1].click();await wait();assert.equal(calls.at(-1)[1].name,'shared:001122/money.sqlite3');
  listed[0].click();await wait();assert.equal(calls.at(-1)[1].name,'money.sqlite3');
  state.device.files=[{dataset:'joined-id',key:'mine.sqlite3',name:'money'}];
  run("peerCatalog=[{dataset:'joined-id',name:'money',peer_id:'a',peer_name:'Desk'},{dataset:'different-id',name:'money',peer_id:'b',peer_name:'Phone'}];renderPeerCatalog()");
  assert.equal(node('peer-catalog-list').querySelectorAll('.peer-join').length,1);
  assert.match(node('peer-catalog-list').textContent,/Already shared on this phone/);
  assert.equal(node('peer-catalog-list').querySelector('input'),null);
  node('peer-catalog-list').querySelector('button').click();await wait();
  assert.equal(calls.at(-1)[0],'peer_join');
  assert.equal(calls.at(-1)[1].dataset,'different-id');assert.equal(calls.at(-1)[1].name,'money');
  state.peer_file={dataset:'shared',conflicts:[{id:'conflict',title:'Amount and units',choices:[
    {id:'c1',label:'10 USD',value:{amount:'10',units:'USD'}},{id:'c2',label:'15 EUR',value:{amount:'15',units:'EUR'}}]}]};
  run('renderPeerFile()');const buttons=node('peer-conflicts').querySelectorAll('button');
  assert.deepEqual(Array.from(buttons,b=>b.textContent),['10 USD','15 EUR']);
  buttons[1].click();await wait();assert.equal(calls.at(-1)[0],'peer_resolve');
  assert.equal(calls.at(-1)[1].choice,'c2');
  state.dataset='document:source';state.dataset_name='money.sqlite3';run('renderPeerFile()');
  assert.equal(node('peer-copy').hidden,false);assert.equal(node('peer-copy-name'),null);
  console.log('Phone sharing controls passed: same-name catalog IDs, no renaming, actual conflict choices, and document copy.');
})().catch(e=>{console.error(e);process.exitCode=1;});
