(() => {
  'use strict';
  function fit(field) {
    if (!(field instanceof HTMLTextAreaElement)) return;
    field.rows = 2;
    field.classList.add('autosize');
    if (!field.getClientRects().length) return;
    const style = getComputedStyle(field);
    const border = parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    field.style.height = 'auto';
    field.style.height = Math.ceil(field.scrollHeight + border) + 'px';
  }
  function fitAll(root = document) {
    root.querySelectorAll('textarea').forEach(fit);
  }
  fitAll();
  document.addEventListener('input', event => fit(event.target));
  document.addEventListener('toggle', event => {
    if (event.target instanceof HTMLDetailsElement && event.target.open) fitAll(event.target);
  }, true);
  document.addEventListener('reset', event => requestAnimationFrame(() => fitAll(event.target)));
  window.addEventListener('resize', () => fitAll());
  window.addEventListener('pageshow', () => fitAll());
})();
