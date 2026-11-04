'use strict';
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; };
const fields=['date','name','from_account','to_account','amount','units','status','category','tags','notes'];
const required=[['date','Date'],['to_account','To'],['from_account','From'],['amount','Amount'],['units','Units']];
const filterDefs=[['from_account','From','accounts'],['to_account','To','accounts'],['units','Units','units'],['status','Status','statuses'],['category','Categories','categories'],['tags','Tags','tags']];
const filters=Object.fromEntries(filterDefs.map(([k])=>[k,new Set()]));
let state=null, page='flows', editing=null, draft=null, formTail=Promise.resolve(true), submitted='', pickerConfig=null, anchorRecord=null, adding=false, formGeneration=0, fileBusy=false;
let editorBase=null, anchorBase=null;
let deletingFile=null;
const queuedFlowFields=new Map(), queuedAnchorFields=new Map();
let syncBusy=false, syncPaused=false, syncTimer=null, syncAgain=false;
const writeActions=new Set(['add_flow','update_flow','copy_flow','delete_flow','save_anchor','save_positions','undo','redo','sync_resolve']);
let graphPairs=[], graphInitialized=false, rangeEdited=false, graphGeneration=0, graphData=null, zoom={lo:0,hi:1};
const pending=new Map(), session=String(Date.now())+'-'+Math.random(); let sequence=0, noticeTimer;
const localNow=()=>{ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`; };
const blank=()=>({date:localNow().slice(0,10),name:'',from_account:'',to_account:'',amount:'',units:'USD',status:'Complete',category:'Uncategorized',tags:[],notes:''});

window.flowResponse=(id,response)=>{ const p=pending.get(id); if(!p){if(response.ok&&response.result?.state)accept(response.result);else if(!response.ok)notice(response.error,true);return;} pending.delete(id); if(response.ok)p.resolve(response.result);else p.reject(new Error(response.error||'The operation could not be completed.')); };
function api(action,args={}) {
  const payload=JSON.stringify({action,args});
  let response;
  if(window.FlowNative) response=new Promise((resolve,reject)=>{const id=session+'-'+String(++sequence);pending.set(id,{resolve,reject});window.FlowNative.request(id,payload);});
  // Development harness only; the Android host always uses the local bridge.
  else response=fetch('/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:payload}).then(r=>r.json()).then(r=>{if(!r.ok)throw new Error(r.error);return r.result;});
  return response.then(result=>{if(writeActions.has(action))scheduleSync(0);return result;});
}
function notice(message,error=false){clearTimeout(noticeTimer);$('notice').textContent=message;$('notice').classList.toggle('error-notice',error);$('notice').hidden=false;noticeTimer=setTimeout(()=>$('notice').hidden=true,error?9000:3500);}
function setError(id,message){$(id).textContent=message;$(id).hidden=!message;}
function plain(record){return Object.fromEntries(fields.map(f=>[f,record[f]??(f==='tags'?[]:'')]).concat(record.flow_id?[['flow_id',record.flow_id]]:[]));}
function accept(result){if(result.state){state=result.state;refreshOpenEditors();render();}return result;}
async function act(action,args={}){try{return accept(await api(action,args));}catch(e){notice(e.message,true);throw e;}}
function bind(id,event,fn){$(id).addEventListener(event,e=>Promise.resolve(fn(e)).catch(()=>{}));}
// Calendar and typing share the same field and the same commit path.
function connectCalendar(id, calendarId=id+'-calendar') {
  const input=$(id), calendar=$(calendarId);
  const seed=()=>{
    const match=input.value.trim().match(/^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?(?:[ T]|$)/);
    calendar.value=match?`${match[1]}-${(match[2]||'1').padStart(2,'0')}-${(match[3]||'1').padStart(2,'0')}`:'';
  };
  calendar.addEventListener('pointerdown',seed);
  calendar.addEventListener('focus',seed);
  calendar.addEventListener('change',()=>{
    if(!calendar.value)return;
    const time=input.value.trim().match(/[ T].*$/)?.[0]||'';
    input.value=calendar.value+time;
    input.dispatchEvent(new Event('input',{bubbles:true}));
    input.dispatchEvent(new Event('change',{bubbles:true}));
  });
}
function bindDateInput(id, commit, report=message=>notice(message,true)) {
  const input=$(id);
  connectCalendar(id);
  bind(id,'change',async()=>{
    const value=input.value,dataset=state?.dataset;
    try{
      const result=value.trim()?await api('normalize_input',{field:'date',value}):{value:''};
      if(input.value!==value||state?.dataset!==dataset)return;
      input.value=result.value;
      await commit();
    }catch(error){report(error.message);}
  });
  bind(id,'keydown',event=>{if(event.key==='Enter'){event.preventDefault();input.blur();}});
}
const displayDate=value=>value.endsWith(' 00:00:00')?value.slice(0,10):value;

function hint(data){const missing=required.filter(([k])=>!String(data[k]??'').trim()).map(([,n])=>n);return missing.length===0?'':(missing.length===1?missing[0]:missing.length===2?missing.join(' and '):missing.slice(0,-1).join(', ')+', and '+missing.at(-1))+' required';}

function render(){
  $('dataset').textContent=(state.dataset_name||state.dataset||'No file open').replace(/\.sqlite3$/,'');
  $('dataset').title=state.file_location||'On this phone';
  $('storage-warning').hidden=!state.storage_warning;
  $('storage-message').textContent=state.storage_warning||'';
  $('retry-save').hidden=!state.file_pending;
  $('undo').disabled=!state.undo;$('undo').title=state.undo||'Undo';$('redo').disabled=!state.redo;$('redo').title=state.redo||'Redo';
  $('add-open').disabled=!state.dataset;
  document.querySelectorAll('[data-page]').forEach(n=>n.disabled=!state.dataset&&n.dataset.page!=='files');
  if(!state.dataset)navigate('files');
  for(const [key,,vocab] of filterDefs)for(const selected of filters[key])if(!state.vocabulary[vocab].includes(selected))filters[key].delete(selected);
  for(const [id,values] of [['account-options',state.vocabulary.accounts],['unit-options',state.vocabulary.units],['category-options',[...new Set(['Uncategorized',...state.vocabulary.categories])]],['tag-options',state.vocabulary.tags],['names',state.vocabulary.names]]){
    $(id).replaceChildren(...values.map(value=>{const n=el('option');n.value=value;return n;}));
  }
  renderFlows();renderAccounts();renderFiles();
  if(page==='graphs')loadGraph();
  experimental.changed();
}
function matchingFlows(){const q=$('search').value.trim().toLocaleLowerCase();return state.flows.filter(f=>{
  if(q&&!fields.map(k=>Array.isArray(f[k])?f[k].join(' '):f[k]).join(' ').toLocaleLowerCase().includes(q))return false;
  return filterDefs.every(([k])=>!filters[k].size||(k==='tags'?f.tags.some(t=>filters[k].has(t)):filters[k].has(f[k])));
});}
function renderFlows(){
  $('filters').replaceChildren(...filterDefs.map(([k,label,vocab])=>{const n=el('button','chip'+(filters[k].size?' selected':''),`${label}: ${filters[k].size||'All'}`);n.title=[...filters[k]].join(', ')||'All';n.onclick=()=>openPicker(label,state.vocabulary[vocab],filters[k],()=>renderFlows(),true);return n;}));
  const flows=matchingFlows();$('count').textContent=`${flows.length} of ${state.flows.length}`;
  const nodes=[];let day='';
  for(const f of flows){
    if(f.date.slice(0,10)!==day){day=f.date.slice(0,10);nodes.push(el('div','day-heading',formatDate(day)));}
    const row=el('button','flow-row');row.dataset.id=f.flow_id;row.setAttribute('aria-label',`${f.name}, ${f.amount} ${f.units}, ${f.from_account} to ${f.to_account}`);
    const top=el('div','flow-top'),qty=el('span','flow-quantity',f.amount_display);qty.append(el('span','flow-unit',f.units));top.append(el('span','flow-name',f.name),qty);
    const route=el('div','flow-route');route.append(document.createTextNode(f.from_account),el('span','arrow','→'),document.createTextNode(f.to_account));
    const meta=el('div','flow-meta');meta.append(el('span','status '+f.status,f.status),el('span','',f.category));
    if(f.date.length>10)meta.append(el('span','',f.date.slice(11)));
    if(f.tags.length)meta.append(el('span','',f.tags.map(t=>'#'+t).join(' ')));
    if(f.notes)meta.append(el('span','','≡'));
    row.append(top,route,meta);row.onclick=()=>openEditor(f);nodes.push(row);
  }
  if(!flows.length){const n=el('div','empty');n.append(el('strong','',state.flows.length?'No matching flows':'No flows yet'),document.createTextNode(state.flows.length?'Change the search or reset the filters.':'Add a transfer, or open a file from Files.'));nodes.push(n);}
  $('flow-list').replaceChildren(...nodes);
}
function formatDate(d){const parts=d.split('-').map(Number),date=new Date(parts[0],parts[1]-1,parts[2]);return Number.isNaN(+date)?d:date.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});}
function renderAccounts(){
  if(document.activeElement!==$('as-of'))$('as-of').value=displayDate(state.as_of);
  $('basis').value=state.basis;
  $('account-list').replaceChildren(...state.accounts.map(a=>{
    const row=el('button','account-row');const b=el('span','balance',a.balance_display);b.append(el('small','',a.units));
    row.append(el('strong','',a.name),el('small','',`Confirmed ${a.confirmed_display}`),b);row.onclick=()=>openAnchor(a);return row;
  }));
  if(!state.accounts.length)$('account-list').append(el('div','empty','Accounts appear when a flow references them.'));
}
function renderFiles(){
  renderSync();
  $('file-name').textContent=state.dataset_name||state.dataset||'No file open';
  $('file-location').textContent=state.dataset?(state.file_location||'On this phone - Flow files'):'Open, create, or join a file.';
  const files=state.files||state.datasets.map(name=>({key:name,name,location:'On this phone'}));
  files.sort((a,b)=>a.name.localeCompare(b.name)||a.location.localeCompare(b.location));
  $('dataset-list').replaceChildren(...files.map(file=>{
    const active=file.key===state.dataset,row=el('div','dataset-row'),b=el('button','dataset-item'+(active?' active':'')),label=el('span','file-label');
    label.append(el('span','',file.name),el('small','muted',file.location));b.append(label,el('span','',active?'Open':'>'));
    b.disabled=fileBusy;b.onclick=()=>switchDataset(file.key.startsWith('document:')?'open_document':'open_dataset',file.key.startsWith('document:')?{key:file.key}:{name:file.key}).catch(()=>{});row.append(b);
    if(!file.key.startsWith('document:')){
      const remove=el('button','text-button danger delete-dataset','Delete');
      remove.setAttribute('aria-label','Delete '+file.name);remove.disabled=fileBusy;
      remove.onclick=()=>{deletingFile=file;setError('delete-file-error','');$('delete-file-message').textContent=`Delete ${file.name} from this phone? Other devices are unchanged. This cannot be undone.`;$('delete-file-confirm').showModal();};
      row.append(remove);
    }
    return row;
  }));
  if(!files.length)$('dataset-list').append(el('p','small muted','No saved files on this phone.'));
  fileControls();
}
async function switchDataset(action,args={}){return fileAction(action,args);}
function resetView(){editorBase=null;anchorBase=null;queuedFlowFields.clear();queuedAnchorFields.clear();experimental.reset();formGeneration++;draft=null;editing=null;submitted='';$('search').value='';Object.values(filters).forEach(s=>s.clear());graphPairs=[];graphInitialized=false;rangeEdited=false;zoom={lo:0,hi:1};}
function navigate(next){if(state&&!state.dataset)next='files';page=next;document.querySelectorAll('.page').forEach(n=>n.classList.toggle('active',n.id===page));document.querySelectorAll('[data-page]').forEach(n=>n.classList.toggle('active',n.dataset.page===page));window.scrollTo(0,0);if(page==='graphs')loadGraph();experimental.setActive(page==='experimental');}
document.querySelectorAll('[data-page]').forEach(n=>n.onclick=()=>navigate(n.dataset.page));

function formData(){const d=Object.fromEntries(fields.map(f=>[f,$('f-'+f).value]));d.tags=d.tags.split(',').map(s=>s.trim()).filter(Boolean);if(editing)d.flow_id=editing;return d;}
function updateHint(){const data=formData(),defaults=blank();const changed=fields.some(f=>JSON.stringify(data[f])!==JSON.stringify(defaults[f]));$('clear-flow').disabled=adding||(!editing&&!changed);const missing=hint(data);$('required-hint').textContent=missing||(editing?'Edits save automatically.':'Ready to add');$('add-flow').disabled=adding||!!editing||!!missing;$('existing-actions').hidden=!editing;}
function fillForm(data){for(const f of fields)$('f-'+f).value=f==='tags'?data.tags.join(', '):data[f]??'';$('f-calendar').value=data.date.slice(0,10);updateHint();}
function openEditor(record=null){if(!state?.dataset)return;formGeneration++;editing=record?.flow_id||null;editorBase=record?plain(record):null;queuedFlowFields.clear();const data=record?plain(record):(draft||blank());fillForm(data);submitted=editing?JSON.stringify(data):'';$('editor-title').textContent=editing?'Edit Flow':'Add Flow';setError('form-error','');$('editor').showModal();$('editor').querySelector('.sheet-body').scrollTop=0;}
function stash(){if(!editing)draft=formData();updateHint();}
function commitField(field=null){
  if(!editing){stash();
    if(!['date','amount'].includes(field)||!$('f-'+field).value.trim())return Promise.resolve(true);
    const value=$('f-'+field).value,generation=formGeneration;
    return api('normalize_input',{field,value}).then(result=>{
      if(!editing&&generation===formGeneration&&$('f-'+field).value===value){$('f-'+field).value=result.value;stash();setError('form-error','');}return true;
    }).catch(e=>{if(generation===formGeneration)setError('form-error',e.message);return false;});
  }
  const data=formData(),recordId=editing,generation=formGeneration;
  const changed=(field?[field]:fields).filter(f=>JSON.stringify(data[f])!==JSON.stringify(queuedFlowFields.has(f)?queuedFlowFields.get(f):editorBase?.[f]));
  if(!changed.length)return formTail;
  const missing=hint(data);if(missing){setError('form-error',missing);return Promise.resolve(false);}
  const values=Object.fromEntries(changed.map(f=>[f,data[f]]));
  changed.forEach(f=>queuedFlowFields.set(f,data[f]));
  formTail=formTail.then(async()=>{
    if(generation!==formGeneration)return false;
    const baseline=Object.fromEntries(changed.map(f=>[f,editorBase[f]]));
    try{
      const result=await api('update_flow',{flow:{flow_id:recordId,...values},baseline});
      if(generation===formGeneration){
        for(const f of changed){
          editorBase[f]=result.record[f];
          if(JSON.stringify(queuedFlowFields.get(f))===JSON.stringify(values[f]))queuedFlowFields.delete(f);
          if(JSON.stringify(formData()[f])===JSON.stringify(values[f]))$('f-'+f).value=f==='tags'?result.record[f].join(', '):result.record[f];
        }
        setError('form-error','');
      }
      accept(result);return true;
    }catch(e){
      if(generation===formGeneration){
        for(const f of changed)if(JSON.stringify(queuedFlowFields.get(f))===JSON.stringify(values[f]))queuedFlowFields.delete(f);
        setError('form-error',e.message);
      }
      return false;
    }
  });
  return formTail;
}
for(const f of fields){
  $('f-'+f).addEventListener('input',stash);
  $('f-'+f).addEventListener('change',()=>commitField(f));
  $('f-'+f).addEventListener('keydown',e=>{if(e.key==='Enter'&&f!=='notes'){e.preventDefault();commitField(f);$('f-'+fields[Math.min(fields.indexOf(f)+1,fields.length-1)]).focus();}});
}
async function closeEditor(){if(await commitField())$('editor').close();}
bind('editor-close','click',closeEditor);
$('editor').addEventListener('cancel',event=>{event.preventDefault();closeEditor();});
connectCalendar('f-date','f-calendar');
bind('clear-flow','click',async()=>{await formTail;formGeneration++;editing=null;editorBase=null;queuedFlowFields.clear();draft=blank();submitted='';fillForm(draft);$('editor-title').textContent='Add Flow';setError('form-error','');});
bind('add-open','click',()=>openEditor());
bind('add-flow','click',async()=>{if(adding)return;const data=formData();adding=true;updateHint();try{accept(await api('add_flow',{flow:data}));formGeneration++;draft=null;fillForm(blank());$('editor').close();notice('Flow added.');}catch(e){setError('form-error',e.message);}finally{adding=false;updateHint();}});
bind('copy-flow','click',async()=>{if(!await commitField())return;const r=await act('copy_flow',{flow_id:editing});editing=r.record.flow_id;editorBase=plain(r.record);queuedFlowFields.clear();submitted=JSON.stringify(plain(r.record));fillForm(r.record);notice('Copy created. Edits apply to this copy.');});
bind('delete-flow','click',async()=>{await formTail;await act('delete_flow',{flow_id:editing});editing=null;submitted='';$('editor').close();notice('Flow deleted. Undo is available.');});

function openPicker(title,values,selection,onChange,allowAll=false){pickerConfig={title,values,selection,onChange,allowAll};$('picker-title').textContent=title;$('picker-search').value='';renderPicker();$('picker').showModal();}
function renderPicker(){const c=pickerConfig,q=$('picker-search').value.toLocaleLowerCase();const nodes=[];
  if(c.allowAll&&!q){const all=el('label','picker-choice');const input=el('input');input.type='checkbox';input.checked=!c.selection.size;input.onchange=()=>{c.selection.clear();c.onChange();renderPicker();};all.append(input,document.createTextNode('All'));nodes.push(all);}
  c.values.filter(v=>v.toLocaleLowerCase().includes(q)).forEach(v=>{const row=el('label','picker-choice'),input=el('input');input.type='checkbox';input.checked=c.selection.has(v);input.onchange=()=>{input.checked?c.selection.add(v):c.selection.delete(v);c.onChange();if(c.allowAll)renderPicker();};row.append(input,document.createTextNode(v));nodes.push(row);});
  if(!nodes.length)nodes.push(el('p','muted','No matching choices.'));$('picker-options').replaceChildren(...nodes);
}
bind('picker-search','input',renderPicker);bind('picker-done','click',()=>$('picker').close());
bind('search','input',renderFlows);bind('reset','click',()=>{$('search').value='';Object.values(filters).forEach(s=>s.clear());renderFlows();});
for(const action of ['undo','redo'])bind(action,'click',async()=>{await formTail;await act(action);});
bindDateInput('as-of',()=>act('view',{as_of:$('as-of').value}));bind('basis','change',()=>act('view',{basis:$('basis').value}));
function openAnchor(a){anchorRecord=a;anchorBase={opening_at:a.opening_at,opening_amount:a.opening_amount??''};queuedAnchorFields.clear();$('anchor-title').textContent=`${a.name} / ${a.units}`;$('anchor-date').value=displayDate(a.opening_at);$('anchor-amount').value=a.opening_amount??'';setError('anchor-error','');$('anchor').showModal();}
let anchorTail=Promise.resolve();
function saveAnchor(){
  const a=anchorRecord;if(!a)return Promise.resolve(true);
  const input={opening_at:$('anchor-date').value,opening_amount:$('anchor-amount').value};
  const changed=Object.keys(input).filter(f=>input[f]!==String(queuedAnchorFields.has(f)?queuedAnchorFields.get(f):anchorBase[f])&&!(f==='opening_at'&&!queuedAnchorFields.has(f)&&input[f]===displayDate(anchorBase[f])));
  if(!changed.length)return anchorTail;
  const values=Object.fromEntries(changed.map(f=>[f,input[f]]));
  changed.forEach(f=>queuedAnchorFields.set(f,input[f]));
  anchorTail=anchorTail.then(async()=>{
    const baseline=Object.fromEntries(changed.map(f=>[f,anchorBase[f]]));
    try{
      const result=await api('save_anchor',{name:a.name,units:a.units,...values,baseline});
      const current=result.state.accounts.find(c=>c.name===a.name&&c.units===a.units);
      if(anchorRecord===a&&current){for(const f of changed){anchorBase[f]=current[f]??'';if(queuedAnchorFields.get(f)===values[f])queuedAnchorFields.delete(f);const input=$(f==='opening_at'?'anchor-date':'anchor-amount');if(input.value===values[f])input.value=f==='opening_at'?displayDate(anchorBase[f]):anchorBase[f];}setError('anchor-error','');}
      accept(result);return true;
    }catch(e){if(anchorRecord===a){for(const f of changed)if(queuedAnchorFields.get(f)===values[f])queuedAnchorFields.delete(f);setError('anchor-error',e.message);}return false;}
  });return anchorTail;
}
function refreshOpenEditors(){
  if(editing&&editorBase&&$('editor').open){
    const record=state.flows.find(f=>f.flow_id===editing);
    if(!record){setError('form-error','This flow was deleted on another device.');return;}
    const current=formData();
    for(const f of fields){
      if(queuedFlowFields.has(f)||document.activeElement===$('f-'+f))continue;
      if(JSON.stringify(current[f])===JSON.stringify(editorBase[f])){
        $('f-'+f).value=f==='tags'?record[f].join(', '):record[f];editorBase[f]=record[f];
      }
    }
    updateHint();
  }
  if(anchorRecord&&anchorBase&&$('anchor').open){
    const account=state.accounts.find(a=>a.name===anchorRecord.name&&a.units===anchorRecord.units);
    if(!account){setError('anchor-error','This account is no longer referenced.');return;}
    for(const [field,id] of [['opening_at','anchor-date'],['opening_amount','anchor-amount']]){
      if(queuedAnchorFields.has(field)||document.activeElement===$(id))continue;
      if($(id).value===String(anchorBase[field])||field==='opening_at'&&$(id).value===displayDate(anchorBase[field])){
        anchorBase[field]=account[field]??'';$(id).value=field==='opening_at'?displayDate(account[field]):anchorBase[field];
      }
    }
  }
}
async function closeAnchor(){if(await saveAnchor())$('anchor').close();}
bindDateInput('anchor-date',saveAnchor,message=>setError('anchor-error',message));bind('anchor-amount','change',saveAnchor);bind('anchor-done','click',closeAnchor);
$('anchor').addEventListener('cancel',event=>{event.preventDefault();closeAnchor();});
bind('anchor-amount','focus',()=>{const n=$('anchor-amount');n.setSelectionRange(n.value.length,n.value.length);});
bind('new-dataset','click',async()=>{await switchDataset('new_dataset',{name:$('new-dataset-name').value});$('new-dataset-name').value='';});
bind('camp','click',async()=>{await switchDataset('camp');navigate('flows');});
function fileControls(){for(const id of ['open-file','new-dataset','camp','sync-join','sync-reconnect','delete-file-yes','delete-file-no'])$(id).disabled=fileBusy;$('save-as').disabled=fileBusy||!state?.dataset;document.querySelectorAll('.dataset-item,.delete-dataset').forEach(n=>n.disabled=fileBusy);}
async function fileAction(action,args={}){
  if(fileBusy)return {canceled:true};fileBusy=true;fileControls();
  try{await formTail;await anchorTail;const r=await api(action,args);if(r.state){if(action!=='delete_dataset'||r.state.dataset!==state?.dataset)resetView();accept(r);notice(action==='delete_dataset'?'File deleted from this phone.':`Opened ${state.dataset_name||state.dataset}`);}return r;}
  catch(e){notice(e.message,true);throw e;}finally{fileBusy=false;fileControls();}
}
bind('delete-file-no','click',()=>{$('delete-file-confirm').close();deletingFile=null;});
bind('delete-file-yes','click',async()=>{if(!deletingFile)return;try{await fileAction('delete_dataset',{name:deletingFile.key});$('delete-file-confirm').close();deletingFile=null;}catch(error){setError('delete-file-error',error.message);}});
bind('open-file','click',()=>fileAction('pick_open'));
bind('save-as','click',()=>fileAction('save_as'));
bind('retry-save','click',async()=>{await act('retry_save');notice('Saved to the selected file.');});

bind('graph-select','click',()=>{
  const choices=new Map(state.accounts.map(a=>[`${a.name} / ${a.units}`,[a.name,a.units]]));
  const selected=new Set(graphPairs.map(p=>`${p[0]} / ${p[1]}`));
  openPicker('Accounts & Units',[...choices.keys()],selected,()=>{graphInitialized=true;graphPairs=[...selected].map(k=>choices.get(k));zoom={lo:0,hi:1};loadGraph();});
});
bind('graph-basis','change',()=>{zoom={lo:0,hi:1};return loadGraph();});
for(const id of ['graph-start','graph-end'])bindDateInput(id,()=>{rangeEdited=true;zoom={lo:0,hi:1};return loadGraph();},message=>$('charts').replaceChildren(el('p','error',message)));
bind('graph-reset','click',()=>{zoom={lo:0,hi:1};rangeEdited=false;return loadGraph();});
async function loadGraph(){
  if(!state?.dataset)return;const gen=++graphGeneration;
  if(!graphInitialized){const a=state.accounts.find(a=>a.opening_amount!==null);graphPairs=a?[[a.name,a.units]]:[];graphInitialized=true;}
  graphPairs=graphPairs.filter(p=>state.accounts.some(a=>a.name===p[0]&&a.units===p[1]));
  $('graph-select').textContent=`Accounts & Units${graphPairs.length?' · '+graphPairs.length:''}`;
  const args={pairs:graphPairs,basis:$('graph-basis').value};
  if(rangeEdited){if($('graph-start').value)args.start=$('graph-start').value;if($('graph-end').value)args.end=$('graph-end').value;}
  try{const data=await api('graph',args);if(gen!==graphGeneration)return;graphData=data;if(!rangeEdited){$('graph-start').value=data.start.slice(0,10);$('graph-end').value=data.end.slice(0,10);}drawGraphs();}
  catch(e){if(gen===graphGeneration)$('charts').replaceChildren(el('p','error',e.message));}
}
const SVG='http://www.w3.org/2000/svg',colors=['#27705c','#9a6842','#6577b3','#ae667e','#8c953a','#408c9e'];
function svgNode(tag,attrs,text){const n=document.createElementNS(SVG,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;}
const timeOf=s=>Date.parse(s.replace(' ','T'));
function drawGraphs(){
  if(!graphData?.series.length){$('charts').replaceChildren(el('div','empty','Choose accounts and units to graph their balances.'));return;}
  const groups=new Map();graphData.series.forEach(s=>{if(!groups.has(s.units))groups.set(s.units,[]);groups.get(s.units).push(s);});
  const cards=[];for(const [unit,series]of groups){
    const card=el('div','chart');card.append(el('h2','',unit));const svg=svgNode('svg',{viewBox:'0 0 360 270',role:'img','aria-label':`Balances in ${unit}`});
    const legend=el('div','legend');series.forEach((s,i)=>{const n=el('span'),dot=el('i');dot.style.background=colors[i%colors.length];n.append(dot,document.createTextNode(s.name));legend.append(n);});
    const readout=el('div','chart-readout','Touch the graph for exact balances.');
    card.append(svg,legend,readout);cards.push(card);
    const fullLo=timeOf(graphData.start),fullHi=timeOf(graphData.end),fullSpan=Math.max(1,fullHi-fullLo);
    const lo=fullLo+zoom.lo*fullSpan,hi=fullLo+zoom.hi*fullSpan;
    const all=series.flatMap(s=>s.points.map(p=>Number(p[1])));let ylo=Math.min(...all),yhi=Math.max(...all);
    if(!Number.isFinite(ylo)||!Number.isFinite(yhi)){readout.textContent='These quantities exceed the display range.';continue;}
    const pad=(yhi-ylo||Math.abs(yhi)||1)*.1;ylo-=pad;yhi+=pad;
    const x=t=>fullLo===fullHi?202:60+((t-lo)/Math.max(1,hi-lo))*284,y=v=>226-(v-ylo)/(yhi-ylo)*204;
    const clipId='clip-'+cards.length;const defs=svgNode('defs',{}),clip=svgNode('clipPath',{id:clipId});clip.append(svgNode('rect',{x:60,y:16,width:284,height:215}));defs.append(clip);svg.append(defs);
    for(let i=0;i<5;i++){const v=ylo+(yhi-ylo)*i/4,yp=y(v);svg.append(svgNode('line',{x1:60,x2:344,y1:yp,y2:yp,stroke:'#e8ece5'}),svgNode('text',{x:53,y:yp+4,'text-anchor':'end',fill:'#68776f','font-size':10},new Intl.NumberFormat(undefined,{maximumSignificantDigits:4,notation:Math.abs(v)>99999?'compact':'standard'}).format(v)));}
    series.forEach((s,i)=>{let d='';s.points.forEach((p,j)=>{const xp=x(timeOf(p[0])),yp=y(Number(p[1]));d+=j?`H${xp}V${yp}`:`M${xp},${yp}`;});svg.append(svgNode('path',{d,fill:'none',stroke:colors[i%colors.length],'stroke-width':2.5,'clip-path':`url(#${clipId})`}));});
    if(fullLo===fullHi)series.forEach((s,i)=>svg.append(svgNode('circle',{cx:202,cy:y(Number(s.points.at(-1)[1])),r:4,fill:colors[i%colors.length]})));
    for(const [t,anchor]of [[lo,'start'],[hi,'end']])svg.append(svgNode('text',{x:anchor==='start'?60:344,y:251,'text-anchor':anchor,fill:'#68776f','font-size':10},new Date(t).toLocaleDateString(undefined,{month:'short',day:'numeric'})));
    const cursor=svgNode('line',{x1:0,x2:0,y1:18,y2:228,stroke:'#9daaa1','stroke-dasharray':'3 3',visibility:'hidden'});svg.append(cursor);
    const points=new Map();let gesture=null;
    function inspect(clientX){const rect=svg.getBoundingClientRect(),vx=(clientX-rect.left)/rect.width*360;const t=lo+Math.max(0,Math.min(1,(vx-60)/284))*(hi-lo);cursor.setAttribute('x1',x(t));cursor.setAttribute('x2',x(t));cursor.setAttribute('visibility','visible');const nodes=[el('strong','',new Date(t).toLocaleString()),el('br')];series.forEach((s,i)=>{let chosen=s.points[0];for(const p of s.points){if(timeOf(p[0])<=t)chosen=p;else break;}if(i)nodes.push(el('br'));nodes.push(document.createTextNode(`${s.name}: ${chosen[2]} ${unit}`));});readout.replaceChildren(...nodes);}
    svg.onpointerdown=e=>{svg.setPointerCapture(e.pointerId);points.set(e.pointerId,e.clientX);inspect(e.clientX);gesture={initial:new Map(points),zoom:{...zoom}};};
    svg.onpointermove=e=>{if(!points.has(e.pointerId)){if(e.pointerType==='mouse')inspect(e.clientX);return;}points.set(e.pointerId,e.clientX);inspect(e.clientX);};
    svg.onpointerup=e=>{if(!gesture)return;const initial=[...gesture.initial.values()],current=[...points.values()],width=svg.getBoundingClientRect().width;let span=gesture.zoom.hi-gesture.zoom.lo,center=(gesture.zoom.hi+gesture.zoom.lo)/2;
      if(initial.length===2&&current.length===2){const scale=Math.abs(current[1]-current[0])/Math.max(10,Math.abs(initial[1]-initial[0]));span=Math.min(1,Math.max(.02,span/scale));}
      else if(initial.length===1&&current.length===1){const dx=current[0]-initial[0];if(Math.abs(dx)<12){points.delete(e.pointerId);gesture=null;return;}center-=dx/width*span;}
      const low=Math.max(0,Math.min(1-span,center-span/2));zoom={lo:low,hi:low+span};points.clear();gesture=null;drawGraphs();
    };
    svg.onpointercancel=()=>{points.clear();gesture=null;};
    svg.onwheel=e=>{e.preventDefault();const old=zoom.hi-zoom.lo,span=Math.min(1,Math.max(.02,old*(e.deltaY>0?1.2:1/1.2))),center=(zoom.hi+zoom.lo)/2;zoom.lo=Math.max(0,Math.min(1-span,center-span/2));zoom.hi=zoom.lo+span;drawGraphs();};
  }$('charts').replaceChildren(...cards);
}

