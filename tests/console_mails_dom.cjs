// Un DOM sans puits HTML prouve que les valeurs hostiles restent du texte.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ids = new Map();
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.hidden = false;
    this.classList = {add() {}}; this.value = ''; this.text = ''; }
  set innerHTML(_) { throw new Error('Puits HTML interdit'); }
  set textContent(text) { this.text = String(text); }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
  setAttribute(key, value) {
    this.attributes[key] = value;
    if (key === 'id') ids.set(value, this);
    if (key === 'hidden') this.hidden = true;
    if (key === 'disabled') this.disabled = true;
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  addEventListener(kind, listener) { this.listeners[kind] = listener; }
  focus() { focused = this; }
}
let focused;
const root = new Element('section'); root.hidden = false; ids.set('mails-chat', root);
const document = {getElementById: id => ids.get(id), createElement: tag => new Element(tag),
  head: new Element('head'), body: new Element('body')};
const attack = '<img src=x onerror="globalThis.pwned=true">';
const requests = [];
function all(element) { return [element, ...element.children.flatMap(all)]; }
async function main() {
  let smtp = false;
  const context = {document, location: {origin: 'http://127.0.0.1:8010'}, setInterval() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      return {ok: true, json: async () => ({complete: true, smtp_enabled: smtp, items: [{id: 'safe',
        subject: attack, impact: attack, action: attack, period: attack, comparison: attack, limit: attack,
        evidence: [attack], link: 'javascript:globalThis.pwned=true'}]})};
    }};
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/console/mails-chat/outbox');
  assert.equal(context.pwned, undefined);
  assert(root.textContent.includes(attack));
  assert(all(root).filter(e => e.tagName === 'a').every(e => e.attributes.href === '/dashboard#mails-chat'));
  assert(!all(root).some(e => e.tagName === 'button' && e.textContent.includes('Envoyer')));
  const panel = ids.get('mc-chat'), opener = ids.get('mc-open');
  assert(document.body.children.includes(panel));
  assert(!root.children.includes(panel));
  opener.listeners.click();
  assert.equal(panel.hidden, false); assert.equal(focused, ids.get('krys-question'));
  // Changer d’onglet masque la section mais jamais la bulle extérieure.
  root.hidden = true;
  assert.equal(panel.hidden, false);
  panel.listeners.keydown({key: 'Escape', preventDefault() {}});
  assert.equal(panel.hidden, true); assert.equal(focused, opener);
  assert.equal(ids.get('krys-confirm').disabled, true);
  assert(document.body.children.some(e => e.tagName === 'script' && e.attributes.src === '/demo/krys.js'));
  smtp = true;
  await all(root).find(e => e.tagName === 'button' && e.textContent === 'Actualiser').listeners.click();
  const send = all(root).find(e => e.tagName === 'button' && e.textContent.includes('Envoyer'));
  await send.listeners.click();
  assert.equal(requests.length, 2); // Sans jeton, aucune écriture.
  ids.get('krys-token').value = 'fixture-token';
  await send.listeners.click();
  assert.equal(requests.at(-1).url, '/console/mails-chat/send');
  assert.equal(requests.at(-1).options.headers['X-Demo-Token'], 'fixture-token');
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {incident_id: 'safe'});
  assert.equal(requests.at(-1).options.redirect, 'error');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
