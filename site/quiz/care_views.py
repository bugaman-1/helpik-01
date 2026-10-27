import uuid
from datetime import datetime, time, timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from quiz.models import (Profile, CareNote, CareMessage, AdverseEvent, Questionnaire, QuestionnaireVersion,
                         PrescriptionVersion, CareRelationship)
from quiz.care_forms import (NoteForm, MessageForm, AdverseForm, PlanForm, PrescriptionForm, AssignmentForm, IntakeForm)
from quiz.services.access import require_patient_access, require_moderator, has_role
from quiz.services.contracts import DomainError
from quiz.services import questionnaires
from quiz.services.care_records import latest_plan, schedule, append_once, save_action, mark_intake
from quiz.services.dynamics import selected_date
from quiz.access_views import domain_view


def new_form(cls, **initial):
    return cls(initial={"key": uuid.uuid4(), **initial}, auto_id=f"id_{uuid.uuid4().hex}_%s")


def care_context(request, patient, *, doctor=False, day=None):
    plan = latest_plan(patient)
    version = plan.version if plan else 0
    meds = list(plan.definition.get("medicines", [])) if plan else []
    form = new_form(PrescriptionForm, expected_version=version, start=timezone.localdate(), end=timezone.localdate())
    editing = next((m for m in meds if m["id"] == request.GET.get("edit")), None)
    if editing:
        form = new_form(PrescriptionForm, **{**editing, "medication_id": editing["id"],
            "expected_version": version, "times": ", ".join(editing["times"])})
    for med in meds:
        med["stop_form"] = new_form(PlanForm, expected_version=version)
        med['stop_form'].fields['reason'].label = 'Причина завершения'
    day = day or selected_date(request)
    week_slots = [row for ago in range(7) for row in schedule(patient, day-timedelta(days=ago)) if row['can_mark']]
    taken = sum(row['status'] == 'taken' for row in week_slots)
    skipped = sum(row['status'] == 'skipped' for row in week_slots)
    medication_context = {}
    if doctor:
        from quiz.medication_ui import doctor_context
        medication_context = doctor_context(request, patient, form)
        from quiz.services.medication_review import review, medication_context as interaction_context
        from datetime import date
        for med in meds:
            med['interaction_check'] = review(med['name'], date.fromisoformat(med['start']), date.fromisoformat(med['end']), interaction_context(patient, med['id']))
    return {"medicines": meds, "schedule_rows": schedule(patient, day), "schedule_day": day,
        **medication_context,
        "adherence": {"total": len(week_slots), "taken": taken, "skipped": skipped,
            "missing": len(week_slots)-taken-skipped, "percent": round(100*taken/len(week_slots)) if week_slots else None},
        "prescription_form": form, "editing": editing, "plan": plan,
        "plan_history": PrescriptionVersion.objects.filter(patient=patient).select_related("action__clinician").order_by("-version"),
        "note_form": new_form(NoteForm), "plan_form": new_form(PlanForm, expected_version=version),
        "message_form": new_form(MessageForm, kind="message"), "adverse_form": new_form(AdverseForm),
        "assignment_form": new_form(AssignmentForm, day=timezone.localdate(), extra=0),
        "messages_url": "care_message", "care_patient": patient, "care_doctor": doctor,
        "care_messages": CareMessage.objects.filter(patient=patient).select_related("author").order_by("-created_at")[:100],
        "adverse_events": AdverseEvent.objects.filter(patient=patient).order_by("-created_at")[:100],
        "care_notes": CareNote.objects.filter(patient=patient).select_related("author").order_by("-created_at")[:100] if doctor else [],
        "decisions": patient.clinical_actions.select_related("clinician").order_by("-created_at")[:100],
        "demo_survey_enabled": settings.DEMO_SURVEY_ENABLED}


def patient_for(actor, pk, write=False):
    require_patient_access(actor, pk, clinical_write=write)
    return get_object_or_404(Profile.objects.select_related("user"), user_id=pk, role="patient").user


def invalid_form(request, form):
    return render(request, "quiz/ui/form_error.html", {"form": form}, status=400)


