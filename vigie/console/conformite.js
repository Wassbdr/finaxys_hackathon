/* Les statuts sont documentaires : ce module ne lit ni l'état de la banque,
   ni ses annotations, et ne modifie aucun verdict du portique. */
(function () {
  'use strict';
  const BASE = '/console/conformite/';
  const STATUS = {couvert: ['✅ Couvert dans la démo', 'couvert'], partiel: ['🟡 Partiel', 'partiel'], 'hors-perimetre': ['❌ Hors périmètre', 'hors-perimetre']};
  function node(doc, tag, text, className) {
    const el = doc.createElement(tag);
    if (text != null) el.textContent = String(text);
    if (className) el.className = className;
    return el;
  }
  function link(doc, text, href) {
    const el = node(doc, 'a', text);
    const url = new URL(href, 'http://console.local');
    if ((url.origin === 'http://console.local' && url.pathname.startsWith(BASE))
        || (url.protocol === 'https:' && url.hostname === 'eur-lex.europa.eu')) {
      el.href = url.origin === 'http://console.local' ? url.pathname : url.href;
      el.target = '_blank'; el.rel = 'noopener noreferrer';
    }
    return el;
  }
  function paragraph(doc, label, text) {
    const p = node(doc, 'p'); p.append(node(doc, 'strong', label + ' '), node(doc, 'span', text)); return p;
  }
  function render(root, data, compact = false) {
    const doc = root.ownerDocument;
    // Le résumé natif reste manipulable et le reste du contenu n'est jamais du HTML tiers.
    if (compact) root.replaceChildren(node(doc, 'summary', 'Conformité — preuves et limites'));
    else root.replaceChildren(node(doc, 'h1', data.titre));
    root.classList.add('conformite');
    root.append(node(doc, 'p', data.scope, 'conformite-lead'));
    if (!compact) {
      root.append(node(doc, 'p', 'Lecture : couvert = fonction exercée dans la démo ; partiel = un dossier reste à compléter ; hors périmètre = responsabilité externe.'));
      const grid = node(doc, 'div', null, 'conformite-grid');
      for (const item of data.items) {
        const article = node(doc, 'article', null, 'conformite-card');
        const status = STATUS[item.status] || STATUS.partiel;
        article.append(node(doc, 'span', status[0], 'conformite-status ' + status[1]), node(doc, 'h2', item.titre));
        article.append(link(doc, item.article, item.source || BASE + 'preuves/mesure'));
        article.append(paragraph(doc, 'Argus :', item.fait), paragraph(doc, 'Contrôles classiques :', item.classiques));
        article.append(paragraph(doc, 'Limite :', item.limite));
        article.append(link(doc, 'Preuve : ' + item.preuve_label, BASE + 'preuves/' + item.preuve));
        grid.append(article);
      }
      root.append(grid);
    } else {
      root.append(node(doc, 'p', 'Journal signé : couvert dans la démonstration. Notification DORA et contrôle humain : partiels. Organisation du déployeur : hors périmètre.'));
      root.append(node(doc, 'p', 'Les contrôles classiques reproduisent déjà les contrôles avant ordre et l’arrêt. Limite : ni qualification réglementaire ni borne universelle de perte ne sont démontrées.'));
      const full = node(doc, 'a', 'Ouvrir la matrice complète dans la console'); full.href = '/dashboard#conformite'; root.append(full);
    }
    root.append(paragraph(doc, 'Calendrier :', data.calendar));
    root.append(link(doc, data.calendar_note, data.calendar_source));
    root.append(node(doc, 'p', data.method));
    root.append(node(doc, 'p', data.measurement.sample + ' — commit mesuré ' + data.measurement.commit + ' ; livraison ' + data.measurement.delivery_commit, 'conformite-method'));
    root.append(link(doc, 'Mesures publiées et dénominateurs — JSON', BASE + data.measurement.file));
    const words = node(doc, 'details'); words.append(node(doc, 'summary', 'Lexique'));
    const glossary = {'DORA': 'Règles européennes sur la résistance aux incidents informatiques du secteur financier.',
      'AI Act': 'Règlement européen sur les systèmes d’intelligence artificielle.',
      'MiFID II / RTS 6': 'Règles des marchés financiers et exigences techniques du trading algorithmique.',
      'Portique': 'Contrôle qui autorise ou refuse une opération avant son exécution.',
      'Déployeur': 'Organisation qui utilise le système sous sa responsabilité.',
      'Journal signé et chaîné': 'Messages authentifiés et reliés par empreintes pour rendre une modification détectable.'};
    for (const [term, meaning] of Object.entries(glossary)) {
      const abbr = node(doc, 'abbr', term); abbr.title = meaning;
      const line = node(doc, 'p'); line.append(abbr, node(doc, 'span', ' — ' + meaning)); words.append(line);
    }
    root.append(words, draftPanel(doc));
  }
  function draftPanel(doc) {
    const panel = node(doc, 'div', null, 'conformite-draft');
    panel.append(node(doc, 'h2', 'Brouillon à valider par un humain'));
    panel.append(node(doc, 'p', 'Reprise du brouillon existant de Krys. Aucun envoi à une autorité. Qualification, dates, destinataire et délai restent à compléter.'));
    const label = node(doc, 'label', 'Jeton de lancement de la démo ');
    const token = node(doc, 'input'); token.type = 'password'; token.autocomplete = 'off'; token.maxLength = 256;
    label.append(token);
    const button = node(doc, 'button', 'Préparer la notification DORA'); button.type = 'button';
    const output = node(doc, 'pre', '', 'conformite-output'); output.setAttribute('aria-live', 'polite');
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      try { await prepareDraft(output, token.value, globalThis.fetch.bind(globalThis)); }
      finally { button.disabled = false; }
    });
    panel.append(label, button, output); return panel;
  }
  async function prepareDraft(output, token, fetcher) {
    output.textContent = '';
    if (!token || !token.trim()) { output.textContent = 'Jeton de lancement requis. Ouvrez la démo locale et utilisez son jeton.'; return; }
    try {
      // La question est constante ; ni les cases de couverture ni les mesures
      // du simulateur ne servent de connaissance au brouillon opérationnel.
      const response = await fetcher('/demo/krys/ask', {method: 'POST', credentials: 'same-origin', redirect: 'error',
        headers: {'Content-Type': 'application/json', 'X-Demo-Token': token}, body: JSON.stringify({question: 'Brouillon DORA'})});
      if (!response.ok) throw new Error('unavailable');
      const answer = await response.json();
      const citations = Array.isArray(answer.citations) ? answer.citations.map(c => c.message_id || c.section_id).filter(Boolean) : [];
      output.textContent = 'BROUILLON À VALIDER PAR UN HUMAIN\n' + String(answer.text || 'Aucune source disponible.')
        + (citations.length ? '\nPreuves : ' + citations.join(', ') : '\nAucune preuve opérationnelle disponible ; ne pas déclarer cet élément.');
    } catch (_) { output.textContent = 'Brouillon indisponible. Ouvrez /demo/page, vérifiez le jeton et les sources signées. Aucun envoi effectué.'; }
  }
  function decorate(root, badges) {
    for (const row of root.querySelectorAll('tbody tr')) {
      const cell = row.querySelector('td');
      if (!cell || cell.querySelector('.conformite-badges')) continue;
      const group = node(root.ownerDocument, 'span', null, 'conformite-badges');
      for (const badge of badges) {
        const a = node(root.ownerDocument, 'a', badge.label, 'conformite-badge');
        a.href = '#conformite'; a.title = badge.why; a.setAttribute('aria-label', badge.label + ' — ' + badge.why);
        group.append(a);
      }
      cell.append(group);
    }
  }
  async function mount(doc, fetcher) {
    const full = doc.getElementById('conformite'), compact = doc.getElementById('conformite-resume');
    if (!full && !compact) return;
    const style = node(doc, 'link'); style.rel = 'stylesheet'; style.href = BASE + 'conformite.css'; doc.head.append(style);
    try {
      const response = await fetcher(BASE + 'data/conformite.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('data');
      const data = await response.json();
      if (full) render(full, data);
      if (compact) render(compact, data, true);
      // Les sections natives sont recréées à chaque tick. Observer seulement
      // leur DOM évite d'ajouter une dépendance au code partagé de Marius.
      if (full) for (const id of ['actions', 'metrics', 'summary']) {
        const area = doc.getElementById(id);
        if (!area) continue;
        decorate(area, data.badges);
        new MutationObserver(() => decorate(area, data.badges)).observe(area, {childList: true, subtree: true});
      }
    } catch (_) {
      const root = full || compact;
      root.append(node(doc, 'p', 'Éléments de preuve indisponibles ; aucun statut de couverture ne peut être affiché.'));
    }
  }
  if (typeof module !== 'undefined') module.exports = {render, prepareDraft, decorate, mount, link};
  if (typeof document !== 'undefined') mount(document, globalThis.fetch.bind(globalThis));
})();
