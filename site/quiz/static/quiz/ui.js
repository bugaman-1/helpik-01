/* Presentation only. No clinical scoring, analytics or persisted demo data. */
(() => {
  document.querySelectorAll('[data-contact-patient]').forEach(link => link.addEventListener('click', () => {
    requestAnimationFrame(() => document.querySelector('#patient-messages textarea')?.focus({preventScroll: true}));
  }));
  document.querySelectorAll('[data-demo-enable]').forEach(b => b.disabled = false);
  const toast = document.querySelector('#ui-toast');
  let timer;
  function notify(message) { if (!toast) return; toast.textContent = message; toast.hidden = false; clearTimeout(timer); timer = setTimeout(() => toast.hidden = true, 7000); }
  document.querySelectorAll('[data-toast]').forEach(b => b.addEventListener('click', () => notify(b.dataset.toast)));
  document.querySelectorAll('[data-demo-status]').forEach(b => b.addEventListener('click', () => {
    const card = b.closest('[data-demo-card]'); card.querySelector('.demo-feedback').textContent = b.dataset.demoStatus;
    card.querySelectorAll('[data-demo-status]').forEach(x => {x.classList.toggle('primary', x === b); x.setAttribute('aria-pressed', String(x === b));});
  }));
  document.querySelectorAll('[data-demo-message]').forEach(form => form.addEventListener('submit', e => {
    e.preventDefault(); const text = form.elements.message.value.trim(); if (!text) return;
    const msg = document.createElement('div'); msg.className = 'chat-message'; msg.textContent = text;
    const label = document.createElement('p'); label.className = 'small-text muted'; label.textContent = 'Демо · не сохраняется'; msg.append(label);
    form.parentElement.querySelector('.demo-messages').append(msg); form.reset(); notify('Демо · не сохраняется. Врач не получит сообщение.');
  }));
  document.querySelectorAll('[data-demo-decision]').forEach(form => form.addEventListener('submit', e => {
    e.preventDefault(); form.querySelector('.demo-feedback').textContent = `${form.elements.decision.value} · демо · не сохраняется`;
  }));
  document.querySelectorAll('[name=selected_role]').forEach(input => input.addEventListener('change', () => {
    const role = input.value; document.querySelectorAll('[data-role-description]').forEach(x => x.hidden = x.dataset.roleDescription !== role);
  }));
})();