def back(request, patient):
    if has_role(request.user, "patient"):
        return redirect("contact")
    return redirect("patient_records", pk=patient.pk)


@login_required
@require_POST
@domain_view
@transaction.atomic
def message(request, pk):
    patient = patient_for(request.user, pk)
    form = MessageForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    append_once(CareMessage, patient=patient, author=request.user, **form.cleaned_data)
    messages.success(request, "Сообщение отправлено.")
    return back(request, patient)


@login_required
@require_POST
@domain_view
@transaction.atomic
def adverse(request):
    if not has_role(request.user, "patient"):
        raise DomainError("forbidden")
    form = AdverseForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    append_once(AdverseEvent, patient=request.user, **form.cleaned_data)
    messages.success(request, "Сообщение о нежелательном явлении сохранено и доступно наблюдающим врачам.")
    return redirect("home")


@login_required
@require_POST
@domain_view
@transaction.atomic
def note(request, pk):
    patient = patient_for(request.user, pk, write=True)
    form = NoteForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    append_once(CareNote, patient=patient, author=request.user, **form.cleaned_data)
    messages.success(request, "Заметка сохранена.")
    return back(request, patient)


@login_required
@require_POST
@domain_view
def action(request, pk, kind, medication_id=None):
    patient = patient_for(request.user, pk, write=True)
    form = (PrescriptionForm if kind == "prescription" else PlanForm)(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    try:
        save_action(request.user, patient, form.cleaned_data, kind=kind, stop_id=medication_id)
    except DomainError as exc:
        errors = {
            'medication_contraindicated': ('name', 'Назначение не сохранено: найдено противопоказанное сочетание. Выберите другой препарат или уточните сведения о приёме.'),
            'medication_review_required': ('interaction_ack', 'Изучите предупреждения и подтвердите, что учли их в решении.'),
            'medication_context_changed': ('interaction_ack', 'Список лекарств или даты изменились. Проверьте обновлённые предупреждения и подтвердите решение заново.'),
        }
        if kind != 'prescription' or exc.code not in errors:
            raise
        from quiz.medication_ui import editor_context
        context = editor_context(patient, form)
        form.data = form.data.copy()
        form.data['interaction_token'] = context['medication_check']['token']
        form.data['interaction_ack'] = ''
        field, message = errors[exc.code]
        form.add_error(field, message)
        return render(request, 'quiz/ui/prescription_error.html', {'prescription_form': form, 'care_patient': patient, **context}, status=409)
    messages.success(request, "Назначение сохранено." if kind == 'prescription' else 'Изменения сохранены.')
    return back(request, patient)


@login_required
@require_POST
@domain_view
def intake(request):
    form = IntakeForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    mark_intake(request.user, form.cleaned_data)
    messages.success(request, "Отметка приёма сохранена.")
    return redirect('/?date=' + timezone.localtime(form.cleaned_data["due_at"]).date().isoformat())


@login_required
@require_POST
@domain_view
def assign(request, pk):
    patient = patient_for(request.user, pk, write=True)
    form = AssignmentForm(request.POST)
    if not form.is_valid():
        return invalid_form(request, form)
    data = form.cleaned_data
    start = timezone.make_aware(datetime.combine(data["day"], time.min))
    end = start+timedelta(days=1)
    from quiz.services.screening import CODE as SCREENING_CODE
    observation_start = end-timedelta(days=14) if data["version"].questionnaire.code == SCREENING_CODE else start
    questionnaires.assign(request.user, patient, data["version"], key=data["key"], timezone_name=settings.TIME_ZONE,
        available_from=start, available_until=end, observation_start=observation_start, observation_end=end,
        window_code="daily", unscheduled_limit=data["extra"])
    messages.success(request, "Опрос назначен на выбранный день.")
    return back(request, patient)


@login_required
@require_POST
@domain_view
def prepare_demo_survey(request):
    require_moderator(request.user)
    if not settings.DEMO_SURVEY_ENABLED:
        raise DomainError("clinical_publication_disabled")
    from quiz.services.diary import backup_database, prepare
    backup_database()
    prepare()
    messages.success(request, "Дневник самочувствия готов к назначению.")
    return redirect("home")
