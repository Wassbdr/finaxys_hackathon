// Les suggestions ne bloquent pas le formulaire partagé, qui porte seul les requêtes du chat.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.listeners = {}; this.attributes = {}; this.value = ''; this.hidden = false; }
  addEventListener(kind, listener, capture) { this.listeners[kind] = {listener, capture}; }
  setAttribute(key, value) { this.attributes[key] = value; }
  getAttribute(key) { return this.attributes[key]; }
  focus() { this.focused = true; }
  set innerHTML(_) { throw new Error('HTML interdit'); }
}
async function run(impact) {
  const ids = new Map();
  for (const id of ['argus-phrase', 'krys-question', 'nav-toggle', 'console-nav', 'krys-question-form',
    'krys-access', 'krys-log', 'metrics-link', 'proof-status']) ids.set(id, new Element());
  const get = id => ids.get(id);
  get('metrics-link').href = '/dashboard#metrics';
  get('proof-status').textContent = 'attente';
  const suggestion = new Element();
  suggestion.dataset = {question: '<script>question</script>'};
  const network = [];
  const media = {matches: true, addEventListener() {}};
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    document: {getElementById: get, querySelector: () => ({content: 'Voit. Isole. Prouve.'}),
      querySelectorAll: () => [suggestion]},
    matchMedia: () => media,
    MutationObserver: class { observe() {} },
    DOMParser: class { parseFromString() { return {querySelector: () => impact}; } },
    fetch: async url => {
      network.push(url);
      return {ok: true, text: async () => '', json: async () => ({tick: impact ? 2 : -1})};
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(network, ['/console', '/demo']);
  suggestion.listeners.click.listener();
  assert.equal(get('krys-question').value, '<script>question</script>');
  assert.equal(get('krys-question').focused, true);
  assert.equal(get('console-nav').hidden, true);
  get('nav-toggle').listeners.click.listener();
  assert.equal(get('console-nav').hidden, false);
  assert.equal(get('nav-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(get('krys-question-form').listeners.submit, undefined);
  assert.equal(get('metrics-link').href, impact ? '/console#impact' : '/dashboard#metrics');
  assert.equal(get('proof-status').textContent, impact
    ? 'Démo active. Les réponses de Krys restent limitées aux preuves signées disponibles.' : 'attente');
}
(async () => { await run(false); await run(true); })().catch(error => {
  console.error(error); process.exitCode = 1;
});
