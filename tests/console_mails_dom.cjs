// Un DOM sans puits HTML prouve que les valeurs hostiles restent du texte.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ids = new Map();
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.hidden = false;
    this.classList = {add() {}}; this.value = ''; this.text = ''; this.parentNode = null; }
  set innerHTML(_) { throw new Error('Puits HTML interdit'); }
  set textContent(text) { this.text = String(text); }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
  setAttribute(key, value) {
    this.attributes[key] = value;
    if (key === 'id') ids.set(value, this);
    if (key === 'hidden') this.hidden = true;
    if (key === 'disabled') this.disabled = true;
  }
  // comme le vrai DOM, ajouter un nœud déjà placé le retire de son ancien parent
  appendChild(child) {
    if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
    child.parentNode = this; this.children.push(child); return child;
  }
  replaceChildren() { this.children = []; }
  addEventListener(kind, listener) { this.listeners[kind] = listener; }
  focus() { focused = this; }
}
let focused;
const root = new Element('section'); root.hidden = false; ids.set('mails-chat', root);
// la vue Krys et son chat, tels que la console les sert
const view = new Element('section'); ids.set('krys', view);
const chat = view.appendChild(new Element('div')); ids.set('krys-chat', chat);
const question = chat.appendChild(new Element('input')); ids.set('krys-question', question);
const observers = [];
const document = {getElementById: id => ids.get(id), createElement: tag => new Element(tag),
  head: new Element('head'), body: new Element('body')};
const attack = '<img src=x onerror="globalThis.pwned=true">';
const requests = [];
function all(element) { return [element, ...element.children.flatMap(all)]; }
function setHidden(element, hidden) { element.hidden = hidden; observers.forEach(fn => fn()); }
async function main() {
  let smtp = false;
  const context = {document, location: {origin: 'http://127.0.0.1:8010'}, setInterval() {},
    MutationObserver: class { constructor(fn) { observers.push(fn); } observe() {} },
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
  assert(all(root).filter(e => e.tagName === 'a').every(e => e.attributes.href === '/console#krys'));
  assert(!all(root).some(e => e.tagName === 'button' && e.textContent.includes('Envoyer')));
  const panel = ids.get('mc-chat'), opener = ids.get('mc-open');
  assert(document.body.children.includes(panel));
  assert(!root.children.includes(panel));
  // le chat n'est lié qu'une fois : la bulle ne recharge pas le client
  assert(!all(document.body).some(e => e.tagName === 'script'));
  // sur la vue Krys, la bulle s'efface ; ailleurs, elle ouvre le même chat
  assert.equal(opener.hidden, true);
  setHidden(view, true);
  assert.equal(opener.hidden, false);
  opener.listeners.click();
  assert.equal(panel.hidden, false); assert.equal(focused, question);
  assert(panel.children.includes(chat)); assert(!view.children.includes(chat));
  // Changer de vue masque la section de la boîte d'envoi, jamais la bulle extérieure.
  root.hidden = true;
  assert.equal(panel.hidden, false);
  panel.listeners.keydown({key: 'Escape', preventDefault() {}});
  assert.equal(panel.hidden, true); assert.equal(focused, opener);
  assert(view.children.includes(chat));
  // revenir sur la vue Krys pendant que la bulle est ouverte rend le chat à sa vue
  opener.listeners.click();
  setHidden(view, false);
  assert.equal(panel.hidden, true); assert.equal(opener.hidden, true); assert(view.children.includes(chat));
  // « Examiner avec Krys » pose la question, dans la bulle hors de la vue Krys
  setHidden(view, true);
  all(root).find(e => e.tagName === 'button' && e.textContent === 'Examiner avec Krys').listeners.click();
  assert.equal(question.value, `Pourquoi ${attack} ?`);
  assert.equal(panel.hidden, false); assert(panel.children.includes(chat));
  smtp = true;
  await all(root).find(e => e.tagName === 'button' && e.textContent === 'Actualiser').listeners.click();
  const send = all(root).find(e => e.tagName === 'button' && e.textContent.includes('Envoyer'));
  await send.listeners.click();
  assert.equal(requests.length, 3);
  assert.equal(ids.has('krys-token'), false);
  assert.equal(requests.at(-1).url, '/console/mails-chat/send');
  assert.deepEqual(Object.keys(requests.at(-1).options.headers), ['Content-Type']);
  assert.deepEqual(JSON.parse(requests.at(-1).options.body), {incident_id: 'safe'});
  assert.equal(requests.at(-1).options.redirect, 'error');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
