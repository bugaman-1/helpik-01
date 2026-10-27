(() => {
  'use strict';
  const script = document.querySelector('#notification-client');
  if (!script) return;
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]')?.value;
  const enable = document.querySelector('#enable-alert-notifications');
  const disable = document.querySelector('#disable-alert-notifications');
  const permissionStatus = document.querySelector('#notification-permission-status');
  const supports = window.isSecureContext && 'serviceWorker' in navigator && 'Notification' in window;
  const registration = supports ? navigator.serviceWorker.register(script.dataset.workerUrl, {scope: '/'}).catch(() => null) : Promise.resolve(null);
  const key = `compass-alert-ids-${script.dataset.userId}`;
  const deviceKey = `compass-notifications-${script.dataset.userId}`;
  let enabledOnDevice = false;
  try { enabledOnDevice = localStorage.getItem(deviceKey) === 'true'; } catch (_) {}
  let seen = null, latest = null, polling = false;
  try { const saved = sessionStorage.getItem(key); if (saved) seen = new Set(JSON.parse(saved)); } catch (_) {}
  const banner = document.createElement('aside');
  banner.className = 'notice doctor-alert-notice'; banner.setAttribute('role', 'status'); banner.hidden = true;
  const link = document.createElement('a'); link.href = '/?filter=attention';
  banner.append(link); document.querySelector('#content').prepend(banner);

  function note(message) { if (permissionStatus) permissionStatus.textContent = message; }
  async function post(data) {
    const response = await fetch(script.dataset.subscribeUrl, {method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(data)});
    if (!response.ok) throw new Error('subscription');
  }
  function decodeKey(value) {
    const raw = atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4));
    return Uint8Array.from(raw, ch => ch.charCodeAt(0));
  }
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch(script.dataset.feedUrl, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok || response.redirected) return;
      latest = await response.json();
      banner.hidden = !latest.count || !!document.querySelector('#patient-alerts');
      link.textContent = `Открытых сигналов: ${latest.count} · Пациенты →`;
      const patients = new Set(latest.items.map(item => String(item.patient_id)));
      document.querySelectorAll('[data-patient-id]').forEach(card => {
        if (patients.has(card.dataset.patientId)) {
          card.classList.add('attention');
          const mark = card.querySelector('[data-alert-mark]'); if (mark) mark.hidden = false;
        }
      });
      const fresh = seen ? latest.items.filter(item => !seen.has(item.key)) : [];
      seen = new Set([...(seen || []), ...latest.items.map(item => item.key)].slice(-200));
      try { sessionStorage.setItem(key, JSON.stringify([...seen])); } catch (_) {}
      if (fresh.length && enabledOnDevice && supports && Notification.permission === 'granted') {
        const worker = await registration;
        if (worker) await worker.showNotification('Компас: новый сигнал', {body: 'Проверьте сигналы пациентов в кабинете врача.',
          icon: '/static/quiz/app-192.png', tag: `alert-${fresh[0].id}`, data: {url: '/?filter=attention'}});
      }
    } catch (_) { /* Keep existing cards; retry on the next poll. */ }
    finally { polling = false; }
  }
  enable?.addEventListener('click', async () => {
    if (!supports) { note('Уведомления недоступны здесь. Откройте HTTPS-ссылку; на iPhone добавьте сайт на экран «Домой».'); return; }
    try {
      // Request permission directly in the click gesture, including on iOS.
      const permission = await Notification.requestPermission();
      if (permission !== 'granted') { note('Уведомления не разрешены. Сигналы остаются в кабинете. Разрешение можно изменить в настройках браузера.'); return; }
      enabledOnDevice = true;
      try { localStorage.setItem(deviceKey, 'true'); } catch (_) {}
      const worker = await registration;
      if (!worker) throw new Error('worker');
      await navigator.serviceWorker.ready;
      if (!latest) await poll();
      if (!latest?.public_key || !('PushManager' in window)) {
        note('Системные уведомления включены для открытого приложения. Фоновая доставка сейчас недоступна.'); return;
      }
      let subscription = await worker.pushManager.getSubscription();
      if (subscription && subscription.options.applicationServerKey &&
          String(new Uint8Array(subscription.options.applicationServerKey)) !== String(decodeKey(latest.public_key))) {
        await subscription.unsubscribe(); subscription = null;
      }
      subscription ||= await worker.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: decodeKey(latest.public_key)});
      await post(subscription.toJSON());
      note('Уведомления включены на этом устройстве, в том числе для закрытой вкладки.');
      await worker.showNotification('Компас', {body: 'Уведомления врача включены.', tag: 'compass-enabled'});
    } catch (_) { note('Не удалось включить фоновую доставку. Проверьте разрешение; на iPhone запускайте приложение с экрана «Домой». Сигналы доступны в кабинете.'); }
  });
  disable?.addEventListener('click', async () => {
    enabledOnDevice = false;
    try { localStorage.setItem(deviceKey, 'false'); } catch (_) {}
    try {
      const worker = await registration;
      const subscription = await worker?.pushManager.getSubscription();
      if (subscription) {
        await post({remove: true, endpoint: subscription.endpoint});
        await subscription.unsubscribe();
      }
      note('Фоновые уведомления на этом устройстве выключены. Сигналы доступны в кабинете.');
    } catch (_) { note('Не удалось отключить подписку. Повторите попытку.'); }
  });
  if (supports) note(Notification.permission === 'granted' ? 'Разрешение браузера есть. Нажмите «Включить», чтобы подключить этот аккаунт.' : 'Браузер запросит разрешение после нажатия кнопки.');
  else note('На iPhone откройте приложение с экрана «Домой». В других браузерах необходима HTTPS-ссылка и поддержка уведомлений.');
  poll();
  setInterval(() => { if (!document.hidden) poll(); }, 15000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
})();
