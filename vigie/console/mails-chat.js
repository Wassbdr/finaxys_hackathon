/* Les preuves et réponses restent du texte. La bulle monte hors de main :
   changer de vue ne ferme pas une conversation ou son récapitulatif humain.
   Le chat est celui de la vue Krys, posé dans la bulle tant qu’elle est ouverte :
   il n’existe qu’une fois, lié une seule fois par /demo/krys.js. */
(function () {
  'use strict';
  const root = document.getElementById('mails-chat');
  if (!root) return;
  function node(tag, text, attributes = {}) {
    const element = document.createElement(tag);
    if (text !== null) element.textContent = text;
    for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value);
    return element;
  }
  function append(parent, ...children) { children.forEach(child => parent.appendChild(child)); return parent; }
  const css = node('link', null, {rel: 'stylesheet', href: '/console/mails-chat/mails-chat.css'});
  document.head.appendChild(css);
  root.classList.add('console-mails-chat');
  const status = node('p', 'Lecture du journal signé…', {role: 'status', 'aria-live': 'polite'});
  const list = node('div', null, {class: 'mc-drafts'});
  const refresh = node('button', 'Actualiser', {type: 'button'});
  append(root, node('h1', 'Boîte d’envoi'),
    node('p', 'Un brouillon par incident de la banque Krys, regroupé à partir des alertes et refus signés. Aucune notification automatique.'),
    node('p', 'Contrôles classiques : comparaison non mesurée ici. Limite : les brouillons ne constituent pas une notification réglementaire.'),
    refresh, status, list);

  // Réutiliser le chat de la vue Krys conserve les citations, le nonce à usage unique
  // et l’invalidation du récapitulatif si le contexte du formulaire change.
  const chat = document.getElementById('krys-chat');
  const home = chat ? chat.parentNode : null;
  const view = document.getElementById('krys');
  const launcher = node('button', 'Demandez à Krys', {id: 'mc-open', type: 'button',
    'aria-expanded': 'false', 'aria-controls': 'mc-chat'});
  const panel = node('div', null, {id: 'mc-chat', role: 'dialog', 'aria-labelledby': 'krys-heading', hidden: ''});
  const close = node('button', 'Fermer', {type: 'button', class: 'mc-close', 'aria-label': 'Fermer le chat'});
  append(panel, append(node('div', null, {class: 'mc-header'}), close));
  append(document.body, launcher, panel);
  function openChat(open) {
    if (!chat) return;
    (open ? panel : home).appendChild(chat);
    panel.hidden = !open;
    launcher.setAttribute('aria-expanded', String(open));
    if (open) document.getElementById('krys-question').focus();
    else if (!launcher.hidden) launcher.focus();
  }
  launcher.addEventListener('click', () => openChat(panel.hidden));
  close.addEventListener('click', () => openChat(false));
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') { event.preventDefault(); openChat(false); } });
  // Sur la vue Krys, le chat est chez lui : la bulle s’efface et le lui rend.
  function dock() {
    const docked = !!view && !view.hidden;
    launcher.hidden = docked || !chat;
    if (docked && !panel.hidden) openChat(false);
  }
  if (view) new MutationObserver(dock).observe(view, {attributes: true, attributeFilter: ['hidden']});
  dock();

  async function send(item, button) {
    button.disabled = true;
    try {
      const response = await fetch('/console/mails-chat/send', {method: 'POST', credentials: 'omit', redirect: 'error',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({incident_id: item.id})});
      const result = await response.json();
      status.textContent = typeof result.detail === 'string' ? result.detail : 'Réponse du transport indisponible.';
    } catch (_) { status.textContent = 'Envoi incertain ; aucune relance automatique.'; }
    // Un clic répété n’est pas une stratégie de relance après résultat incertain.
  }
  let busy = false;
  async function reload() {
    if (busy) return;
    busy = true;
    try {
      const response = await fetch('/console/mails-chat/outbox', {credentials: 'omit', redirect: 'error'});
      if (!response.ok) throw new Error('unavailable');
      const result = await response.json();
      const items = Array.isArray(result.items) ? result.items : [];
      list.replaceChildren();
      status.textContent = items.length + ' incident(s) affiché(s) dans ce journal. ' +
        (result.smtp_enabled ? 'SMTP activé ; envoi manuel vers l’unique adresse configurée.' : 'Mode démonstration : aucun mail envoyé.') +
        (result.complete ? '' : ' Journal incomplet ; envoi refusé.');
      if (!items.length) list.appendChild(node('p', 'Aucun incident dans les observations disponibles.'));
      for (const item of items) {
        const card = node('article', null, {class: 'mc-card'});
        append(card, node('span', 'Brouillon à examiner', {class: 'mc-state'}), node('h2', item.subject),
          node('p', item.impact), node('p', item.period), node('p', item.action),
          node('p', item.comparison), node('p', item.limit));
        const proof = append(node('details', null), node('summary', 'Preuves · message_id'));
        for (const mid of item.evidence || []) proof.appendChild(node('code', mid));
        // L’URL reste une constante locale : même un lien forgé dans une réponse
        // ne devient pas une navigation javascript: ou une sortie vers un tiers.
        append(card, proof, node('a', 'Ouvrir le journal et poser une question', {href: '/console#krys'}));
        const ask = node('button', 'Examiner avec Krys', {type: 'button'});
        ask.addEventListener('click', () => {
          document.getElementById('krys-question').value = 'Pourquoi ' + String((item.evidence || [])[0] || '') + ' ?';
          if (view && !view.hidden) document.getElementById('krys-question').focus();
          else openChat(true);
        });
        card.appendChild(ask);
        if (result.smtp_enabled) {
          const button = node('button', 'Envoyer à l’adresse configurée', {type: 'button'});
          button.disabled = !result.complete;
          button.addEventListener('click', () => send(item, button));
          card.appendChild(button);
        }
        list.appendChild(card);
      }
    } catch (_) {
      status.textContent = 'Boîte indisponible. Lancez la console via demo-live ou configurez son journal signé. Aucun mail envoyé.';
    } finally { busy = false; }
  }
  refresh.addEventListener('click', reload);
  reload();
  setInterval(() => { if (!root.hidden && !document.hidden) reload(); }, 10000);
}());
