/* Navigation uses confirmed saves from the existing engine. */
(() => {
  const root = document.querySelector('#survey-app');
  const finish = document.querySelector('#finish-form');
  if (!root || !finish) return;
  const questions = [...root.querySelectorAll('.survey-question')];
  if (!questions.length) return;
  const formAt = n => questions[n].querySelector('form');
  let index = Math.max(0, questions.findIndex((q, n) => formAt(n).dataset.saved !== 'true'));
  let locked = false, advanceFrom = null;
  const progress = document.createElement('p');
  progress.className = 'survey-meta'; progress.setAttribute('aria-live', 'polite');
  const bar = document.createElement('progress'); bar.max = questions.length; bar.setAttribute('aria-label', 'Сохранённые ответы');
  const block = document.createElement('h2'); block.className = 'survey-block'; block.tabIndex = -1;
  const controls = document.createElement('div'); controls.className = 'survey-controls';
  const previous = document.createElement('button'); previous.type = 'button'; previous.textContent = 'Назад';
  const next = document.createElement('button'); next.type = 'button'; next.className = 'primary'; next.textContent = 'Далее';
  controls.append(previous, next);
  root.insertBefore(progress, questions[0]); root.insertBefore(bar, questions[0]); root.insertBefore(block, questions[0]); root.insertBefore(controls, finish);
  function show(focus = false) {
    questions.forEach((q, n) => q.hidden = n !== index);
    root.dispatchEvent(new CustomEvent('survey:visible', {detail: formAt(index)}));
    const saved = questions.filter((q, n) => formAt(n).dataset.saved === 'true').length;
    progress.textContent = `Вопрос ${index + 1} из ${questions.length} · Сохранено ${saved} из ${questions.length}`;
    bar.value = saved;
    const code = formAt(index).dataset.code;
    block.textContent = code.startsWith('BDI') ? 'Самочувствие сегодня' : (code.startsWith('STAI') || code === 'CHECK01') ? 'Тревога прямо сейчас' : code.startsWith('PHQ') ? 'Самочувствие' : code.startsWith('GAD') ? 'Тревога' : questions[index].dataset.additional === 'true' ? 'Дополнительные вопросы' : 'Основные вопросы';
    previous.disabled = locked || index === 0; next.disabled = locked;
    next.hidden = index === questions.length - 1;
    finish.hidden = index !== questions.length - 1;
    if (focus) {
      block.focus({preventScroll: true});
      progress.scrollIntoView({block: 'start'});
    }
  }
  previous.addEventListener('click', () => { if (locked || index === 0) return; advanceFrom = null; index--; show(true); });
  next.addEventListener('click', () => {
    if (locked) return;
    const form = formAt(index);
    if (form.dataset.saved === 'true' || (form.dataset.required === 'false' && !form.querySelector('[name=choice]:checked'))) {
      index++; show(true); return;
    }
    if (!form.querySelector('[name=choice]:checked')) {
      document.querySelector('#save-status').textContent = 'Выберите ответ, чтобы продолжить.'; return;
    }
    advanceFrom = form;
    form.requestSubmit();
  });
  root.addEventListener('survey:confirmed', e => {
    if (advanceFrom === e.detail && index < questions.length - 1) { index++; advanceFrom = null; show(true); }
    else show();
  });
  root.addEventListener('survey:changed', () => show());
  root.addEventListener('survey:locked', e => { locked = e.detail; show(); });
  show();
})();
