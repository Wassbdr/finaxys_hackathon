// Le rendu réel est exécuté avec un DOM minimal ; aucun transport n'est simulé.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const config = JSON.parse(fs.readFileSync(0, 'utf8'));
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.style = {}; this.className = ''; this._text = ''; this.raw = ''; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  set innerHTML(value) { this.raw = String(value); this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.raw = ''; this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}
const root = new Element('section');
const context = vm.createContext({document: {getElementById: () => root, createElement: tag => new Element(tag)}, console});
vm.runInContext(source.slice(0, source.indexOf('const drawn =')), context);
vm.runInContext('function put(id, html) { document.getElementById(id).innerHTML = html; }', context);
vm.runInContext(source.slice(source.indexOf('const THRESHOLDS ='), source.indexOf('// --- the details popup')), context);
context.config = config;
function all(node) { return [node, ...node.children.flatMap(all)]; }
function draw(limits = {}, edit = '') {
  vm.runInContext(`GR = JSON.parse(JSON.stringify(config)); D = {S: {limits: ${JSON.stringify(limits)}, params: {}, agents: {}}, st: {}, fake: new Set()}; ${edit};`, context);
  const before = vm.runInContext('JSON.stringify([GR,D])', context);
  vm.runInContext('renderGuardrails()', context);
  assert.equal(vm.runInContext('JSON.stringify([GR,D])', context), before, 'Rendering must not change its inputs');
  return all(root);
}
let nodes = draw({bank: {exposure_pct: 85, var_pct: 100}});
let meters = nodes.filter(n => n.attributes.role === 'meter');
assert(meters.some(n => n.attributes['aria-valuenow'] === '85' && n.className.includes('orange')), '85% must have an orange meter');
assert(meters.some(n => n.attributes['aria-valuenow'] === '100' && n.className.includes('red')), '100% must have a red meter');
assert(nodes.find(n => n.id === 'gr-near').textContent.includes('Exposition brute'));
assert.equal(nodes.filter(n => n.tagName === 'h2').map(n => n.textContent).filter(t => ['Trader','Compliance','Risk','Payment','Quant','Krys','LLM'].includes(t)).length, 7);
assert(root.textContent.includes('0,50 M€'), 'Small and large amounts must use the same M€ unit');
nodes = draw(); assert.equal(nodes.filter(n => n.attributes.role === 'meter').length, 0, 'Missing usage must not become a zero meter');
assert(root.textContent.includes("Aucune lecture d'usage disponible"));
nodes = draw({bank: {exposure_pct: 79.9}});
assert(root.textContent.includes('Aucune limite proche de son seuil'));
assert(!nodes.find(n => n.id === 'gr-near').textContent.includes('Exposition brute'));
nodes = draw({bank: {exposure_pct: 80}});
assert(nodes.filter(n => n.attributes.role === 'meter').some(n => n.className.includes('orange')));
nodes = draw({bank: {exposure_pct: 120}});
assert(nodes.some(n => n.attributes.role === 'meter' && n.attributes['aria-valuenow'] === '100' && n.attributes['aria-valuetext'].includes('120%')));
assert(nodes.some(n => n.style.width === '100%'));
nodes = draw({traders: {'trader-1': {max_position_pct: 85}, 'trader-2': {max_position_pct: 20}}});
assert(root.textContent.includes('trader-1') && root.textContent.includes('trader-2'));
assert(nodes.find(n => n.id === 'gr-near').textContent.includes('trader-1'));
assert(!nodes.find(n => n.id === 'gr-near').textContent.includes('trader-2'));
const attack = '<img src=x onerror="globalThis.pwned=true">'; context.attack = attack;
nodes = draw({bank: {var_pct: null}}, 'GR.agents.trader.max_order_eur = attack; GR.gate.timeout_s = attack; D.S.params.restricted_list = [attack]; D.st.metrics = {thresholds: {[attack]: attack}}');
assert(root.textContent.includes(attack), 'Hostile values must remain literal text');
assert(nodes.every(n => !n.raw && n.tagName !== 'img'), 'No value may become interpreted HTML');
assert.equal(nodes.filter(n => n.attributes.role === 'meter').length, 0);
for (const meter of meters) {
  assert.equal(meter.attributes['aria-valuemin'], '0'); assert.equal(meter.attributes['aria-valuemax'], '100');
  assert(meter.attributes['aria-label']); assert(meter.attributes['aria-valuetext']);
}
