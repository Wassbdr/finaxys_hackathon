/* Les mesures sont des archives : ce module ne reçoit ni état vivant ni pouvoir de commande. */
(function (root, factory) {
  const impact = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = impact;
  else impact.start(root.document, root.fetch.bind(root));
})(globalThis, function () {
  'use strict';
  const prefix = '/dashboard/console/impact/';
  const proofs = new Set(['data.json', 'fixed.json', 'normal.json', 'crash.json', 'detections.json',
    'fraud.json', 'outage.json', 'manifest.json', 'limits.md', 'outage-method.md']);
  const number = (n, digits = 0) => new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: digits, maximumFractionDigits: digits
  }).format(n);
  const ratio = (n, d) => d > 0 ? `${number(100 * n / d, 2)} %` : 'non défini';
  const signed = n => `${n < 0 ? '−' : '+'}${number(Math.abs(n))}`;
  const range = (values, format = number) => values[0] === values[1] ? format(values[0]) : `${format(values[0])} à ${format(values[1])}`;

  // Les mesures archivées nomment encore « Sans Krys » la banque sans protection : seul le texte affiché change.
  const shown = text => String(text).replace(/\bSans Krys\b/g, 'Sans protection');
  function node(doc, tag, text, cls) {
    const result = doc.createElement(tag);
    if (text !== undefined && text !== null) result.textContent = shown(text);
    if (cls) result.className = cls;
    return result;
  }
  function safeProof(url) {
    return typeof url === 'string' && url.startsWith(prefix) && proofs.has(url.slice(prefix.length)) ? url : prefix + 'manifest.json';
  }
  function link(doc, proof) {
    const a = node(doc, 'a', proof.label);
    a.href = safeProof(proof.url);
    return a;
  }
  function glossary(doc, text, explanation) {
    const abbr = node(doc, 'abbr', text);
    abbr.title = explanation;
    abbr.tabIndex = 0;
    return abbr;
  }
  function valueText(row) {
    if (row.unit === 'ticks') {
      if (row.value === null) return `aucun constat (${row.observed}/${row.total})`;
      return `t${range(row.tick_range)} · ${row.observed}/${row.total} journées · délai ${range(row.delay_range, signed)} ticks`;
    }
    if (row.unit === 'euros_ratio') return `${ratio(row.numerator, row.denominator)} · ${number(row.numerator, 2)} € simulés`;
    const counts = `${number(row.numerator)} / ${number(row.denominator)}`;
    return row.unit === 'ratio' ? `${counts} · ${ratio(row.numerator, row.denominator)}` : counts;
  }
  // Sur la page Simulation, deux banques seulement : la banque sans protection reste dans les archives, pas à l'écran.
  const SHOWN_BANKS = row => row.label !== 'Sans Krys';
  function group(doc, data, two = false) {
    const box = node(doc, 'div', null, 'impact-comparison');
    box.append(node(doc, 'h4', data.label));
    const ticks = data.injection_tick !== undefined;
    if (ticks) box.append(node(doc, 'p', `Injection à t${data.injection_tick} ; barre = tick médian du constat, parmi les journées avec constat.`, 'impact-axis'));
    const maximum = Math.max(1, ...data.rows.map(row => Number.isFinite(row.value) ? row.value : 0));
    for (const row of two ? data.rows.filter(SHOWN_BANKS) : data.rows) {
      const item = node(doc, 'div', null, 'impact-row');
      item.append(node(doc, 'span', row.label, 'impact-bank'));
      item.append(node(doc, 'span', valueText(row), 'impact-value'));
      const bar = node(doc, 'div', null, 'impact-bar');
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', shown(`${row.label} : ${valueText(row)}`));
      const ink = node(doc, 'i');
      /* Une valeur absente reste absente : aucune barre à zéro inventée pour « non détecté ». */
      if (row.value === null) { bar.classList.add('impact-undefined'); ink.style.width = '0%'; }
      else ink.style.width = `${Math.min(100, Math.max(0, row.value / maximum * 100))}%`;
      bar.append(ink); item.append(bar); box.append(item);
    }
    return box;
  }
  function card(doc, data, terms, two = false) {
    const article = node(doc, 'article', null, 'impact-card');
    article.id = `impact-${data.id}`;
    const head = node(doc, 'div', null, 'impact-card-head');
    head.append(node(doc, 'h3', data.title));
    head.append(node(doc, 'span', `mesuré · ${data.measured_commit.slice(0, 7)}`, 'impact-stamp'));
    article.append(head, node(doc, 'p', data.sample, 'impact-sample'));
    for (const comparison of data.groups) article.append(group(doc, comparison, two));
    if (data.id === 'contamination') {
      article.append(glossary(doc, 'Donnée contaminée', terms.contamination));
    }
    if (data.bound && !two) {
      article.append(node(doc, 'p', `Refus explicites : sans protection ${data.refusals.A}, contrôles classiques ${data.refusals.B}, Krys ${data.refusals.C}.`, 'impact-note'));
      const bound = node(doc, 'p', null, 'impact-note');
      bound.append(glossary(doc, 'Borne conditionnelle à 95 %',
        'Règle de trois : après zéro refus sur n opérations indépendantes, borne supérieure approximative 3/n ; elle ne prouve pas l’indépendance des opérations de ces journées.'));
      bound.append(node(doc, 'span', ` : 3/${data.bound.denominator} ≈ ${ratio(3, data.bound.denominator)}, soit moins d’environ 1 % par opération si les essais étaient indépendants.`));
      article.append(bound);
    }
    if (data.id === 'detection') article.append(node(doc, 'p', `Contrôles classiques : premier constat une fois le premier virement volé dans ${data.classical_after_theft_days}/10 journées PHP2Shell ; aucun constat du registre altéré dans 10/10 journées, ni de la rumeur dans 6/10.`, 'impact-note'));
    if (data.id === 'fraud' && !two) article.append(node(doc, 'p', `Dénominateur commun : ${number(data.groups[0].rows[0].denominator, 2)} € simulés volés sans Krys, sur les deux journées.`, 'impact-note'));
    if (data.id === 'outage') article.append(node(doc, 'p', `Indisponibilité : sans relais ≥ ${data.unavailable_without_relay_min} ticks (fin d’observation sans reprise), avec relais ${data.unavailable_with_relay_max} tick dans les six cas.`, 'impact-note'));
    article.append(node(doc, 'p', `Ce que cela veut dire — ${data.meaning}`, 'impact-meaning'));
    article.append(node(doc, 'p', `Limite — ${data.limit}`, 'impact-limit'));
    const evidence = node(doc, 'div', null, 'impact-proofs');
    for (const source of data.proofs) evidence.append(link(doc, source));
    article.append(evidence);
    return article;
  }
  function render(doc, target, data, compact = false) {
    target.replaceChildren();
    if (compact) {
      const heading = node(doc, 'a', 'Impact · mesures archivées et limites');
      heading.href = '/console#impact';
      const counts = data.cards[0].groups[0].rows;
      const utility = data.cards[1].groups[0].rows;
      target.append(heading, node(doc, 'p', `6 attaques × 10 journées simulées : opérations contaminées exécutées / proposées — ${counts.map(r => `${r.label} ${number(r.numerator)}/${number(r.denominator)}`).join(' ; ')}.`));
      target.append(node(doc, 'p', `Opérations saines, référence ${utility[0].denominator} : contrôles classiques ${ratio(utility[1].numerator, utility[1].denominator)} ; Krys ${ratio(utility[2].numerator, utility[2].denominator)}. Limite : ce débit ne mesure pas un gain financier. Commit ${data.cards[0].measured_commit.slice(0, 7)}.`));
      return;
    }
    target.append(node(doc, 'h2', 'Impact : ce que les essais montrent'));
    target.append(node(doc, 'p', 'Argus · banque simulée. Ces chiffres archivés ne changent pas avec le direct.', 'lead'));
    const grid = node(doc, 'div', null, 'impact-grid');
    const two = target.dataset && target.dataset.banks === 'deux';
    for (const item of data.cards) grid.append(card(doc, item, data.glossary, two));
    const foot = node(doc, 'footer', null, 'impact-method');
    foot.append(node(doc, 'p', data.method));
    foot.append(glossary(doc, 'Opération saine', data.glossary.healthy));
    foot.append(glossary(doc, 'Refus à tort', data.glossary.false_positive));
    for (const source of data.proofs) foot.append(link(doc, source));
    target.append(grid, foot);
  }
  function start(doc, fetcher) {
    const full = doc.getElementById('impact'), summary = doc.getElementById('impact-summary');
    if (!full && !summary) return;
    const css = node(doc, 'link'); css.rel = 'stylesheet'; css.href = '/dashboard/console/impact.css'; doc.head.append(css);
    for (const target of [full, summary].filter(Boolean)) target.textContent = 'Chargement des mesures archivées…';
    fetcher(prefix + 'data.json', {method: 'GET', credentials: 'same-origin', cache: 'no-cache'})
      .then(response => { if (!response.ok) throw new Error('Mesures indisponibles'); return response.json(); })
      .then(data => {
        if (data.schema_version !== 1 || !Array.isArray(data.cards) || data.cards.length !== 6) throw new Error('Format de mesure inconnu');
        if (full) render(doc, full, data);
        if (summary) render(doc, summary, data, true);
      }).catch(() => {
        for (const target of [full, summary].filter(Boolean)) {
          target.replaceChildren(node(doc, 'p', 'Mesures archivées indisponibles. Aucun chiffre de remplacement n’est affiché.'));
          target.append(link(doc, {label: 'Origine des mesures', url: prefix + 'manifest.json'}));
        }
      });
  }
  return {render, start, safeProof};
});
