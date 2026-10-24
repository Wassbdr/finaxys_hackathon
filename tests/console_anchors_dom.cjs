// Le script d'en-tête de la console est joué tel quel : une ancre partie vers son espace y mène, les autres restent.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).filter(s => s.includes('AWAY'));
assert.equal(scripts.length, 1, 'one head script translates the anchors');
function visit(hash, search = '') {
  const location = {hash, search, to: null, replace(url) { this.to = url; }};
  const listeners = {};
  vm.runInNewContext(scripts[0], {location, URLSearchParams, addEventListener: (kind, fn) => { listeners[kind] = fn; }});
  return {location, listeners};
}
assert.equal(visit('#simulation').location.to, '/simulation');
assert.equal(visit('#sentinel').location.to, '/regulation#marche');
assert.equal(visit('', '?embed=simulation').location.to, '/simulation?embed=simulation');
for (const stays of ['', '#agents', '#summary', '#details', '#mails-chat', '#constructor', '#__proto__']) {
  assert.equal(visit(stays).location.to, null, stays);
}
assert.equal(visit('', '?embed=impact').location.to, null);
// an anchor changed once the console is open follows the same way
const page = visit('#agents');
page.location.hash = '#simulation';
page.listeners.hashchange();
assert.equal(page.location.to, '/simulation');
