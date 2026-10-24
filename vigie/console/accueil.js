/* Le moteur existant garde l'unique chemin de questions et de confirmations. */
(function () {
  'use strict';
  const byId = id => document.getElementById(id);
  byId('argus-phrase').textContent = document.querySelector('meta[name="argus-phrase"]').content;
  // La page Régulation de l'équipe arrive plus tard : seule une adresse web ou un chemin local remplace le repli.
  const regulation = (document.querySelector('meta[name="argus-regulation-url"]') || {}).content || '';
  if (/^(https:\/\/|\/(?!\/))/.test(regulation)) {
    byId('regulation-link').href = regulation;
    byId('regulation-link').removeAttribute('aria-disabled');
    byId('regulation-note').textContent = 'DORA, AI Act, MiFID II';
  }
  const question = byId('krys-question');
  for (const suggestion of document.querySelectorAll('[data-question]')) {
    suggestion.addEventListener('click', () => {
      question.value = suggestion.dataset.question;
      question.focus();
    });
  }
  const menu = byId('nav-toggle');
  menu.addEventListener('click', () => {
    const expanded = menu.getAttribute('aria-expanded') === 'true';
    menu.setAttribute('aria-expanded', String(!expanded));
    byId('console-nav').hidden = expanded;
  });
  const mobile = matchMedia('(max-width: 760px)');
  function adaptMenu() {
    byId('console-nav').hidden = mobile.matches;
    menu.setAttribute('aria-expanded', String(!mobile.matches));
  }
  adaptMenu();
  mobile.addEventListener('change', adaptMenu);

  new MutationObserver(() => {
    byId('krys-log').scrollTop = byId('krys-log').scrollHeight;
  }).observe(byId('krys-log'), {childList: true});

  /* L'onglet Impact arrive dans un lot distinct : le repli est une ancre réelle. */
  fetch('/dashboard', {credentials: 'omit', redirect: 'error'})
    .then(response => response.ok ? response.text() : '')
    .then(html => {
      const dashboard = new DOMParser().parseFromString(html, 'text/html');
      if (dashboard.querySelector('section#impact') && dashboard.querySelector('.tabs a[data-tab="impact"]')) {
        byId('metrics-link').href = '/dashboard#impact';
      }
    }).catch(() => { /* La navigation statique reste utilisable sans API. */ });

  /* Seul l'état de disponibilité est affiché ; aucune donnée bancaire ne rejoint le chat. */
  fetch('/demo', {credentials: 'omit', redirect: 'error'})
    .then(response => response.ok ? response.json() : null)
    .then(frame => {
      if (frame && Number.isInteger(frame.tick) && frame.tick >= 0) {
        byId('proof-status').textContent = 'Démo active. Les réponses de Krys restent limitées aux preuves signées disponibles.';
      }
    }).catch(() => { /* Le texte d'attente initial couvre aussi la démo non lancée. */ });
}());
