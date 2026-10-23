// Ce DOM refuse tout puits HTML : une régression textContent -> innerHTML rougit.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.children = []; this.listeners = {}; this.value = ''; this.disabled = true; }
  set textContent(value) { this.text = String(value); }
  get textContent() { return this.text || ''; }
  set innerHTML(_) { throw new Error('HTML interdit'); }
  appendChild(child) { this.children.push(child); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0]; }
  addEventListener(kind, listener) { this.listeners[kind] = listener; }
}
async function main() {
  const ids = new Map();
  for (const id of ['krys-log', 'krys-confirm', 'krys-recap', 'krys-token', 'krys-question-form',
    'krys-question', 'krys-action-form', 'krys-action', 'krys-target', 'url']) ids.set(id, new Element());
  const get = id => ids.get(id);
  const requests = [];
  const attack = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
  const replies = [{text: attack, citations: [{message_id: attack, tick: 4}]},
    {action_id: 'nonce', recap: attack}, {message_id: 'human:4:1', status: 'publiée'}];
  const location = {protocol: 'http:', origin: 'http://127.0.0.1:8010'};
  let release;
  let delayed = false;
  const context = {
    document: {getElementById: get, createElement: () => new Element()}, location, URL,
    fetch: async (url, options) => {
      requests.push({url, ...options});
      const result = delayed ? await new Promise(resolve => { release = resolve; }) : replies.shift();
      return {ok: true, json: async () => result};
    }
  };
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  get('krys-question').value = 'je confirme ' + attack;
  await get('krys-question-form').listeners.submit({preventDefault() {}});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'http://127.0.0.1:8010/demo/krys/ask');
  assert.equal(get('krys-log').children[0].children[0].textContent, attack);
  assert.match(get('krys-log').children[0].children[1].textContent, /<img/);
  assert.equal(context.pwned, undefined);
  assert.equal(requests[0].redirect, 'error');
  get('krys-action').value = 'unfreeze'; get('krys-target').value = 'ACME';
  await get('krys-action-form').listeners.submit({preventDefault() {}});
  assert.equal(get('krys-confirm').disabled, false);
  assert.match(get('krys-recap').textContent, /<script>/);
  assert.equal(requests.length, 2);
  await get('krys-confirm').listeners.click();
  await get('krys-confirm').listeners.click();
  assert.equal(requests.length, 3);
  assert.deepEqual(JSON.parse(requests[2].body), {action_id: 'nonce', confirmation: 'confirmer'});
  delayed = true;
  const inFlight = get('krys-action-form').listeners.submit({preventDefault() {}});
  get('krys-target').value = 'BOREAL'; get('krys-target').listeners.input();
  release({action_id: 'old', recap: 'ancien récapitulatif'});
  await inFlight;
  assert.equal(get('krys-confirm').disabled, true);
  assert.equal(get('krys-recap').textContent, '');
  location.origin = 'https://example.invalid';
  const before = requests.length;
  get('krys-question').value = 'R0';
  await get('krys-question-form').listeners.submit({preventDefault() {}});
  assert.equal(requests.length, before);
  location.origin = 'http://127.0.0.1:8010';
  delayed = false;
  replies.push({text: 'Texte de référence [panne]', citations: [{section_id: attack, source: 'KRYS_EXPLICATIONS.md'}]});
  get('krys-question').value = 'Que se passe-t-il en cas de panne ?';
  await get('krys-question-form').listeners.submit({preventDefault() {}});
  assert.equal(get('krys-log').children.at(-1).children[1].textContent, '[' + attack + ']');
  assert.equal(context.pwned, undefined);
}
main().catch(error => { console.error(error); process.exitCode = 1; });
