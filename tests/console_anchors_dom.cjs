// Le script d'en-tête de la console est joué tel quel : une ancre partie vers son espace y mène, celle d'un ancien
// onglet ouvre la vue qui a repris son contenu, les autres restent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.includes('ANCHORS'));
assert.equal(scripts.length, 1, 'one head script translates the anchors');
function visit(hash, search = '') {
  const location = {hash, search, to: null, replace(url) { this.to = url; }};
  const listeners = {};
  const context = {location, URLSearchParams, addEventListener: (kind, fn) => { listeners[kind] = fn; }};
  vm.runInNewContext(scripts[0], context);
  return {location, listeners, anchor: context.anchor};
}
assert.equal(visit('#simulation').location.to, '/simulation');
assert.equal(visit('#sentinel').location.to, '/regulation#marche');
assert.equal(visit('', '?embed=simulation').location.to, '/simulation?embed=simulation');
for (const stays of ['', '#krys', '#alertes', '#summary', '#details', '#mails-chat', '#constructor', '#__proto__']) {
  assert.equal(visit(stays).location.to, null, stays);
}
assert.equal(visit('', '?embed=parc').location.to, null);
// an anchor changed once the console is open follows the same way
const page = visit('#agents');
page.location.hash = '#simulation';
page.listeners.hashchange();
assert.equal(page.location.to, '/simulation');
// every former tab opens the view that took its content, scrolled to its block
const {anchor} = visit('');
const moved = {summary: 'alertes', actions: 'alertes', agents: 'parc', guardrails: 'parc', improve: 'parc',
  metrics: 'gouvernance', impact: 'gouvernance', details: 'gouvernance'};
for (const [block, view] of Object.entries(moved)) assert.deepEqual({...anchor(block)}, {view, block}, block);
for (const view of ['krys', 'alertes', 'synthese', 'parc', 'gouvernance', 'mails-chat', 'inconnue']) assert.deepEqual({...anchor(view)}, {view});
assert.deepEqual({...anchor('simulation')}, {away: '/simulation'});
assert.deepEqual({...anchor('constructor')}, {view: 'constructor'});
assert.deepEqual({...anchor('toString')}, {view: 'toString'});
