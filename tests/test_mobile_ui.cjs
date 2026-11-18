'use strict';
// Run the real form and graph handlers against the shipped markup.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const assets = path.join(__dirname, '../android/app/src/main/assets');
const {document, Event} = parseHTML(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'));
// linkedom lacks the writable select.value property supplied by WebView.
for (const select of document.querySelectorAll('select')) {
  let value = select.querySelector('option[selected],option')?.textContent || '';
  Object.defineProperty(select, 'value', {get: () => value, set: next => {value = next;}});
}
const source = fs.readFileSync(path.join(assets, 'app.js'), 'utf8');
const calls = [];
const data = {start: '2026-10-01 00:00:00', end: '2026-10-02 00:00:00', series: [
  {name: 'Battery', units: 'Wh', points: [['2026-10-01 00:00:00', '100', '100']]},
  {name: 'Laptop', units: 'Wh', points: [['2026-10-01 00:00:00', '20', '20']]}
]};
const context = vm.createContext({document, Event, console,
  flowChoices: {close() {}}, experimental: {reset() {}}, bindDateInput() {},
  api: async (action, args) => {
    calls.push({action, args});
    return {...data, series: args.combined ? [...data.series,
      {name: 'Combined: Battery + Laptop', units: 'Wh',
        points: [['2026-10-01 00:00:00', '120', '120']]}] : data.series};
  }
});
const run = code => vm.runInContext(code, context);
run(source.slice(0, source.indexOf('\nwindow.flowResponse=')));
run(source.match(/function bind\(id,event,fn\).*\n/)[0]);
run(source.slice(source.indexOf('function hint('), source.indexOf('\nfunction render(')));
run(source.slice(source.indexOf('function formData('), source.indexOf('\nfunction openEditor(')));
run(source.match(/function resetView\(\).*\n/)[0]);
run(source.slice(source.indexOf("bind('graph-basis'"), source.indexOf('\nfunction renderSync(')));
const node = id => document.getElementById(id);
const tick = () => new Promise(resolve => setImmediate(resolve));

async function main() {
  run('fillForm(blank())');
  assert.equal(node('flow-buttons').hidden, false);
  assert.equal(node('add-flow').disabled, true);
  assert.equal(node('clear-flow').disabled, true);
  run("editing='existing';fillForm({...blank(),from_account:'Battery',to_account:'Laptop',amount:'10',units:'Wh'})");
  assert.equal(node('flow-buttons').hidden, true);
  assert.equal(node('required-hint').textContent, 'Edits save automatically.');
  assert.equal(node('existing-actions').hidden, false);
  run("editing=null;fillForm({...blank(),from_account:'Battery',to_account:'Laptop',amount:'10',units:'Wh'})");
  assert.equal(node('flow-buttons').hidden, false);
  assert.equal(node('add-flow').disabled, false);
  assert.equal(node('clear-flow').disabled, false);
  assert.equal(node('existing-actions').hidden, true);

  run("state={dataset:'camp.sqlite3',accounts:[{name:'Battery',units:'Wh',opening_amount:'100'},{name:'Laptop',units:'Wh',opening_amount:'20'}]};graphPairs=[['Battery','Wh'],['Laptop','Wh']];graphInitialized=true");
  node('graph-combined').checked = false;
  await run('loadGraph()');
  assert.equal(calls.at(-1).args.combined, false);
  assert.equal(document.querySelectorAll('.chart svg path').length, 2);
  node('graph-combined').checked = true;
  node('graph-combined').dispatchEvent(new Event('change'));
  await tick();
  assert.equal(calls.at(-1).args.combined, true);
  assert.equal(document.querySelectorAll('.chart svg path').length, 3);
  assert.deepEqual(Array.from(document.querySelectorAll('.legend span'), n => n.textContent),
    ['Battery', 'Laptop', 'Combined: Battery + Laptop']);
  node('graph-reset').dispatchEvent(new Event('click'));
  await tick();
  assert.equal(calls.at(-1).args.combined, true, 'Fit preserves the checkbox');
  node('graph-combined').checked = false;
  node('graph-combined').dispatchEvent(new Event('change'));
  await tick();
  assert.equal(document.querySelectorAll('.chart svg path').length, 2);
  node('graph-combined').checked = true;
  run('resetView()');
  assert.equal(node('graph-combined').checked, false, 'A new file starts unchecked');
  console.log('Edit footer and additive graph controls passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
