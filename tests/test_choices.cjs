'use strict';
// DOM event regression test; development-only dependency: linkedom.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {parseHTML} = require('linkedom');
const assets = path.join(__dirname, '../android/app/src/main/assets');
const {document, Event, HTMLElement} = parseHTML(fs.readFileSync(path.join(assets, 'index.html'), 'utf8'));
HTMLElement.prototype.scrollIntoView = function () {};
const vocabulary = {accounts: ['Battery', 'Laptop'], names: ['Charge'], units: ['Wh'],
  categories: ['Power'], tags: ['camp', 'work']};
const state = {dataset: 'flow.sqlite3', vocabulary};
const context = vm.createContext({document, Event, state,
  $: id => document.getElementById(id),
  el: (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
});
const app = fs.readFileSync(path.join(assets, 'app.js'), 'utf8');
vm.runInContext(app.slice(app.indexOf('const flowChoices='), app.indexOf('\nfunction hint(')), context);
const refresh = () => vm.runInContext('flowChoices.refresh()', context);
const close = () => vm.runInContext('flowChoices.close()', context);
const input = name => document.getElementById('f-' + name);
const list = name => document.getElementById(input(name).getAttribute('aria-controls'));
const choices = name => Array.from(list(name).children, n => n.textContent);
const event = (node, type, values = {}) => {
  const e = new Event(type, {bubbles: true, cancelable: true});
  Object.assign(e, values);
  node.dispatchEvent(e);
  return e;
};
function type(name, value) { input(name).value = value; event(input(name), 'input'); }
refresh();

// The same current account vocabulary appears for both endpoints on focus.
for (const name of ['from_account', 'to_account']) {
  event(input(name), 'focus');
  assert.equal(input(name).getAttribute('role'), 'combobox');
  assert.equal(list(name).hidden, false);
  assert.deepEqual(choices(name), ['Battery', 'Laptop']);
}
assert.equal(list('from_account').hidden, true, 'Only one list stays open');
type('to_account', 'bAt');
assert.deepEqual(choices('to_account'), ['Battery']);
const selected = list('to_account').firstChild;
const changes = [];
input('to_account').addEventListener('change', () => changes.push(input('to_account').value));
assert.equal(event(selected, 'pointerdown').defaultPrevented, true);
event(selected, 'click');
assert.equal(input('to_account').value, 'Battery');
assert.deepEqual(changes, ['Battery'], 'Selection commits the final value once');
assert.equal(list('to_account').hidden, true);

// New typed names remain usable; Enter does not select an arbitrary suggestion.
type('from_account', 'New panel');
assert.equal(list('from_account').hidden, true);
assert.equal(event(input('from_account'), 'keydown', {key: 'Enter'}).defaultPrevented, false);
assert.equal(input('from_account').value, 'New panel');
assert.equal(close(), false, 'Back does not consume a hidden, empty menu');
type('from_account', 'bat');
assert.equal(event(input('from_account'), 'keydown', {key: 'Enter'}).defaultPrevented, false);
event(input('from_account'), 'keydown', {key: 'ArrowDown'});
assert.equal(event(input('from_account'), 'keydown', {key: 'Enter'}).defaultPrevented, true);
assert.equal(input('from_account').value, 'Battery');

// Synced/edit/undo state refreshes replace stale choices without altering input.
event(input('from_account'), 'focus');
const stableRow = list('from_account').firstChild;
refresh();
assert.equal(list('from_account').firstChild, stableRow, 'Unchanged refresh preserves a touched row');
type('from_account', 'New');
vocabulary.accounts = ['Battery', 'Laptop', 'New panel'];
refresh();
assert.deepEqual(choices('from_account'), ['New panel']);
assert.equal(input('from_account').value, 'New');
vocabulary.accounts = ['Battery', 'Laptop'];
refresh();
assert.deepEqual(choices('from_account'), []);
assert.equal(input('from_account').value, 'New');

type('tags', 'camp, wo');
assert.deepEqual(choices('tags'), ['work']);
event(list('tags').firstChild, 'click');
assert.equal(input('tags').value, 'camp, work');
for (const [name, values] of [['name', ['Charge']], ['units', ['Wh']], ['category', ['Uncategorized', 'Power']]]) {
  event(input(name), 'focus');
  assert.deepEqual(choices(name), values);
}
event(input('units'), 'focus');
assert.equal(event(input('units'), 'keydown', {key: 'Escape'}).defaultPrevented, true);
assert.equal(list('units').hidden, true);
type('units', 'Other');
assert.equal(event(input('units'), 'keydown', {key: 'Escape'}).defaultPrevented, false);
event(input('units'), 'focus');
event(input('units'), 'keydown', {key: 'ArrowDown'});
assert.equal(event(input('units'), 'keydown', {key: 'Enter', isComposing: true}).defaultPrevented, false);
state.dataset = 'empty.sqlite3';
state.vocabulary = {accounts: [], names: [], units: [], categories: [], tags: []};
refresh();
assert.equal(list('units').hidden, true);
event(input('from_account'), 'focus');
assert.deepEqual(choices('from_account'), []);
console.log('Choice event checks passed: focus, filtering, selection, free entry, refresh, tags, keyboard and file switching.');
