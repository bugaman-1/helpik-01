/* Notifications only: no offline cache of patient pages or answers. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data?.json() || {}; } catch (_) { /* Use the generic message. */ }
  event.waitUntil(self.registration.showNotification('Компас: новый сигнал', {
    body: 'Откройте кабинет врача и проверьте сигналы пациентов.',
    icon: '/static/quiz/app-192.png', tag: data.tag || 'compass-alert',
    data: {url: '/?filter=attention'}
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const url = new URL('/?filter=attention', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(async clients => {
    const client = clients.find(item => new URL(item.url).origin === self.location.origin);
    if (client) { await client.navigate(url); return client.focus(); }
    return self.clients.openWindow(url);
  }));
});
