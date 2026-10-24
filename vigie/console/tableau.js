/* Le tableau de bord de la console : les mesures qui font la valeur d'Argus, rangées en arborescence et montrées une
   page à la fois. Chaque chiffre vient des archives publiées (vigie/console/data) ou de ce que Krys publie en direct ;
   un chiffre absent s'affiche absent. Les graphes sont du SVG écrit ici, chaque valeur passe par esc(). */
(function () {
  'use strict';
  const root = document.getElementById('tableau-body');
  if (!root) return;
  // the Krys series in deep blue, the classic controls' in light blue (checked for colour blindness, values written out)
  const DEEP = '#1446C8', PALE = '#6AA6FF';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const num = (x, digits = 0) => x == null || Number.isNaN(+x) ? '-' : (+x).toLocaleString('fr-FR', {minimumFractionDigits: digits, maximumFractionDigits: digits});
  const pct = (x, digits = 1) => x == null ? '-' : num(100 * x, digits) + ' %';
  const eur = v => v == null ? '-' : Math.abs(v) >= 1e6 ? num(v / 1e6, 2) + ' M€' : Math.abs(v) >= 1e3 ? num(v / 1e3) + ' k€' : num(v) + ' €';
  const CASES = {php2shell_day: 'Faille PHP2Shell', ledger_legacy: 'Registre altéré', rumeur_mimetisme: 'Rumeur et mimétisme',
    jour_noir: 'Jour noir', dossier_piege: 'Dossier piégé', propre: 'Journée normale'};
  const caseName = c => CASES[c] ?? String(c).replaceAll('_', ' ');
  const SERIES = [['C', 'Krys', DEEP], ['B', 'Contrôles classiques', PALE]];

  // --- the tree: what the dashboard holds, one page at a time ---------------------------------------------------
  const TREE = [
    {id: 'apercu', name: "Vue d'ensemble"},
    {id: 'protection', name: 'Protection', children: [
      {id: 'fraude', name: 'Fraude au virement'}, {id: 'contamination', name: 'Opérations contaminées'}, {id: 'detection', name: 'Délai de détection'}]},
    {id: 'continuite', name: "Continuité d'activité", children: [
      {id: 'activite', name: 'Activité saine sous attaque'}, {id: 'normales', name: 'Journées normales'}, {id: 'panne', name: 'Panne de Krys'}]},
    {id: 'reglage', name: 'Réglage', children: [{id: 'arbitrage', name: 'Sûreté et utilité'}]},
    {id: 'direct', name: 'En direct', children: [
      {id: 'portique', name: 'Portique'}, {id: 'contagion', name: 'Contagion'}, {id: 'limites', name: 'Limites'}, {id: 'resultat', name: 'Résultat de la journée'}]},
    {id: 'conformite', name: 'Conformité'},
  ];
  const leaves = TREE.flatMap(n => n.children ? n.children.map(c => ({...c, parent: n})) : [n]);
  const DATA = {}, LIVE = {snapshot: null, pnl: []};
  let page = 'apercu';

  // --- the charts ------------------------------------------------------------------------------------------------
  const legend = series => `<p class="tb-legend">${series.map(([, name, color]) => `<span><i style="background:${color}"></i>${esc(name)}</span>`).join('')}</p>`;
  const table = (head, rows) => `<details class="tb-numbers"><summary>Voir les chiffres</summary><table><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></details>`;
  /** Horizontal bars, grouped by label: each bar its value written at its end; a hover says it again. */
  function bars(groups, {max, fmt = num, limits = []} = {}) {
    const W = 680, LABEL = 220, BAR = 14, GAP = 2, PAD = 14, plot = W - LABEL - 90;
    const top = max ?? Math.max(1e-9, ...groups.flatMap(g => g.values.map(v => v.value ?? 0)));
    let y = 6, body = '';
    for (const g of groups) {
      const h = g.values.length * (BAR + GAP) - GAP;
      body += `<text x="0" y="${y + h / 2 + 4}" class="tb-label">${esc(g.label)}</text>`;
      g.values.forEach((v, i) => {
        const yy = y + i * (BAR + GAP), w = v.value == null ? 0 : Math.max(2, Math.min(1, v.value / top) * plot);
        body += v.value == null
          ? `<text x="${LABEL}" y="${yy + 11}" class="tb-missing">${esc(v.name)} : aucun constat</text>`
          : `<g><title>${esc(`${g.label} · ${v.name} : ${fmt(v.value)}`)}</title><rect x="${LABEL}" y="${yy}" width="${w}" height="${BAR}" rx="3" fill="${v.color}"/>`
            + `<text x="${LABEL + w + 6}" y="${yy + 11}" class="tb-value">${esc(fmt(v.value))}</text></g>`;
      });
      y += h + PAD;
    }
    for (const l of limits) body += `<line x1="${LABEL + l.at / top * plot}" x2="${LABEL + l.at / top * plot}" y1="0" y2="${y - PAD + 6}" class="tb-limit"/><text x="${LABEL + l.at / top * plot + 4}" y="${y - PAD + 4}" class="tb-axis">${esc(l.name)}</text>`;
    return `<svg class="tb-chart" viewBox="0 0 ${W} ${y - PAD + 12}" role="img" aria-label="${esc(groups.map(g => g.label).join(', '))}">${body}</svg>`;
  }
  /** Lines over a common axis (never two scales): x from the points, y from 0 to the largest value. */
  function lines(series, {xfmt = num, yfmt = num, ymin = 0, ymax, marks = []} = {}) {
    const W = 640, H = 240, L = 64, B = 28, all = series.flatMap(s => s.points);
    if (!all.length) return '<p class="tb-empty">Pas encore de point.</p>';
    const x0 = Math.min(...all.map(p => p[0])), x1 = Math.max(...all.map(p => p[0])) || 1;
    const lo = Math.min(ymin, ...all.map(p => p[1])), hi = ymax ?? Math.max(...all.map(p => p[1])), span = (hi - lo) || 1;
    const X = v => L + (W - L - 16) * (x1 === x0 ? .5 : (v - x0) / (x1 - x0)), Y = v => 8 + (H - B - 8) * (hi - v) / span;
    let body = '';
    for (let i = 0; i <= 4; i++) { const v = lo + span * i / 4; body += `<line x1="${L}" x2="${W - 16}" y1="${Y(v)}" y2="${Y(v)}" class="tb-grid"/><text x="${L - 8}" y="${Y(v) + 4}" class="tb-axis" text-anchor="end">${esc(yfmt(v))}</text>`; }
    body += `<text x="${L}" y="${H - 8}" class="tb-axis">${esc(xfmt(x0))}</text><text x="${W - 16}" y="${H - 8}" class="tb-axis" text-anchor="end">${esc(xfmt(x1))}</text>`;
    for (const m of marks) body += `<line x1="${X(m.at)}" x2="${X(m.at)}" y1="8" y2="${H - B}" class="tb-limit"/><text x="${X(m.at) + 4}" y="${H - B - 6}" class="tb-axis">${esc(m.name)}</text>`;
    for (const s of series) {
      body += `<polyline points="${s.points.map(p => `${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(' ')}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
      for (const p of s.points.length <= 12 ? s.points : []) body += `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="4" fill="${s.color}" stroke="#fff" stroke-width="2"><title>${esc(`${s.name} · ${xfmt(p[0])} : ${yfmt(p[1])}`)}</title></circle>`;
      const last = s.points.at(-1);
      body += `<text x="${Math.min(X(last[0]) + 6, W - 120)}" y="${Y(last[1]) - 8}" class="tb-value">${esc(yfmt(last[1]))}</text>`;
    }
    return `<svg class="tb-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map(s => s.name).join(', '))}">${body}</svg>`;
  }
  const tile = (value, name, under = '') => `<div class="tb-tile"><b>${value == null ? '<span class="tb-missing">manquant</span>' : esc(value)}</b><span>${esc(name)}</span>${under ? `<small>${esc(under)}</small>` : ''}</div>`;
  const head = (title, lead, source) => `<h2>${esc(title)}</h2><p class="tb-lead">${esc(lead)}</p>${source ? `<p class="tb-source">Source : <a href="${esc(source.url)}" target="_blank" rel="noopener">${esc(source.label)}</a></p>` : ''}`;
  const unavailable = what => `<p class="tb-empty">${esc(what)} n'est pas disponible : aucune mesure archivée n'a pu être lue.</p>`;

  // --- the archived measures -------------------------------------------------------------------------------------
  const IMPACT = '/dashboard/console/impact/';
  const krysRows = (card, label) => card?.groups?.flatMap(g => g.rows.filter(r => r.label === label)) ?? [];
  const card = id => DATA.impact?.cards?.find(c => c.id === id);
  const runs = () => (DATA.fraud?.runs ?? []).filter(r => r.variant === 'B' || r.variant === 'C');
  function byCase(field, filter = () => true) {
    const out = new Map();
    for (const r of runs().filter(filter)) {
      const slot = out.get(r.case) ?? {B: [], C: []};
      slot[r.variant].push(r[field]); out.set(r.case, slot);
    }
    return out;
  }
  const mean = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;

  const PAGES = {
    apercu() {
      const f = DATA.fixed, fraud = card('fraud'), util = card('utility');
      const [fb] = krysRows(fraud, 'Contrôles classiques'), [fc] = krysRows(fraud, 'Krys');
      const [ub] = krysRows(util, 'Contrôles classiques'), [uc] = krysRows(util, 'Krys');
      const det = krysRows(card('detection'), 'Krys').flatMap(r => r.delay_range ?? []);
      let html = head("Vue d'ensemble", "Ce que Krys apporte, mesuré sur les journées archivées : six attaques jouées chacune sur dix journées, et deux journées de fraude au virement. Les contrôles classiques servent de repère.", {url: IMPACT + 'manifest.json', label: 'mesures archivées et empreintes'});
      html += `<div class="tb-tiles">${tile(fc ? eur(fc.numerator) + ' volé' : null, 'fraude au virement avec Krys', fb && fc ? `sur ${eur(fc.denominator)} tentés ; ${eur(fb.numerator)} volés avec des contrôles classiques` : '')}`
        + tile(f?.C ? pct(f.C.contaminated_executed / f.C.contaminated_proposed) : null, 'des opérations contaminées exécutées', f?.B ? `${pct(f.B.contaminated_executed / f.B.contaminated_proposed)} avec des contrôles classiques` : '')
        + tile(uc ? pct(uc.numerator / uc.denominator) : null, "de l'activité saine conservée sous attaque", ub ? `${pct(ub.numerator / ub.denominator)} avec des contrôles classiques` : '')
        + tile(det.length ? `${Math.max(...det)} ticks` : null, 'au plus pour repérer une attaque', 'la faille connue est repérée avant même l\'attaque') + '</div>';
      if (f?.B && f?.C && fb && fc && ub && uc) html += legend(SERIES) + bars([
        {label: 'Euros volés', values: [{name: 'Krys', value: fc.numerator / fc.denominator, color: DEEP}, {name: 'Contrôles classiques', value: fb.numerator / fb.denominator, color: PALE}]},
        {label: 'Contaminées exécutées', values: [{name: 'Krys', value: f.C.contaminated_executed / f.C.contaminated_proposed, color: DEEP}, {name: 'Contrôles classiques', value: f.B.contaminated_executed / f.B.contaminated_proposed, color: PALE}]},
        {label: 'Activité saine gardée', values: [{name: 'Krys', value: uc.numerator / uc.denominator, color: DEEP}, {name: 'Contrôles classiques', value: ub.numerator / ub.denominator, color: PALE}]},
      ], {max: 1, fmt: v => pct(v)});
      html += `<p class="tb-next">Pour aller plus loin : choisissez une mesure dans l'arborescence.</p>`;
      return html;
    },
    fraude() {
      const cases = byCase('fraud_eur', r => r.case !== 'propre');
      if (!cases.size) return unavailable('La fraude par scénario');
      const groups = [...cases].map(([c, v]) => ({label: caseName(c), values: SERIES.map(([k, name, color]) => ({name, value: mean(v[k]), color}))}));
      return head('Fraude au virement', "Les euros partis vers un IBAN détourné, par scénario, en moyenne sur les graines mesurées. Plus la barre est courte, mieux la banque est protégée.", {url: IMPACT + 'fraud.json', label: 'résultats par journée, graines 42 et 43'})
        + legend(SERIES) + bars(groups, {fmt: eur}) + table(['Scénario', 'Krys', 'Contrôles classiques'], groups.map(g => [g.label, eur(g.values[0].value), eur(g.values[1].value)]));
    },
    contamination() {
      const exec = byCase('contaminated_executed', r => r.case !== 'propre'), prop = byCase('contaminated_proposed', r => r.case !== 'propre');
      if (!exec.size) return unavailable('La contamination par scénario');
      const ratio = (c, k) => { const e = exec.get(c)[k].reduce((a, b) => a + b, 0), p = prop.get(c)[k].reduce((a, b) => a + b, 0); return p ? e / p : null; };
      const groups = [...exec.keys()].map(c => ({label: caseName(c), values: SERIES.map(([k, name, color]) => ({name, value: ratio(c, k), color}))}));
      return head('Opérations contaminées exécutées', "La part des opérations bâties sur une donnée compromise qui ont tout de même été exécutées, par scénario. Krys coupe la chaîne dès qu'elle remonte à une source suspecte.", {url: IMPACT + 'fixed.json', label: 'comptes et références des runs'})
        + legend(SERIES) + bars(groups, {max: 1, fmt: v => pct(v)}) + table(['Scénario', 'Krys', 'Contrôles classiques'], groups.map(g => [g.label, pct(g.values[0].value), pct(g.values[1].value)]));
    },
    detection() {
      const det = card('detection');
      if (!det) return unavailable('Le délai de détection');
      const groups = det.groups.map(g => ({label: g.label, values: SERIES.map(([, name, color]) => { const r = g.rows.find(x => x.label === name); return {name, value: r?.value == null ? null : Math.max(0, r.delay_range?.[1] ?? r.value - (g.injection_tick ?? 0)), color}; })}));
      return head('Délai de détection', "Le nombre de ticks entre le début de l'attaque et le premier constat, au pire des journées mesurées. Sur la faille connue, Krys signale la compromission avant l'attaque, dès la publication de la CVE : le délai vaut zéro.", {url: IMPACT + 'detections.json', label: 'ticks, absences de constat et message_id'})
        + legend(SERIES) + bars(groups, {fmt: v => `${num(v)} ticks`}) + table(['Scénario', 'Krys', 'Contrôles classiques'], det.groups.map(g => [g.label, ...['Krys', 'Contrôles classiques'].map(n => { const r = g.rows.find(x => x.label === n); return r?.value == null ? 'aucun constat' : `tick ${num(r.value)} (${r.observed}/${r.total} journées)`; })]));
    },
    activite() {
      const util = card('utility'), rows = SERIES.map(([, name, color]) => ({name, row: krysRows(util, name)[0], color}));
      if (!rows[0].row) return unavailable("L'activité saine");
      return head('Activité saine sous attaque', "La part des opérations saines exécutées pendant les journées d'attaque. Le frein a un coût : Krys refuse un peu plus d'opérations saines pour couper la contamination, et ce coût est mesuré.", {url: IMPACT + 'fixed.json', label: 'débit sain et référence'})
        + legend(SERIES) + bars([{label: 'Opérations saines exécutées', values: rows.map(r => ({name: r.name, value: r.row.numerator / r.row.denominator, color: r.color}))}], {max: 1, fmt: v => pct(v)})
        + table(['Banque', 'Exécutées', 'Référence', 'Part'], rows.map(r => [r.name, num(r.row.numerator), num(r.row.denominator), pct(r.row.numerator / r.row.denominator)]));
    },
    normales() {
      const m = DATA.normal?.metrics;
      if (!m) return unavailable('Les journées normales');
      const rows = SERIES.map(([k, name, color]) => ({name, m: m[k], color}));
      return head('Journées normales', "Dix journées sans attaque : une protection qui ne sert à rien les jours calmes ne doit rien coûter. On compte les opérations saines exécutées et les refus à tort.", {url: IMPACT + 'normal.json', label: 'refus, gels et dénominateur'})
        + `<div class="tb-tiles">${rows.map(r => tile(r.m ? pct(r.m.utility) : null, `d'activité saine · ${r.name}`, r.m ? `${num(r.m.fp_total)} refus à tort sur ${num(r.m.runs)} journées` : '')).join('')}</div>`
        + table(['Banque', 'Journées', 'Exécutées', 'Référence', 'Refus à tort'], rows.map(r => [r.name, num(r.m?.runs), num(r.m?.healthy_executed), num(r.m?.baseline_healthy_executed), num(r.m?.fp_total)]));
    },
    panne() {
      const res = DATA.outage?.results;
      if (!res) return unavailable('Le coût d\'une panne');
      const durations = [...new Set(res.map(r => r.duration_ticks))].sort((a, b) => a - b);
      const at = (d, relay) => mean(res.filter(r => r.duration_ticks === d && r.relay === relay).map(r => r.comparison?.unavailable_ticks ?? r.unavailable_ticks).filter(v => v != null));
      const series = [['avec', 'Avec le relais', DEEP], ['sans', 'Sans relais', PALE]];
      const groups = durations.map(d => ({label: `Coupure de ${d} ticks`, values: series.map(([k, name, color]) => ({name, value: at(d, k === 'avec'), color}))}));
      return head('Panne de Krys', "Si Krys s'arrête, plus rien d'irréversible ne passe : c'est sûr, mais l'activité s'arrête. Le relais reprend la main. On mesure combien de ticks la banque reste sans exécution après une coupure ; sans relais, la mesure s'arrête avant la reprise : c'est un minimum.", {url: IMPACT + 'outage.json', label: 'douze cas de panne et leurs témoins'})
        + legend(series) + bars(groups, {fmt: v => `${num(v)} ticks`}) + table(['Coupure', 'Avec le relais', 'Sans relais'], groups.map(g => [g.label, ...g.values.map(v => v.value == null ? '-' : `${num(v.value)} ticks`)]));
    },
    arbitrage() {
      const c = DATA.curve;
      if (!Array.isArray(c)) return unavailable("L'arbitrage sûreté et utilité");
      const series = [{name: 'Contaminées exécutées', color: PALE, points: c.map(p => [p.margin, p.attack.asr])},
        {name: 'Activité saine gardée', color: DEEP, points: c.map(p => [p.margin, p.attack.utility])}];
      return head('Sûreté et utilité', "Chaque marge du seuil de Krys déplace l'équilibre entre ce qu'elle laisse passer de contaminé et ce qu'elle garde d'activité saine. La marge livrée est 0 ; aucun seuil ne change depuis cet écran.", {url: '/console/details/data/curve.json', label: 'points et références'})
        + legend(series.map(s => [0, s.name, s.color])) + lines(series, {xfmt: v => `marge ${num(v, 2)}`, yfmt: v => pct(v, 0), ymax: 1, marks: [{at: 0, name: 'livrée'}]})
        + table(['Marge', 'Contaminées exécutées', 'Activité saine'], c.map(p => [num(p.margin, 2), pct(p.attack.asr), pct(p.attack.utility)]));
    },
    portique() {
      const s = LIVE.snapshot;
      if (!s?.live) return liveMissing();
      const d = s.S.decisions || {}, rows = [['Exécutions approuvées', d['execution:executed']], ['Exécutions refusées', d['execution:refused']],
        ['Décisions de risque approuvées', d['risk_decision:approved']], ['Décisions de risque rejetées', d['risk_decision:rejected']], ['Visas de conformité', d['compliance_decision:approved']]];
      return head('Portique', "Chaque ordre et chaque virement passe devant le portique de Krys avant d'être exécuté. Les comptes depuis le début de la journée.")
        + bars(rows.filter(r => r[1] != null).map(([label, value]) => ({label, values: [{name: label, value, color: DEEP}]})), {fmt: v => num(v)})
        + table(['Décision', 'Nombre'], rows.map(([l, v]) => [l, v == null ? '-' : num(v)]));
    },
    contagion() {
      const s = LIVE.snapshot;
      if (!s?.live) return liveMissing();
      const st = s.status, kinds = new Map();
      for (const f of s.findings) kinds.set(f.kind, (kinds.get(f.kind) ?? 0) + 1);
      return head('Contagion', "Ce que Krys publie sur la propagation d'une faille : les services qu'elle isole, les actifs gelés, et le nombre d'agents qui agissent sur chaque message empoisonné (R0).")
        + `<div class="tb-tiles">${tile(st ? num(st.suspects?.length ?? 0) : null, 'services isolés', st?.suspects?.join(', ') ?? '')}${tile(st ? num(st.frozen?.length ?? 0) : null, 'actifs gelés', st?.frozen?.join(', ') ?? '')}${tile(st?.r0 != null ? num(st.r0, 1) : null, 'R0 de la contagion', 'agents par message empoisonné')}</div>`
        + (kinds.size ? `<h3>Constats récents par type</h3>` + bars([...kinds].map(([k, n]) => ({label: String(k).replaceAll('_', ' '), values: [{name: k, value: n, color: DEEP}]})), {fmt: v => num(v)}) : '<p class="tb-empty">Aucun constat publié pour l\'instant.</p>');
    },
    limites() {
      const s = LIVE.snapshot;
      if (!s?.live) return liveMissing();
      const L = s.S.limits || {}, names = {exposure_pct: 'Exposition', var_pct: 'Valeur en risque', daily_loss_pct: 'Perte du jour', liquidity_floor_pct: 'Plancher de trésorerie', daily_pct: 'Virements du jour', max_position_pct: 'Plus grosse position'};
      const groups = [];
      for (const [k, v] of Object.entries(L.bank || {})) if (names[k]) groups.push({label: `Banque · ${names[k]}`, v});
      for (const [k, v] of Object.entries(L.payments || {})) if (names[k]) groups.push({label: `Paiements · ${names[k]}`, v});
      for (const [t, m] of Object.entries(L.traders || {})) for (const [k, v] of Object.entries(m)) if (names[k]) groups.push({label: `${t} · ${names[k]}`, v});
      return head('Limites', "La part de chaque limite déjà utilisée. À 80 % la limite approche, à 100 % elle est atteinte : la couleur le dit, le chiffre aussi.")
        + bars(groups.map(g => ({label: g.label, values: [{name: g.label, value: g.v, color: g.v >= 100 ? '#E0412F' : g.v >= 80 ? '#D97706' : DEEP}]})), {max: Math.max(100, ...groups.map(g => g.v)), fmt: v => `${num(v, 1)} %`, limits: [{at: 80, name: '80 %'}, {at: 100, name: '100 %'}]})
        + table(['Limite', 'Utilisée'], groups.map(g => [g.label, `${num(g.v, 1)} %`]));
    },
    resultat() {
      const s = LIVE.snapshot;
      if (!s?.live) return liveMissing();
      return head('Résultat de la journée', "Le résultat de la banque protégée par Krys, trading et fraude compris, relevé à chaque tick depuis l'ouverture de cette page.")
        + `<div class="tb-tiles">${tile(eur(s.S.pnl?.day_eur), 'résultat du jour', `dont ${eur(s.S.pnl?.payment_fraud_eur)} de fraude au virement`)}</div>`
        + lines([{name: 'Résultat du jour', color: DEEP, points: LIVE.pnl}], {xfmt: v => `tick ${num(v)}`, yfmt: eur, ymin: Math.min(0, ...LIVE.pnl.map(p => p[1]))});
    },
    conformite() {
      const c = DATA.conformite;
      if (!c?.items) return unavailable('La matrice de conformité');
      const words = {couvert: 'Couvert dans la démo', partiel: 'Partiel', 'hors-perimetre': 'Hors périmètre'}, count = s => c.items.filter(i => i.status === s).length;
      return head('Conformité', "Où en est Argus face à DORA, à l'AI Act et à MiFID RTS 6 : ce qui est exercé dans la démo, ce qui reste à compléter, ce qui relève du déployeur.", {url: '/regulation#conformite', label: 'la matrice complète, dans la Régulation'})
        + `<div class="tb-tiles">${Object.keys(words).map(s => tile(num(count(s)), words[s])).join('')}</div>`
        + `<ul class="tb-list">${c.items.map(i => `<li><span class="tb-state tb-${esc(i.status)}">${esc(words[i.status] ?? i.status)}</span> ${esc(i.titre)} <small>${esc(i.article)}</small></li>`).join('')}</ul>`;
    },
  };
  const liveMissing = () => '<p class="tb-empty">Aucune démo ne tourne : ces mesures se lisent en direct. Lancez <code>make demo-live</code>.</p>';

  // --- the page: the tree, its keyboard, the address -------------------------------------------------------------
  root.innerHTML = `<nav class="tb-tree" aria-label="Arborescence des mesures"></nav><div class="tb-page" tabindex="-1" aria-live="polite"></div>`;
  const nav = root.querySelector('.tb-tree'), view = root.querySelector('.tb-page');
  const open = new Set(TREE.filter(n => n.children).map(n => n.id));
  function drawTree() {
    nav.innerHTML = '<ul>' + TREE.map(n => n.children
      ? `<li><button type="button" class="tb-group" data-group="${n.id}" aria-expanded="${open.has(n.id)}">${esc(n.name)}</button>${open.has(n.id) ? `<ul>${n.children.map(c => leaf(c)).join('')}</ul>` : ''}</li>`
      : `<li>${leaf(n)}</li>`).join('') + '</ul>';
  }
  const leaf = n => `<a href="#tableau/${n.id}" data-leaf="${n.id}"${n.id === page ? ' aria-current="page"' : ''}>${esc(n.name)}</a>`;
  function show(id, focus = false) {
    page = leaves.some(l => l.id === id) ? id : 'apercu';
    const parent = leaves.find(l => l.id === page)?.parent;
    if (parent) open.add(parent.id);
    drawTree();
    try { view.innerHTML = PAGES[page](); } catch (_) { view.innerHTML = unavailable('Cette mesure'); }
    if (focus) view.focus();
  }
  nav.addEventListener('click', e => {
    const g = e.target.closest('[data-group]'), l = e.target.closest('[data-leaf]');
    if (g) { open.has(g.dataset.group) ? open.delete(g.dataset.group) : open.add(g.dataset.group); drawTree(); nav.querySelector(`[data-group="${g.dataset.group}"]`).focus(); }
    if (l) { e.preventDefault(); history.replaceState(null, '', location.pathname + location.search + '#tableau/' + l.dataset.leaf); show(l.dataset.leaf); nav.querySelector(`[data-leaf="${l.dataset.leaf}"]`).focus(); }
  });
  // up and down move along the tree, right and left open and close a group
  nav.addEventListener('keydown', e => {
    const items = [...nav.querySelectorAll('button, a')], i = items.indexOf(document.activeElement);
    if (i < 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
    const g = document.activeElement.dataset.group;
    if (g && (e.key === 'ArrowRight' || e.key === 'ArrowLeft')) { e.preventDefault(); e.key === 'ArrowRight' ? open.add(g) : open.delete(g); drawTree(); nav.querySelector(`[data-group="${g}"]`).focus(); }
  });
  const fromHash = () => { const [view0, sub] = location.hash.slice(1).split('/'); if (view0 === 'tableau' && sub) show(sub); };
  addEventListener('hashchange', fromHash);

  // the live part, fed by dashboard.js at each of its redraws
  window.tableauLive = snapshot => {
    LIVE.snapshot = snapshot;
    const tick = snapshot.S?.tick, value = snapshot.S?.pnl?.day_eur;
    if (snapshot.live && tick != null && value != null) {
      if (LIVE.pnl.length && tick < LIVE.pnl.at(-1)[0]) LIVE.pnl = [];
      if (!LIVE.pnl.length || LIVE.pnl.at(-1)[0] !== tick) LIVE.pnl.push([tick, value]);
    }
    // a live page is drawn again on a new tick only: an open table of figures stays open between two ticks
    const fresh = `${snapshot.live}:${tick}`;
    if (fresh === LIVE.drawn) return;
    LIVE.drawn = fresh;
    if (leaves.find(l => l.id === page)?.parent?.id === 'direct' && !document.getElementById('tableau').hidden) show(page);
  };

  const get = url => fetch(url, {cache: 'no-store'}).then(r => (r.ok ? r.json() : null), () => null);
  Promise.all([get(IMPACT + 'data.json'), get(IMPACT + 'fixed.json'), get(IMPACT + 'fraud.json'), get(IMPACT + 'normal.json'), get(IMPACT + 'outage.json'),
    get('/console/details/data/curve.json'), get('/console/conformite/data/conformite.json')])
    .then(([impact, fixed, fraud, normal, outage, curve, conformite]) => {
      Object.assign(DATA, {impact, fixed, fraud, normal, outage, curve, conformite});
      show(page); fromHash();
    });
  view.innerHTML = '<p class="tb-empty">Chargement des mesures…</p>';
  drawTree();
})();
