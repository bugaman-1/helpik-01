"""Best-effort Web Push; durable alert cards remain available independently."""
import base64
import json
from urllib.parse import urlsplit
from django.conf import settings
from quiz.models import Alert, PushSubscription


def public_key():
    if not settings.WEBPUSH_PRIVATE_KEY.exists():
        return ""
    from cryptography.hazmat.primitives import serialization
    private = serialization.load_pem_private_key(settings.WEBPUSH_PRIVATE_KEY.read_bytes(), password=None)
    raw = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def valid_subscription(data):
    if not isinstance(data, dict) or not isinstance(data.get("endpoint"), str):
        return False
    try:
        url = urlsplit(data['endpoint'])
        host = url.hostname or ''
        hosts = {'fcm.googleapis.com', 'updates.push.services.mozilla.com', 'push.services.mozilla.com', 'web.push.apple.com'}
        if url.scheme != 'https' or url.port not in (None, 443) or url.username or url.password or len(data['endpoint']) > 2048 or not (host in hosts or host.endswith('.push.services.mozilla.com') or host.endswith('.notify.windows.com')):
            return False
        keys = data['keys']
        auth = base64.urlsafe_b64decode(keys['auth'] + '=' * (-len(keys['auth']) % 4))
        point = base64.urlsafe_b64decode(keys['p256dh'] + '=' * (-len(keys['p256dh']) % 4))
        from cryptography.hazmat.primitives.asymmetric import ec
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        return len(auth) == 16
    except (KeyError, TypeError, ValueError):
        return False


def deliver_alert(alert_id):
    from pywebpush import webpush, WebPushException
    from .alerts import doctors_for
    if not public_key():
        return
    alert = Alert.objects.get(pk=alert_id)
    if alert.status != 'open':
        return
    doctors = doctors_for(alert.patient_id).values_list('doctor_id', flat=True)
    subscriptions = PushSubscription.objects.filter(user_id__in=doctors, enabled=True)
    if settings.CSRF_TRUSTED_ORIGINS:
        subscriptions = subscriptions.filter(origin__in=settings.CSRF_TRUSTED_ORIGINS)
    for subscription in subscriptions:
        # No patient name, scores or clinical details on a phone's lock screen.
        payload = json.dumps({'title': 'Компас: новый сигнал', 'body': 'Откройте кабинет врача и проверьте сигналы пациентов.',
                              'url': '/?filter=attention', 'tag': f'alert-{alert.pk}'}, ensure_ascii=False)
        try:
            webpush(subscription_info=subscription.subscription, data=payload,
                vapid_private_key=str(settings.WEBPUSH_PRIVATE_KEY), vapid_claims={'sub': settings.WEBPUSH_SUBJECT},
                ttl=3600, timeout=3, headers={'Urgency': 'high'})
            subscription.last_error = ''
        except WebPushException as exc:
            status = getattr(exc, 'status_code', None)
            if status is None and exc.response is not None:
                status = exc.response.status_code
            subscription.last_error = f'push_http_{status}' if status else 'push_unavailable'
            if status in (404, 410):
                subscription.enabled = False
        except (OSError, ValueError):
            subscription.last_error = 'push_unavailable'
        subscription.save(update_fields=['last_error', 'enabled'])