function renderSync(){
  const sync=state.sync||{};
  $('sync-reconnect').hidden=!sync.enabled;
  $('sync-state').textContent=!sync.enabled?'Not connected':sync.error?`Offline · ${sync.pending||0} pending`:sync.more?'Catching up':sync.conflicts?.length?'Needs review':sync.pending?`${sync.pending} pending`:sync.connected?'Up to date':'Waiting to sync';
  $('sync-state').title=sync.error||sync.endpoint||'';
  $('sync-help').textContent=sync.error||(sync.enabled?'Edits save here first and synchronize while Flow is open.':'Use the connection text from the coordinator. A matching local file connects in place; a new name creates a local copy.');
  $('sync-conflicts').replaceChildren(...(sync.conflicts||[]).map(conflict=>{
    const row=el('div','sync-conflict');
    row.append(el('p','',conflict.message||'An edit overlaps a change from another device.'));
    const labels={date:'Date',from_key:'From',to_key:'To',amount:'Amount',units:'Units',status:'Status',name:'Name',notes:'Notes',category_key:'Category',opening_amount:'Starting balance',opening_at:'Starting date',x:'Horizontal position',y:'Vertical position',position:'Tag order'};
    const valueText=value=>value===null||value===undefined?'—':String(value);
    for(const change of conflict.changes||[]){
      const flow=change.table==='flows'?state.flows.find(f=>f.flow_id===change.key[0]):null;
      const title=flow?flow.name:change.table==='flows'?'Flow':change.table==='flow_tags'?`Tag: ${change.key[1]}`:change.key.join(' / ');
      row.append(el('strong','small',title));
      if(change.after===null)row.append(el('p','small','This device: deleted'));
      if(change.remote===null)row.append(el('p','small','Received: deleted'));
      const details=el('dl','sync-values');
      for(const field of Object.keys(change.after||change.before||{})){
        if(!labels[field])continue;
        details.append(el('dt','',labels[field]),el('dd','',`This device: ${change.after===null?'deleted':valueText(change.after[field])}`),el('dd','',`Received: ${valueText(change.remote?.[field])}`));
      }
      row.append(details);
    }
    for(const [choice,label] of [['local','Keep this device’s edit'],['remote','Use received change']]){
      const b=el('button','outline',label);b.onclick=async()=>{try{await act('sync_resolve',{operation_id:conflict.operation_id||conflict.id,choice});scheduleSync(0);}catch(_){}};row.append(b);
    }return row;
  }));
}
function scheduleSync(delay=2000){clearTimeout(syncTimer);if(!syncPaused&&!document.hidden)syncTimer=setTimeout(pollSync,delay);}
async function pollSync(force=false){
  if(syncBusy){syncAgain=true;return;}
  if(fileBusy||(!force&&(syncPaused||document.hidden))){scheduleSync();return;}
  if(!state?.sync?.enabled){scheduleSync();return;}
  syncBusy=true;
  try{const result=await api('sync');if(result.state){if(result.changed)accept(result);else{state.sync=result.state.sync;renderSync();}}}
  catch(error){if(state?.sync){state.sync.error=error.message;renderSync();}}
  finally{syncBusy=false;if(syncAgain||state?.sync?.more){syncAgain=false;setTimeout(()=>pollSync(force||syncPaused),0);}else scheduleSync();}
}
bind('sync-join','click',async()=>{await fileAction('join_sync',{name:$('sync-name').value,connection:$('sync-connection').value});$('sync-connection').value='';$('sync-name').value='';scheduleSync(0);});
bind('sync-reconnect','click',async()=>{const result=await api('reconnect_sync',{connection:$('sync-connection').value});accept(result);$('sync-connection').value='';notice('Connection updated.');scheduleSync(0);});
window.flowResume=()=>{syncPaused=false;scheduleSync(0);};
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(syncTimer);}else{syncPaused=false;scheduleSync(0);}});

window.flowBack=()=>{for(const id of ['delete-file-confirm','map-detail','picker','anchor','editor'])if($(id).open){if(id==='editor')closeEditor();else if(id==='anchor')closeAnchor();else $(id).close();return true;}if(page!==(state?.dataset?'flows':'files')){navigate('flows');return true;}return false;};
window.flowPause=()=>{syncPaused=true;clearTimeout(syncTimer);experimental.pause();const saves=[];if($('editor').open)saves.push(commitField());if($('anchor').open)saves.push(saveAnchor());Promise.all(saves).then(()=>pollSync(true)).catch(()=>{});};
window.addEventListener('resize',()=>{if(page==='graphs'&&graphData)drawGraphs();});
const experimental=new window.FlowExperimental({api,getState:()=>state,getFlows:matchingFlows,accept,notice,dateControl:bindDateInput});
async function init(){try{accept(await api('init'));accept(await api('view',{as_of:localNow()}));scheduleSync(0);}catch(e){$('dataset').textContent='Could not open';notice(e.message,true);}}
init();
