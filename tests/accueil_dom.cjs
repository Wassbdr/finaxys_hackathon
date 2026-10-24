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
async function run(active) {
  const ids = new Map();
  for (const id of ['argus-phrase', 'krys-question', 'krys-question-form', 'krys-log', 'proof-status']) ids.set(id, new Element());
  const get = id => ids.get(id);
  get('proof-status').textContent = 'attente';
  const suggestion = new Element();
  suggestion.dataset = {question: '<script>question</script>'};
  const network = [];
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    document: {getElementById: get, querySelector: () => ({content: 'Voit. Isole. Prouve.'}),
      querySelectorAll: () => [suggestion]},
    MutationObserver: class { observe() {} },
    fetch: async (url, options) => {
      network.push([url, options.redirect, options.credentials]);
      return {ok: true, json: async () => ({tick: active ? 2 : -1})};
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  // la vue Krys ne lit que l'état de la démo : ni la console, ni une donnée de banque
  assert.deepEqual(network, [['/demo', 'error', 'omit']]);
  assert.equal(get('argus-phrase').textContent, 'Voit. Isole. Prouve.');
  suggestion.listeners.click.listener();
  assert.equal(get('krys-question').value, '<script>question</script>');
  assert.equal(get('krys-question').focused, true);
  assert.equal(get('krys-question-form').listeners.submit, undefined);
  assert.equal(get('proof-status').textContent, active
    ? 'Démo active. Les réponses de Krys restent limitées aux preuves signées disponibles.' : 'attente');
}
(async () => { await run(false); await run(true); })().catch(error => {
  console.error(error); process.exitCode = 1;
});
