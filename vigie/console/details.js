/* Les mesures restent une lecture des preuves : aucun paramètre de décision n'est modifié. */
(() => {
  'use strict';
  const root = document.getElementById('details');
  if (!root) return;
  const css = document.createElement('link');
  css.rel = 'stylesheet'; css.href = '/console/details.css'; document.head.append(css);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const number = value => value == null ? 'N/A' : new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 2}).format(value);
  const pct = value => value == null ? 'N/A' : number(100 * value) + ' %';
  const data = '/console/details/data/';
  const proof = (name, label = name) => `<a href="${data + encodeURIComponent(name)}" target="_blank" rel="noopener">${esc(label)}</a>`;
  const link = mid => `<a href="/dashboard?details_message_id=${encodeURIComponent(mid)}&amp;details_mode=${encodeURIComponent(document.getElementById('details-mode').value)}#details" data-details-message="${esc(mid)}"><code>${esc(mid)}</code></a>`;
  const limit = text => `<p class="details-limit">Limite : ${esc(text)}</p>`;
  const table = (headers, rows) => `<div class="details-table"><table><thead><tr>${headers.map(x => `<th scope="col">${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(x => `<td>${x}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  async function get(url) {
    const response = await fetch(url, {cache: 'no-store'});
    if (!response.ok) throw new Error(response.status === 409 ? 'Aucun journal de démo connecté. Choisissez la mesure archivée.' : `Preuve indisponible (${response.status}).`);
    return response.json();
  }
  root.innerHTML = `<h2>Détails</h2><p class="lead">Des décisions aux preuves : provenance, journal, portique et mesures d’Argus. Consultation en lecture seule.</p>
    <nav aria-label="Rubriques des détails"><a href="#details" data-details-scroll="details-provenance">Provenance</a> · <a href="#details" data-details-scroll="details-journal">Journal</a> · <a href="#details" data-details-scroll="details-curve">Sûreté / utilité</a> · <a href="#details" data-details-scroll="details-limits">Nos limites</a></nav>
    <article id="details-provenance"><h3>Chaîne de provenance d’une décision</h3>
      <form id="details-form"><label>Source<select id="details-mode"><option value="archive">Mesure archivée · PHP2Shell · graine 42</option><option value="live">Journal de la démo Krys</option></select></label>
      <label>Identifiant du message<input id="details-mid" name="message_id" maxlength="256" required placeholder="message_id" autocomplete="off"></label><button class="act" type="submit">Lire la chaîne</button></form>
      <p id="details-chain-status" role="status"></p><div id="details-chain"></div>
      ${limit('les liens décrivent les dépendances déclarées et les preuves citées ; une signature ne prouve pas la vérité de la donnée. Une référence manquante rend la chaîne incomplète.')}</article>
    <article><h3>Décisions du portique et preuves</h3><p>Dernières exécutions du journal choisi, acceptées ou refusées. Cliquez sur leur message_id pour remonter les visas et les preuves.</p><button class="act ghost" id="details-refresh" type="button">Actualiser la liste</button><div id="details-decisions"></div>
      ${limit('la mesure archivée porte sur une journée simulée, parmi les 10 journées mesurées. Les contrôles classiques appliquent aussi des contrôles avant exécution ; ce journal illustre Krys uniquement.')}</article>
    <article id="details-journal"><h3>Journal signé et chaîné</h3><p>Extrait du journal PHP2Shell, graine 42. Le serveur recalcule les empreintes SHA-256 depuis la première entrée. Les messages portent une signature HMAC par émetteur.</p><button class="act ghost" id="details-verify" type="button">Vérifier le chaînage</button><p id="details-journal-status" role="status"></p><div id="details-excerpt"></div>
      <p>${proof('journal.jsonl', 'Journal complet')} · ${proof('example-result.json', 'Résultat de cette journée')}</p>
      ${limit('les signatures de cette archive sont celles validées lors de la mesure, sans nouvelle vérification HMAC ici. Le chaînage seul ne détecte pas une réécriture intégrale par un détenteur des clés ; la démo utilise la projection revérifiée de Krys.')}</article>
    <article id="details-curve"><h3>Courbe sûreté / utilité</h3><p>6 scénarios d’attaque × 10 journées simulées. Seuils évalués sur des journées distinctes de leur apprentissage. Aucun seuil n’est changé depuis cet écran.</p><div id="details-measures" role="status">Lecture des mesures…</div>
      ${limit('les décisions changent les trajectoires : les dénominateurs diffèrent entre banques. Le débit sain est un rapport à Sans Krys, sans plafonnement à 100 %, et ne mesure pas un gain financier.')}</article>
    <div class="details-grid"><article><h3>Panne et relais</h3><p>2 journées simulées, graines 42 et 43 ; arrêt à t60, redémarrage après 10, 30 ou 60 ticks. Le redémarrage actuel garde une incertitude qui empêche la reprise du portique ; le relais attesté conserve l’analyse et assure la continuité dans le banc.</p>
      <p>Comparaison au même Krys sans panne ; le témoin n’est pas une banque à contrôles classiques. La comparaison à ces contrôles figure dans la courbe.</p>
      <p>${proof('PANNE.md', 'Méthode, résultats et limites')} · ${proof('panne.json', 'Mesures de panne')}</p>
      ${limit('relais en banc d’essai, transport local sur pipes ; aucune haute disponibilité déployée. Hôte, disque, transport et clé commune ne sont pas couverts.')}</article>
    <article><h3>Annexe Invariant Guardrails</h3><p>Adaptation testée sur 3 scénarios, graine 42, moteur LocalPolicy réel et réseau interdit. Elle égale Krys sur PHP2Shell et sur le run propre ; le registre menteur échappe aux règles de cette adaptation.</p><p>Cette annexe complète le comparatif Sans Krys / Contrôles classiques / Krys.</p><p>${proof('ANNEXE_INVARIANT.md', 'Lire le protocole et les résultats')}</p>
      ${limit('une graine et ces règles d’intégration ne représentent pas toutes les capacités de la bibliothèque ; l’ordre d’une trace ne constitue pas une preuve indépendante de causalité.')}</article></div>
    <article id="details-limits"><h3>Nos limites</h3><ul>
      <li>Un trader corrompu isolé peut rester non détecté.</li><li>Un document piégé est signalé alors que des opérations contaminées passent encore.</li>
      <li>La dérive de marché seule peut rendre la prudence coûteuse en opérations saines.</li><li>Isoler un service qui irrigue toute la banque peut provoquer un sur-blocage.</li>
      <li>Les données sont simulées ; 10 journées par scénario ne prouvent pas un comportement en production.</li><li>Le relais reste un prototype de banc d’essai.</li></ul>
      <p>${proof('RESULTATS_ARGUS.md', 'Résultats détaillés, y compris défavorables')} · ${proof('resultats_argus.json', 'Résultats JSON')} · ${proof('SURETE_UTILITE.md', 'Définitions et méthode')}</p>
      ${limit('un feu vert ou une provenance signée ne garantissent ni absence de fraude ni pertinence financière.')}</article>
    <p id="details-origin"></p>`;
  const el = id => document.getElementById(id);
  let chainRequest = 0, listRequest = 0;
  async function loadChain(mid) {
    const request = ++chainRequest;
    el('details-mid').value = mid;
    el('details-chain-status').textContent = 'Lecture de la chaîne…';
    el('details-chain').textContent = '';
    try {
      const result = await get('/console/details/provenance?' + new URLSearchParams({message_id: mid, mode: el('details-mode').value}));
      if (request !== chainRequest) return;
      el('details-chain-status').textContent = result.complete ? 'Chaîne disponible dans le périmètre consulté.' : 'Chaîne incomplète : références absentes, projection incomplète ou limite de 64 messages.';
      el('details-chain').innerHTML = result.messages.map(m => `<div class="details-message"><b>${link(m.message_id)}</b> · t${esc(m.tick)} · ${esc(m.from)} · ${esc(m.type)}
        <p>Dépend de : ${(m.depends_on || []).map(link).join(', ') || 'aucune dépendance déclarée'}</p>
        <p>Preuves citées : ${evidence(m).map(link).join(', ') || 'aucune'}</p>
        <details><summary>Sources déclarées et décision</summary><pre>${esc(JSON.stringify({data_provenance: m.data_provenance, decision: m.payload}, null, 2))}</pre></details></div>`).join('') + result.missing.map(mid => `<p>Preuve manquante : ${esc(mid)}</p>`).join('');
    } catch (error) { if (request === chainRequest) el('details-chain-status').textContent = error.message; }
  }
  function evidence(m) {
    const refs = Array.isArray(m.payload?.finding?.evidence) ? m.payload.finding.evidence : [];
    try { const reason = m.payload?.gate?.reason; const parsed = typeof reason === 'string' ? JSON.parse(reason) : reason; return [...refs, ...(Array.isArray(parsed?.evidence) ? parsed.evidence : [])]; } catch { return refs; }
  }
  async function loadDecisions() {
    const request = ++listRequest;
    el('details-decisions').textContent = 'Lecture du journal choisi…';
    try {
      const result = await get('/console/details/provenance?' + new URLSearchParams({mode: el('details-mode').value}));
      if (request !== listRequest) return;
      el('details-decisions').innerHTML = result.decisions.length ? table(['Message', 'Tick', 'État déclaré', 'Preuves'], result.decisions.map(m => [link(m.message_id), esc(m.tick), esc(m.payload?.status || 'inconnu'), evidence(m).map(link).join(', ') || 'Voir la chaîne'])) : '<p>Aucune exécution disponible dans ce périmètre.</p>';
    } catch (error) { if (request === listRequest) el('details-decisions').textContent = error.message; }
  }
  async function verify() {
    el('details-journal-status').textContent = 'Recalcul du chaînage…';
    try {
      const report = await get('/console/details/journal');
      el('details-journal-status').textContent = `${report.chain_valid ? 'Chaîne intacte' : 'Chaîne invalide'} · ${number(report.entries)} entrées parcourues · ${number(report.recorded_valid_signatures)} signatures déclarées valides à l’enregistrement.`;
      el('details-excerpt').innerHTML = `<details><summary>Voir les premières entrées, signatures et empreintes</summary><pre>${esc(JSON.stringify(report.excerpt, null, 2))}</pre></details>`;
    } catch (error) { el('details-journal-status').textContent = error.message; }
  }
  async function measures() {
    try {
      const [curve, fixed, manifest] = await Promise.all(['curve.json', 'fixed-comparison.json', 'manifest.json'].map(n => get(data+n)));
      const names = {A: 'Sans Krys', B: 'Contrôles classiques', C: 'Krys · seuil livré'};
      const row = (label, m) => [esc(label), esc(`${number(m.contaminated_executed)} / ${number(m.contaminated_proposed)} (${pct(m.asr)})`), esc(`${number(m.healthy_executed)} / ${number(m.baseline_healthy_executed)} (${pct(m.utility)})`)];
      const points = curve.map(p => ({x: 60 + p.attack.asr * 400, y: 230 - p.attack.utility * 180, margin: p.margin, m: p.attack}));
      el('details-measures').innerHTML = table(['Banque', 'Contaminées exécutées / proposées', 'Saines exécutées / Sans Krys'], Object.entries(fixed).map(([k,m]) => row(names[k],m))) +
        `<svg class="details-chart" viewBox="0 0 520 300" role="img" aria-label="Courbe Krys, taux contaminé exécuté en abscisse et débit sain en ordonnée ; données dans le tableau suivant"><line x1="60" y1="230" x2="480" y2="230"/><line x1="60" y1="20" x2="60" y2="230"/><text x="65" y="18">Débit sain / Sans Krys (%)</text><text x="35" y="234">0</text><text x="20" y="54">100</text><text x="60" y="250">0</text><text x="450" y="250">100</text><text x="65" y="280">Contaminées exécutées / proposées (%)</text><polyline points="${points.map(p=>`${esc(p.x)},${esc(p.y)}`).join(' ')}"/>${points.map(p=>`<circle cx="${esc(p.x)}" cy="${esc(p.y)}" r="4"><title>${esc(`Marge ${p.margin} : ${pct(p.m.asr)} contaminées ; ${pct(p.m.utility)} débit sain`)}</title></circle>`).join('')}</svg>` +
        table(['Marge Krys', 'Contaminées exécutées / proposées', 'Saines exécutées / Sans Krys'], curve.map(p => row(p.margin === 0 ? '0 · livré' : String(p.margin),p.attack))) +
        `<p title="La marge est le paramètre freeze_profile.margin évalué dans le plan figé.">Les points sont les marges préenregistrées ; les trois premières se superposent. Les valeurs mesurent un arbitrage, sans choisir un nouveau seuil.</p><p>${proof('curve.json', 'Points et références')} · ${proof('fixed-comparison.json', 'Comparaison et références')} · ${proof('plan.json', 'Plan figé')}</p>`;
      el('details-origin').innerHTML = `Commit mesuré : <code>${esc(manifest.measured_commit)}</code> · preuves : <code>${esc(manifest.evidence_commit)}</code> · panne : <code>${esc(manifest.outage_commit)}</code>. ${proof('manifest.json', 'Origines et empreintes des fichiers')}`;
    } catch (error) { el('details-measures').textContent = error.message; }
  }
  el('details-form').addEventListener('submit', e => { e.preventDefault(); loadChain(el('details-mid').value.trim()); });
  el('details-mode').addEventListener('change', () => { ++chainRequest; el('details-chain').textContent = ''; el('details-chain-status').textContent = ''; loadDecisions(); });
  el('details-refresh').addEventListener('click', loadDecisions);
  el('details-verify').addEventListener('click', verify);
  root.addEventListener('click', e => {
    const message = e.target.closest('[data-details-message]');
    const scroll = e.target.closest('[data-details-scroll]');
    if (message) { e.preventDefault(); loadChain(message.dataset.detailsMessage); el('details-provenance').scrollIntoView({block:'start'}); }
    if (scroll) { e.preventDefault(); el(scroll.dataset.detailsScroll).scrollIntoView({block:'start'}); }
  });
  const params = new URLSearchParams(location.search);
  if (params.get('details_mode') === 'live') el('details-mode').value = 'live';
  measures(); loadDecisions(); verify();
  const initial = params.get('details_message_id');
  if (initial && initial.length <= 256) loadChain(initial);
})();
