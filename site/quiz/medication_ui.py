import uuid
from datetime import date
from django.utils import timezone
from quiz.care_forms import ReportedMedicationForm
from quiz.services import medication_review as service
from quiz.services.drug_catalog import BY_CODE


def day_value(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else value or timezone.localdate()
    except ValueError:
        return timezone.localdate()


def editor_context(patient, form):
    start = day_value(form['start'].value())
    end = max(start, day_value(form['end'].value()))
    data = service.picker(patient, form['name'].value() or '', start, end, form['medication_id'].value() or '')
    if not form.is_bound:
        form.initial['interaction_token'] = data['selected']['token']
    return {'medication_picker': data, 'medication_check': data['selected']}


def doctor_context(request, patient, prescription_form):
    records = service.latest_reported(patient)
    editing = next((r for r in records if str(r.entry_id) == request.GET.get('edit_taken')), None)
    initial = {'entry_id': uuid.uuid4(), 'expected_revision': 0, 'status': 'taking', 'source': 'patient'}
    if editing:
        initial = {field: getattr(editing, field) for field in ('entry_id', 'name', 'regimen', 'start', 'end', 'status', 'source', 'note')}
        initial['expected_revision'] = editing.revision
    form = ReportedMedicationForm(initial={'key': uuid.uuid4(), **initial}, auto_id='taken_%s')
    context = service.medication_context(patient)
    today = timezone.localdate()
    rows = []
    for record in records:
        current = record.status == 'taking' and (record.end is None or record.end >= today) and (record.start is None or record.start <= today)
        other_context = {**context, 'medicines': [m for m in context['medicines'] if m['id'] != str(record.entry_id)]}
        findings = service.review(record.name, today, today, other_context)['findings'] if current else []
        rows.append({'record': record, 'current': current, 'recognized': BY_CODE.get(record.catalog_code, {}).get('name'),
                     'findings': [f for f in findings if f['level'] in {'warning', 'blocked'}]})
    return {'reported_medications': rows, 'reported_form': form, 'editing_reported': editing,
        'reported_history': patient.reported_medications.select_related('author').order_by('-created_at', '-pk')[:200],
        **editor_context(patient, prescription_form)}
