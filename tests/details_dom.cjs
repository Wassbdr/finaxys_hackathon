// Les contenus du journal restent des données échappées, même dans les liens.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.listeners = {}; this.value = ''; this.html = ''; this.text = ''; }
  set innerHTML(value) { this.html = value; }
  get innerHTML() { return this.html; }
  set textContent(value) { this.text = value; this.html = ''; }
  get textContent() { return this.text; }
  addEventListener(type, listener) { this.listeners[type] = listener; }
  scrollIntoView() {}
}
async function main() {
  const ids = new Map();
  const get = id => { if (!ids.has(id)) ids.set(id, new Element()); return ids.get(id); };
  get('details-mode').value = 'archive';
  const attack = '\"><img src=x onerror=alert(1)><script>alert(1)</script>';
  const message = {message_id: attack, tick: attack, from: attack, type: 'execution', depends_on: [attack],
    data_provenance: [{service: attack}], payload: {status: attack, gate: {reason: JSON.stringify({evidence: [attack]})}}};
  const measure = {contaminated_executed: 448, contaminated_proposed: 2489, asr: .18, healthy_executed: 515, baseline_healthy_executed: 545, utility: .945};
  const requests = [];
  const context = {Intl, URLSearchParams, location: {search: ''}, document: {getElementById: get, createElement: () => ({}), head: {append() {}}},
    fetch: async (url, options) => {
      requests.push({url, options});
      let result;
      if (url.endsWith('curve.json')) result = [{margin: 0, attack: measure}];
      else if (url.endsWith('fixed-comparison.json')) result = {A: measure, B: measure, C: measure};
      else if (url.endsWith('manifest.json')) result = {measured_commit: attack, evidence_commit: attack, outage_commit: attack};
      else if (url.endsWith('/journal')) result = {chain_valid: true, entries: 1, recorded_valid_signatures: 1, excerpt: [message]};
      else if (url.includes('message_id=')) result = {complete: false, messages: [message], missing: [attack]};
      else result = {decisions: [message]};
      return {ok: true, json: async () => result};
    }};
  vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), context);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  get('details-mid').value = attack;
  get('details-form').listeners.submit({preventDefault() {}});
  await flush();
  for (const id of ['details-decisions', 'details-excerpt', 'details-chain', 'details-origin']) {
    assert.ok(get(id).innerHTML.includes('&lt;img'), id);
    assert.ok(!get(id).innerHTML.includes('<img'), id);
    assert.ok(!get(id).innerHTML.includes('<script>'), id);
  }
  assert.match(get('details-chain-status').textContent, /incomplète/);
  assert.match(get('details-measures').innerHTML, /448 \/ 2[\s\u202f]489/);
  assert.match(get('details-measures').innerHTML, /Contrôles classiques/);
  assert.ok(requests.every(r => !r.options.method || r.options.method === 'GET'));
  assert.ok(requests.every(r => r.url.startsWith('/console/details')));
  context.fetch = async () => ({ok: false, status: 409});
  get('details-mode').value = 'live';
  get('details-mode').listeners.change();
  await flush();
  assert.match(get('details-decisions').textContent, /Aucun journal/);
  assert.equal(get('details-chain').textContent, '');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
