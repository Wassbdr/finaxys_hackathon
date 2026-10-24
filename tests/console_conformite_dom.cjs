// Le harnais interdit les puits HTML et les routes d'écriture hors brouillon.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ui = require(process.argv[2]);
class Element {
  constructor(tag, doc) { this.tagName = tag; this.ownerDocument = doc; this.children = []; this.events = {}; this.attributes = {}; this.className = ''; this.value = ''; this.disabled = false; this.text = ''; this.classList = {add: cls => { this.className += ' '+cls; }}; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this.text = ''; }
  set textContent(text) { this.text = String(text); this.children = []; }
  get textContent() { return this.text + this.children.map(n => n.textContent).join(' '); }
  set innerHTML(_) { throw Error('Contenu HTML interprété'); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(type, fn) { this.events[type] = fn; }
  all() { return this.children.flatMap(n => [n, ...n.all()]); }
  querySelector(selector) { return this.all().find(n => selector[0] === '.' ? n.className.split(' ').includes(selector.slice(1)) : n.tagName === selector); }
  querySelectorAll(selector) { if (selector === 'tbody tr') return this.all().filter(n => n.tagName === 'tr'); return []; }
}
const doc = {createElement: tag => new Element(tag, doc), getElementById: id => ids[id] || null};
const ids = {}; doc.head = doc.createElement('head');
const data = JSON.parse(fs.readFileSync(process.argv[2].replace(/conformite.js$/, 'data/conformite.json')));
const attack = '<img src=x onerror="globalThis.pwned=true"><script>boom()</script>';
(async () => {
  const root = doc.createElement('div');
  const hostile = structuredClone(data); hostile.items[0].fait = attack; hostile.items[0].source = 'javascript:alert(1)';
  ui.render(root, hostile);
  assert(root.textContent.includes(attack));
  assert(root.textContent.includes('Contrôles classiques'));
  assert(root.textContent.includes('Limite :'));
  assert(!root.all().some(n => n.href?.startsWith('javascript:')));
  assert(root.all().some(n => n.tagName === 'button' && n.textContent === 'Préparer la notification DORA'));
  const compact = doc.createElement('details'); ui.render(compact, data, true);
  assert(compact.all().some(n => n.href === '/dashboard#conformite'));
  assert(compact.textContent.includes('Limite :'));
  const actions = doc.createElement('div'), row = doc.createElement('tr'), cell = doc.createElement('td'); row.append(cell); actions.append(row);
  ui.decorate(actions, data.badges); ui.decorate(actions, data.badges);
  assert.equal(cell.all().filter(n => n.tagName === 'a').length, 3);
  for (const link of cell.all().filter(n => n.tagName === 'a')) {
    assert(link.title.includes('Pourquoi le régulateur'));
    assert(link.attributes['aria-label'].includes(link.textContent));
  }
  const output = doc.createElement('pre'), calls = [];
  const fetcher = async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => ({text: attack, citations: [{message_id: attack}]})}; };
  await ui.prepareDraft(output, '', fetcher); assert.equal(calls.length, 0);
  await ui.prepareDraft(output, 'fake-launch-token', fetcher);
  assert.equal(calls.length, 1); assert.equal(calls[0].url, '/demo/krys/ask');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['X-Demo-Token'], 'fake-launch-token');
  assert.deepEqual(JSON.parse(calls[0].options.body), {question: 'Brouillon DORA'});
  assert(output.textContent.startsWith('BROUILLON À VALIDER PAR UN HUMAIN'));
  assert(output.textContent.includes(attack));
  await ui.prepareDraft(output, 'fake', async () => ({ok: false}));
  assert(output.textContent.includes('Aucun envoi effectué'));
  ids.conformite = root;
  const reads = [];
  await ui.mount(doc, async path => { reads.push(path); return {ok: true, json: async () => data}; });
  assert.deepEqual(reads, ['/console/conformite/data/conformite.json']);
  // Sans fichier de mesure, aucun état vert ne doit être inventé.
  ids.conformite = doc.createElement('div');
  await ui.mount(doc, async () => ({ok: false}));
  assert(!ids.conformite.textContent.includes('✅'));
  assert(ids.conformite.textContent.includes('indisponibles'));
})().catch(error => { console.error(error); process.exitCode = 1; });
