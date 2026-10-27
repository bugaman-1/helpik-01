/* No localStorage, offline answer cache, external analytics or clinical scoring. */
(() => {
  'use strict';
  const root = document.querySelector('#survey-app');
  if (!root || !window.fetch || !window.crypto?.randomUUID) return;
  const status = document.querySelector('#save-status');
  const retry = document.querySelector('#retry-save');
  const finish = document.querySelector('#finish-form');
  const forms = [...document.querySelectorAll('.answer-form')];
  if (!forms.length) return;
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const enabled = root.dataset.telemetry === 'true';
  const stream = crypto.randomUUID();
  let seq = 0, queue = [], flushing = false, pending = null, busy = false;
  let flushDone = Promise.resolve();
  let active = null, finishing = false;
  const latencies = new Map();
  const visited = new Set();
  const dirty = new Set();
  function event(kind, form = active) {
    if (!enabled) return;
    if (queue.length >= 64) {
      seq++;
      return;
    }
    queue.push({event_id: crypto.randomUUID(), stream_id: stream, sequence: ++seq,
      kind, question_code: form?.dataset.code || '', revision_id: form?.dataset.revisionId ? Number(form.dataset.revisionId) : null,
      client_at: new Date().toISOString(), elapsed_ms: Math.round(performance.now()),
      save_latency_ms: kind === 'question_left' ? (latencies.get(form) ?? null) : null});
  }
  async function flush() {
    if (flushing) return flushDone;
    if (!enabled || !queue.length) return;
    flushing = true;
    let done;
    flushDone = new Promise(resolve => { done = resolve; });
    const batch = queue.slice(0, 32);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);
    try {
      const response = await fetch(root.dataset.eventsUrl, {method: 'POST', credentials: 'same-origin', keepalive: true, signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify({events: batch})});
      if (response.ok) queue.splice(0, batch.length);
      else if (response.status < 500) {
        queue.splice(0, batch.length);
      }
    } catch (_) { /* Retry bounded in-memory queue; loss on page close is possible. */ }
    finally { clearTimeout(timeout); flushing = false; done(); }
  }
  function lock(value) {
    forms.forEach(f => { if (finish) f.querySelector('fieldset').disabled = value; });
    if (finish) finish.querySelectorAll('button').forEach(b => b.disabled = value);
    root.dispatchEvent(new CustomEvent('survey:locked', {detail: value}));
  }
  async function sendPending() {
    if (!pending || busy) return;
    busy = true; retry.hidden = true; lock(true);
    status.textContent = 'Сохраняем…';
    if (pending.finish) { for (let i = 0; i < 3 && queue.length; i++) await flush(); }
    const sentAt = performance.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(pending.url, {method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(pending.data)});
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.message || 'Не удалось сохранить. Повторить';
        if (response.status === 409) document.querySelector('#conflict-help').hidden = false;
        if (response.status >= 500) retry.hidden = false;
        else if (response.status !== 409) { pending = null; finishing = false; lock(false); }
        // A conflict retains the local selection and prevents silent overwrite.
        return;
      }
      if (pending.finish) { pending = null; window.scrollTo(0, 0); location.reload(); return; }
      const form = pending.form;
      form.elements.expected_revision.value = result.revision;
      form.elements.client_event_id.value = crypto.randomUUID();
      form.dataset.revisionId = result.revision_id;
      // Preserve a stale-generation conflict when another question changed elsewhere.
      const previousGeneration = Number(root.dataset.generation);
      root.dataset.generation = String(previousGeneration + 1);
      if (finish) finish.elements.expected_generation.value = root.dataset.generation;
      latencies.set(form, Math.round(performance.now() - sentAt));
      dirty.delete(form);
      form.dataset.saved = pending.data.state === 'missing' ? 'false' : 'true';
      form.querySelector('.answer-status').textContent = pending.data.state === 'missing' ? 'Вопрос пропущен' : 'Сохранено';
      status.textContent = 'Сохранено';
      document.querySelector('#run-status').textContent = 'Заполняется';
      pending = null; lock(false);
      root.dispatchEvent(new CustomEvent('survey:confirmed', {detail: form}));
      flush();
    } catch (_) {
      status.textContent = 'Не удалось сохранить. Повторить';
      retry.hidden = false;
    } finally { clearTimeout(timeout); busy = false; }
  }
  forms.forEach(form => {
    let chosen = Boolean(form.querySelector('[name=choice]:checked'));
    form.addEventListener('change', () => {
      dirty.add(form);
      form.dataset.saved = 'false';
      event(chosen ? 'answer_changed' : 'option_selected', form);
      chosen = true;
      form.querySelector('.answer-status').textContent = 'Изменения ещё не сохранены';
      status.textContent = 'Изменения ещё не сохранены';
      root.dispatchEvent(new CustomEvent('survey:changed'));
    });
    form.addEventListener('submit', e => {
      e.preventDefault();
      if (pending) return;
      const choice = form.querySelector('[name=choice]:checked')?.value;
      if (!choice) { status.textContent = 'Выберите ответ.'; return; }
      pending = {url: form.action, form, data: {
        question_code: form.dataset.code, expected_revision: Number(form.elements.expected_revision.value),
        client_event_id: form.elements.client_event_id.value,
        state: choice.startsWith('option:') ? 'known' : choice,
        value: choice.startsWith('option:') ? choice.slice(7) : null}};
      sendPending();
    });
  });
  finish?.addEventListener('submit', e => {
    e.preventDefault();
    if (pending) return;
    if (dirty.size && e.submitter?.value !== 'true') {
      status.textContent = 'Сначала сохраните изменённые ответы.';
      return;
    }
    finishing = true;
    if (active && !document.hidden) { event('question_left', active); active = null; }
    flush();
    pending = {url: finish.action, finish: true, data: {key: finish.elements.key.value,
      expected_generation: Number(finish.elements.expected_generation.value), abandon: e.submitter?.value === 'true'}};
    sendPending();
  });
  retry.addEventListener('click', sendPending);
  window.addEventListener('beforeunload', e => {
    if (dirty.size || pending) { e.preventDefault(); e.returnValue = ''; }
  });
  // Observe one central visible question at a time; focus chooses between visible forms.
  function activate(form) {
    if (!enabled || finishing || document.hidden || form === active || form?.closest('.survey-question')?.hidden) return;
    if (active) event('question_left', active);
    active = form;
    if (form) { event(visited.has(form.dataset.code) ? 'question_revisited' : 'question_visible', form); visited.add(form.dataset.code); }
  }
  if (enabled) {
    root.addEventListener('survey:visible', e => activate(e.detail));
    const visible = new Set();
    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => e.isIntersecting ? visible.add(e.target) : visible.delete(e.target));
      if (!active || !visible.has(active)) activate(forms.find(f => visible.has(f)) || null);
    }, {threshold: 0.25});
    forms.forEach(f => { observer.observe(f); f.addEventListener('focusin', () => { if (visible.has(f)) activate(f); }); });
    event('page_resume', null);
    document.addEventListener('visibilitychange', () => {
      event(document.hidden ? 'visibility_hidden' : 'visibility_visible'); flush();
    });
    window.addEventListener('pageshow', e => { if (e.persisted) event('page_resume'); });
    window.addEventListener('pagehide', () => { if (active && !document.hidden) event('question_left'); flush(); });
    setInterval(flush, 5000);
  }
})();
