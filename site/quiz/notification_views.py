import json
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from quiz.models import Alert, PushSubscription
from quiz.services.access import is_approved_doctor, accessible_patients
from quiz.services import alerts, notifications
from quiz.access_views import domain_view
from quiz.services.contracts import DomainError


@login_required
@require_GET
@domain_view
def feed(request):
    if not is_approved_doctor(request.user):
        raise DomainError('forbidden')
    ids = accessible_patients(request.user).values_list('user_id', flat=True)
    query = Alert.objects.filter(patient_id__in=ids, status='open').order_by('-pk')
    return JsonResponse({'count': query.count(), 'items': [
        {'id': a.pk, 'key': f'{a.pk}:{a.evidence_id or 0}', 'patient_id': a.patient_id, 'priority': a.evidence_snapshot.get('priority', 'review'),
         'url': reverse('patient_records', args=[a.patient_id]) + '#patient-alerts'} for a in query[:100]],
         'public_key': notifications.public_key()})


@login_required
@require_POST
@domain_view
def subscription(request):
    if not is_approved_doctor(request.user):
        raise DomainError('forbidden')
    if len(request.body) > 8192:
        return JsonResponse({'error': 'invalid_subscription'}, status=400)
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'invalid_subscription'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'invalid_subscription'}, status=400)
    if data.get('remove'):
        PushSubscription.objects.filter(user=request.user, endpoint=data.get('endpoint', '')).delete()
        return JsonResponse({'ok': True})
    if not notifications.valid_subscription(data):
        return JsonResponse({'error': 'invalid_subscription'}, status=400)
    origin = request.build_absolute_uri('/').rstrip('/')
    if PushSubscription.objects.filter(user=request.user, origin=origin, enabled=True).exclude(endpoint=data['endpoint']).count() >= 5:
        return JsonResponse({'error': 'device_limit'}, status=400)
    # A browser endpoint follows the currently signed-in doctor's explicit opt-in.
    PushSubscription.objects.update_or_create(endpoint=data['endpoint'], defaults={
        'user': request.user, 'subscription': {'endpoint': data['endpoint'], 'keys': data['keys']},
        'origin': origin, 'enabled': True, 'last_error': ''})
    return JsonResponse({'ok': True})


@login_required
@require_POST
@domain_view
def resolve(request, pk):
    get_object_or_404(Alert, pk=pk)
    alert = alerts.resolve(request.user, pk, request.POST.get('status'), request.POST.get('note', ''))
    return redirect(reverse('patient_records', args=[alert.patient_id]) + '#patient-alerts')


@require_GET
def worker(request):
    source = Path(settings.BASE_DIR) / 'quiz/static/quiz/notifications-worker.js'
    response = HttpResponse(source.read_text(encoding='utf-8'), content_type='application/javascript')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response


@require_GET
def manifest(request):
    return JsonResponse({'id': '/', 'name': 'Клинический компас', 'short_name': 'Компас',
        'start_url': '/', 'scope': '/', 'display': 'standalone', 'lang': 'ru',
        'background_color': '#f6f8f7', 'theme_color': '#226a57',
        'icons': [{'src': '/static/quiz/app-192.png', 'sizes': '192x192', 'type': 'image/png'},
                  {'src': '/static/quiz/app-512.png', 'sizes': '512x512', 'type': 'image/png'}]}, content_type='application/manifest+json')
