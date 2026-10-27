/* Local decision support only. No medication data goes to external services. */
(() => {
  'use strict';
  const form = document.querySelector('#prescription-builder');
  const dataNode = document.querySelector('#medication-picker-data');
  if (!form || !dataNode || !window.fetch) return;
  let data = JSON.parse(dataNode.textContent);
  const name = form.elements.name;
  const start = form.elements.start;
  const end = form.elements.end;
  const token = form.elements.interaction_token;
  const ack = form.elements.interaction_ack;
  const ackField = document.querySelector('#interaction-ack-field');
  const toggle = document.querySelector('#drug-catalog-toggle');
  const panel = document.querySelector('#drug-catalog');
  const group = document.querySelector('#drug-group');
  const list = document.querySelector('#drug-options');
  const status = document.querySelector('#drug-options-status');
  const loading = document.querySelector('#drug-check-loading');
  const result = document.querySelector('#drug-check');
  const alternatives = document.querySelector('#drug-alternatives');
  const submit = document.querySelector('#prescription-submit');
  const csrf = form.elements.csrfmiddlewaretoken.value;
  const levels = new Set(['blocked', 'warning', 'note', 'unknown']);
  const normalized = value => value.toLocaleLowerCase('ru').replaceAll('ё', 'е').trim();
  let query = '', timer = null, controller = null, requestNumber = 0, selectedIndex = -1;
  let visible = [];

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function level(value) { return levels.has(value) ? value : 'unknown'; }
  function open(value) {
    panel.hidden = !value;
    toggle.setAttribute('aria-expanded', String(value));
    name.setAttribute('aria-expanded', String(value));
    if (value) renderOptions();
    else { name.removeAttribute('aria-activedescendant'); selectedIndex = -1; }
  }
  function optionButton(item, isAlternative = false) {
    const button = node('button', '', 'drug-option ' + level(item.level));
    button.type = 'button';
    if (!isAlternative) {
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(normalized(name.value) === normalized(item.name)));
      button.id = 'drug-option-' + item.code;
    }
    button.append(node('strong', item.name), node('span', item.label, 'drug-badge ' + level(item.level)),
      node('span', item.group, 'drug-summary'));
    if (item.level !== 'unknown') button.append(node('span', item.summary, 'drug-summary'));
    button.addEventListener('click', () => choose(item));
    return button;
  }
  function renderOptions() {
    visible = data.options.filter(item => (!group.value || item.group === group.value) &&
      (!query || normalized([item.name, ...item.aliases].join(' ')).includes(normalized(query))));
    list.replaceChildren(...visible.map(item => optionButton(item)));
    status.textContent = visible.length ? `Препаратов в списке: ${visible.length}` : 'Нет совпадений. Название можно ввести вручную.';
    selectedIndex = -1;
  }
  function renderReview(check) {
    result.className = 'drug-check ' + level(check.level);
    result.hidden = !name.value.trim();
    result.replaceChildren();
    token.value = check.token || '';
    ackField.hidden = !check.needs_ack;
    submit.disabled = check.level === 'blocked';
    if (!name.value.trim()) {

    } else {
      result.append(node('strong', check.label), node('p', check.summary));
      if (!check.recognized) result.append(node('p', 'Название не распознано в каталоге. Автоматическая проверка для него недоступна.'));

      for (const finding of check.findings || []) {
        const article = node('article', '', 'drug-finding ' + level(finding.level));
        article.append(node('b', finding.medicine + ' · ' + finding.message), node('p', finding.detail));
        const sources = node('div', '', 'drug-sources');
        for (const source of finding.sources) {
          const link = node('a', source.title);
          link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
          sources.append(link);
        }
        article.append(sources); result.append(article);
      }
      if (check.unknown?.length) result.append(node('p', 'Недостаточно информации о сочетаниях с: ' + check.unknown.join(', ') + '.', 'small-text'));
      if (check.uncertainty?.length) {
        const details = node('details', '', 'drug-uncertainty');
        details.append(node('summary', 'Где недостаточно информации'));
        for (const item of check.uncertainty) {
          const article = node('article', '', 'drug-finding unknown');
          article.append(node('b', item.medicine), node('p', item.comment));
          if (item.checked_at) article.append(node('p', 'Поиск и проверка: ' + item.checked_at, 'small-text muted'));
          const sources = node('div', '', 'drug-sources');
          for (const source of item.sources) {
            const link = node('a', source.title);
            link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.title = source.kind;
            sources.append(link);
          }
          article.append(sources); details.append(article);
        }
        result.append(details);
      }
    }
    alternatives.replaceChildren();
    alternatives.hidden = !data.alternatives?.length;
    if (!alternatives.hidden) {
      alternatives.append(node('h3', 'Варианты для сравнения'));
      data.alternatives.forEach(item => alternatives.append(optionButton(item, true)));
    }
  }
  function choose(item) {
    name.value = item.name;
    query = '';
    open(false);
    name.focus();
    schedule(true);
  }
  function clearAcknowledgment() { ack.checked = false; token.value = ''; }
  function schedule(immediate = false) {
    clearTimeout(timer);
    controller?.abort();
    requestNumber++;
    clearAcknowledgment();
    loading.hidden = false; loading.textContent = 'Проверяем сочетания…';
    submit.disabled = true;
    timer = setTimeout(refresh, immediate ? 0 : 300);
  }
  async function refresh() {
    const number = requestNumber;
    controller = new AbortController();
    if (!start.value || !end.value || start.value > end.value) {
      loading.textContent = 'Укажите корректные даты назначения.';
      submit.disabled = false;
      return;
    }
    try {
      const body = new URLSearchParams({name: name.value, start: start.value, end: end.value,
        exclude: form.elements.medication_id.value});
      const response = await fetch(form.dataset.checkUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf}, body, signal: controller.signal});
      if (!response.ok) throw new Error('check failed');
      const next = await response.json();
      if (number !== requestNumber) return;
      data = next;
      loading.hidden = true;
      renderOptions(); renderReview(data.selected);
    } catch (error) {
      if (number !== requestNumber || error.name === 'AbortError') return;
      clearAcknowledgment();
      loading.textContent = 'Не удалось обновить проверку. Сервер повторно проверит сочетания перед сохранением.';
      result.replaceChildren(node('p', 'Предыдущая подсказка устарела. Обновите страницу или повторите ввод.'));
      result.className = 'drug-check unknown';
      alternatives.hidden = true;
      submit.disabled = false;
    }
  }
  name.setAttribute('role', 'combobox'); name.setAttribute('aria-autocomplete', 'list');
  name.setAttribute('aria-controls', 'drug-options'); name.setAttribute('aria-expanded', 'false');
  name.autocomplete = 'off';
  [...new Set(data.options.map(item => item.group))].sort((a,b) => a.localeCompare(b, 'ru')).forEach(value => {
    const option = node('option', value); option.value = value; group.append(option);
  });
  toggle.addEventListener('click', () => { query = ''; open(panel.hidden); });
  group.addEventListener('change', renderOptions);
  name.addEventListener('input', () => { query = name.value; open(true); schedule(); });
  start.addEventListener('change', () => schedule(true)); end.addEventListener('change', () => schedule(true));
  name.addEventListener('keydown', event => {
    if (event.key === 'Escape') { open(false); return; }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (panel.hidden) { query = ''; open(true); }
      selectedIndex = Math.max(0, Math.min(visible.length - 1, selectedIndex + (event.key === 'ArrowDown' ? 1 : -1)));
      const option = list.children[selectedIndex];
      if (option) { name.setAttribute('aria-activedescendant', option.id); option.scrollIntoView({block: 'nearest'}); }
    } else if (event.key === 'Enter' && !panel.hidden && selectedIndex >= 0) {
      event.preventDefault(); choose(visible[selectedIndex]);
    }
  });
  panel.addEventListener('keydown', event => { if (event.key === 'Escape') { open(false); name.focus(); } });
  document.addEventListener('click', event => { if (!panel.contains(event.target) && event.target !== toggle && event.target !== name) open(false); });
  renderOptions(); renderReview(data.selected);
})();
