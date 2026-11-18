'use strict';
// Run the actual WebView form code. The fixture supplies inputs and captures
// serialized native bridge calls; all baseline/refresh/submit logic is app.js.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const nodes=new Map(),calls=[];
function node(id){
  if(!nodes.has(id))nodes.set(id,{id,value:'',open:false,dataset:{},addEventListener(){},showModal(){this.open=true;},querySelector(){return {};},setAttribute(){}});
  return nodes.get(id);
}
const document={getElementById:node,querySelectorAll(){return [];},addEventListener(){},activeElement:null};
const window={addEventListener(){},FlowExperimental:class{},FlowNative:{request(id,payload){
  const message=JSON.parse(payload);calls.push(message);
  if(message.action!=='init')queueMicrotask(()=>window.flowResponse(id,{ok:false,error:'Change refused'}));
}}};
const context=vm.createContext({window,document,setTimeout,clearTimeout,console});
const run=s=>vm.runInContext(s,context);
run(fs.readFileSync(path.join(__dirname,'../android/app/src/main/assets/app.js'),'utf8'));
(async()=>{
  run(`state={dataset:'water',accounts:[],flows:[{...blank(),flow_id:'id',from_account:'A',to_account:'B',amount:'2',units:'kg',name:'Water',edit_revision:{dataset:'shared',row:'flow id',__row__:'',amount:'original',units:'original'}}]};
       openEditor(state.flows[0]);`);
  node('f-amount').value='3';document.activeElement=node('f-amount');
  run(`state.flows[0]={...state.flows[0],units:'liters',notes:'received',edit_revision:{...state.flows[0].edit_revision,amount:'remote',units:'remote',notes:'remote'}};
       refreshOpenEditors();`);
  assert.equal(node('f-amount').value,'3');assert.equal(node('f-units').value,'kg');
  assert.equal(node('f-notes').value,'received');
  await run("commitField('amount')");
  const flow=calls.at(-1);assert.equal(flow.action,'update_flow');assert.equal(flow.args.revision.amount,'original');
  assert.equal(flow.args.revision.notes,'remote');assert.deepEqual(flow.args.flow,{flow_id:'id',amount:'3'});
  run(`state.accounts=[{name:'A',units:'kg',opening_at:'2026-01-01 00:00:00',opening_amount:'10',
                     edit_revision:{dataset:'shared',row:'A kg',__row__:'',opening_amount:'old'}}];openAnchor(state.accounts[0]);`);
  node('anchor-amount').value='15';document.activeElement=node('anchor-amount');
  run(`state.accounts[0]={...state.accounts[0],opening_at:'2026-01-03 00:00:00',edit_revision:{...state.accounts[0].edit_revision,opening_amount:'new',opening_at:'new'}};
       refreshOpenEditors();`);
  await run('saveAnchor()');
  const anchor=calls.at(-1);assert.equal(anchor.action,'save_anchor');
  assert.equal(anchor.args.revision.opening_amount,'old');assert.equal(anchor.args.revision.opening_at,'new');
  assert.equal(anchor.args.opening_amount,'15');assert.deepEqual(anchor.args.baseline,{opening_amount:'10'});
  console.log('Phone form refresh, retained quantity/unit and submitted field revisions passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
