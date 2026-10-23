/* Le chat et les commandes ont des formulaires distincts : une phrase n'est
   jamais transformée en confirmation. Aucun HTML reçu n'est interprété. */
(function () {
  'use strict';
  const byId = id => document.getElementById(id);
  const log = byId('krys-log');
  if (!log) return;
  let pending = null;
  let busy = false;
  let revision = 0;
  const confirm = byId('krys-confirm');
  function clearPending() {
    revision += 1;
    pending = null;
    confirm.disabled = true;
    byId('krys-recap').textContent = '';
  }
  function show(text, citations) {
    const item = document.createElement('li');
    const paragraph = document.createElement('p');
    paragraph.textContent = String(text);
    item.appendChild(paragraph);
    for (const source of citations || []) {
      const evidence = document.createElement('code');
      evidence.textContent = String(source.message_id) + ' · tick ' + String(source.tick);
      item.appendChild(evidence);
      item.appendChild(document.createElement('br'));
    }
    log.appendChild(item);
    while (log.children.length > 20) log.removeChild(log.firstChild);
  }
  function base() {
    const candidate = new URL(location.protocol === 'http:' || location.protocol === 'https:'
      ? location.origin : byId('url').value.replace(/^ws/, 'http'));
    if (!['127.0.0.1', 'localhost', '[::1]'].includes(candidate.hostname)
        || !['http:', 'https:'].includes(candidate.protocol) || candidate.username || candidate.password) {
      throw new Error('API Krys locale requise');
    }
    return candidate.origin;
  }
  async function post(path, body) {
    const response = await fetch(base() + '/demo/krys/' + path, {
      method: 'POST', credentials: 'omit', redirect: 'error',
      headers: {'Content-Type': 'application/json', 'X-Demo-Token': byId('krys-token').value},
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      let detail = 'Krys indisponible ; la démo continue. Ouvrez /demo/page et vérifiez le jeton de lancement.';
      try {
        const error = await response.json();
        if (typeof error.detail === 'string') detail = error.detail;
      } catch (_) { /* L'erreur de transport ne devient pas un contenu HTML. */ }
      throw new Error(detail);
    }
    return response.json();
  }
  byId('krys-question-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const question = byId('krys-question').value;
    byId('krys-question').value = '';
    try {
      const answer = await post('ask', {question});
      show(answer.text, answer.citations);
    } catch (error) { show(error.message); }
    finally { busy = false; }
  });
  byId('krys-action-form').addEventListener('submit', async event => {
    event.preventDefault();
    clearPending();
    const requestRevision = revision;
    try {
      const prepared = await post('actions/prepare', {
        action: byId('krys-action').value, target: byId('krys-target').value
      });
      if (requestRevision !== revision) return;
      pending = prepared.action_id;
      byId('krys-recap').textContent = prepared.recap + ' Confirmation valable 120 secondes.';
      confirm.disabled = false;
    } catch (error) { show(error.message); }
  });
  confirm.addEventListener('click', async () => {
    if (!pending) return;
    const actionId = pending;
    clearPending();
    try {
      const result = await post('actions/confirm', {action_id: actionId, confirmation: 'confirmer'});
      show(result.status + ' · ' + result.message_id);
    } catch (error) { show(error.message); }
  });
  for (const id of ['krys-action', 'krys-target', 'krys-token', 'url']) {
    byId(id).addEventListener('input', clearPending);
  }
}());
