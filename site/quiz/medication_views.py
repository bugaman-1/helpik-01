from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST, require_GET
from quiz.access_views import domain_view
from quiz.care_views import patient_for, invalid_form
from quiz.care_forms import ReportedMedicationForm, InteractionCheckForm
from quiz.services.access import is_approved_doctor
from quiz.services.medication_review import save_reported, picker


@login_required
@require_POST
@domain_view
def save(request, pk):
    patient = patient_for(request.user, pk, write=True)
    form = ReportedMedicationForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    save_reported(request.user, patient, form.cleaned_data)
    messages.success(request, 'Лекарство сохранено.')
    return redirect(reverse('patient_records', args=[patient.pk]) + '#patient-medications')


@login_required
@require_POST
@domain_view
def check(request, pk):
    patient = patient_for(request.user, pk, write=True)
    form = InteractionCheckForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'message': 'Укажите корректные даты назначения.', 'errors': form.errors.get_json_data()}, status=400)
    return JsonResponse(picker(patient, **form.cleaned_data))


@login_required
@require_GET
def source_document(request):
    if not is_approved_doctor(request.user):
        raise PermissionDenied()
    path = settings.BASE_DIR / 'quiz/resources/medications.docx'
    if not path.is_file():
        raise Http404()
    return FileResponse(path.open('rb'), as_attachment=True, filename='Препараты.docx')
