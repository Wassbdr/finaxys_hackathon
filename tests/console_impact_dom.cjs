/* Un DOM sans parseur HTML fait échouer toute tentative de réinterpréter les mesures comme du balisage. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const impact = require(process.argv[2]);
const source = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.style = {}; this.className = ''; this._text = ''; this.classList = {add: name => {this.className += ' ' + name;}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent).join(''); }
  set innerHTML(_) { throw new Error('innerHTML interdit pour les mesures'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = [...children]; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
}
const elements = {};
const doc = {createElement: tag => new Element(tag), head: new Element('head'), getElementById: id => elements[id] || null};
function all(node) { return [node, ...node.children.flatMap(all)]; }
(async () => {
  const full = new Element('section');
  impact.render(doc, full, source);
  assert.equal(all(full).filter(x => x.tagName === 'article').length, 6);
  assert.match(full.textContent, /94,50 %/);
  assert.match(full.textContent, /97,43 %/);
  assert.match(full.textContent, /319/);
  assert.match(full.textContent, /95 %/);
  assert.match(full.textContent, /conditionnelle/);
  assert.match(full.textContent, /10 refus à tort/);
  assert.match(full.textContent, /pas encore en direct/);
  assert.match(full.textContent, /aucun constat/);
  assert.ok(all(full).filter(x => x.className.includes('impact-undefined')).every(x => x.children[0].style.width === '0%'));
  const data = structuredClone(source);
  data.cards[0].title = '<img src=x onerror=alert(1)>';
  data.cards[0].limit = '</p><script>alert(1)</script>';
  data.cards[0].proofs[0].url = 'javascript:alert(1)';
  data.cards[0].proofs[0].label = '<svg onload=alert(1)>';
  impact.render(doc, full, data);
  assert.match(full.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(all(full).filter(x => ['img', 'script', 'svg'].includes(x.tagName)).length, 0);
  assert.ok(all(full).filter(x => x.tagName === 'a').every(x => x.href.startsWith('/dashboard/console/impact/')));
  assert.equal(impact.safeProof('//evil.test/a'), '/dashboard/console/impact/manifest.json');
  assert.equal(impact.safeProof('/dashboard/console/impact/../config.yaml'), '/dashboard/console/impact/manifest.json');
  const summary = new Element('aside');
  impact.render(doc, summary, source, true);
  assert.equal(summary.children[0].href, '/console#impact');
  assert.match(summary.textContent, /Contrôles classiques|contrôles classiques/);
  assert.match(summary.textContent, /cf4973a/);
  elements.impact = full;
  let requests = [];
  impact.start(doc, (url, options) => {requests.push({url, options}); return Promise.resolve({ok: false});});
  await new Promise(resolve => setImmediate(resolve));
  assert.match(full.textContent, /Aucun chiffre de remplacement/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.method, 'GET');
  console.log('Six cartes, résumé, absence de mesures et valeurs hostiles vérifiés sans réseau.');
})().catch(error => {console.error(error); process.exitCode = 1;});
