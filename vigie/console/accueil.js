/* Le moteur existant garde l'unique chemin de questions et de confirmations. La vue Krys de la console n'ajoute que
   la phrase, les suggestions, le défilement du journal et l'état de la démo ; la navigation est celle de la console. */
(function () {
  'use strict';
  const byId = id => document.getElementById(id);
  byId('argus-phrase').textContent = document.querySelector('meta[name="argus-phrase"]').content;
  // Les suggestions changent avec l'état de la banque : un seul écouteur les sert toutes, d'hier et de demain.
  const question = byId('krys-question');
  document.addEventListener('click', event => {
    const suggestion = event.target.closest && event.target.closest('[data-question]');
    if (!suggestion) return;
    question.value = suggestion.dataset.question;
    question.focus();
  });

  new MutationObserver(() => {
    byId('krys-log').scrollTop = byId('krys-log').scrollHeight;
  }).observe(byId('krys-log'), {childList: true});

  /* Seul l'état de disponibilité est affiché ; aucune donnée bancaire ne rejoint le chat. */
  fetch('/demo', {credentials: 'omit', redirect: 'error'})
    .then(response => response.ok ? response.json() : null)
    .then(frame => {
      if (frame && Number.isInteger(frame.tick) && frame.tick >= 0) {
        byId('proof-status').textContent = 'Démo active. Les réponses de Krys restent limitées aux preuves signées disponibles.';
      }
    }).catch(() => { /* Le texte d'attente initial couvre aussi la démo non lancée. */ });
}());
