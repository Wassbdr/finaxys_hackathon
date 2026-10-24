/* Les pages du collègue ne se chargent qu'à l'ouverture de leur onglet : la 3D et le marché ne tournent pas en fond. */
(function () {
  'use strict';
  function fill(section) {
    if (!section || section.hidden || section.querySelector('iframe')) return;
    const frame = document.createElement('iframe');
    frame.src = section.dataset.src;
    frame.title = section.id;
    frame.loading = 'lazy';
    section.appendChild(frame);
  }
  const sections = [...document.querySelectorAll('section.colleague-page')];
  const watch = new MutationObserver(() => sections.forEach(fill));
  sections.forEach(section => watch.observe(section, {attributes: true, attributeFilter: ['hidden']}));
  sections.forEach(fill);
})();
