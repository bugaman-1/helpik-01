"""Thin read-only presentation adapters. UI demos never change medical state."""
from urllib.parse import urlencode
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET
from quiz.models import Profile, SurveySession, CareRelationship, Alert
from quiz.services.access import has_role, is_approved_doctor, is_moderator, accessible_patients, require_patient_access
from quiz.services.contracts import DomainError
from . import ui_demo
from .care_views import care_context
from .services.dynamics import real_entries, calendar_context
from .services import screening
from .services import alerts, response_quality, checkin

STATUS = {"draft": "Не начат", "in_progress": "Заполняется", "submitted": "Завершён", "abandoned": "Прекращён"}


def shell(screen, *, demo=False, role="patient"):
    if demo:
        links = {name: reverse("ui_preview", args=[name]) for name in ["today", "activity", "medicines", "contact", "doctor", "moderator", "survey"]}
    else:
        links = {"today": reverse("home"), "activity": reverse("activity"), "medicines": reverse("medicines"), "contact": reverse("contact"), "doctor": reverse("home"), "moderator": reverse("home"), "survey": reverse("surveys")}
    return {"ui_screen": screen, "ui_role": role, "preview": demo, "nav": links, "today": timezone.localdate(), "ui_name": ui_demo.PATIENTS[0]["name"].split()[0] if demo else None}


def require_role(user, role):
    if not has_role(user, role):
        raise PermissionDenied()


def runs_for(patient):
    return SurveySession.objects.filter(patient=patient, questionnaire_version__isnull=False).select_related("questionnaire_version__questionnaire").order_by("-started_at")


@login_required
@require_GET
def home(request):
    if is_approved_doctor(request.user):
        return doctor_list(request)
    if is_moderator(request.user):
        patients = Profile.objects.filter(role="patient").select_related("user").prefetch_related("user__care_doctors__doctor__profile")
        return render(request, "quiz/moderator.html", {**shell("moderator", role="moderator"), "demo_survey_enabled": settings.DEMO_SURVEY_ENABLED, "patients": patients, "doctors": Profile.objects.filter(role="doctor").select_related("user")})
    if not has_role(request.user, "patient"):
        return render(request, "quiz/pending.html", status=403)
    latest = runs_for(request.user).first()
    screening_run = runs_for(request.user).filter(questionnaire_version__questionnaire__code=checkin.CODE, started_at__date=timezone.localdate()).first()
    completed = screening_run and screening_run.status == "submitted"
    return render(request, "quiz/ui/today.html", {**shell("today"), "latest_run": latest, "latest_status": STATUS.get(latest.status) if latest else None,
        "screening_run": screening_run, "screening_completed": completed, "screening_result": screening.results(screening_run),
        "screening_due": False,
        "reminders_enabled": request.user.profile.preferences.get("reminders", True),
        **care_context(request, request.user, day=timezone.localdate()),
        **calendar_context(request, real_entries(request.user, request), reverse("home")),
        "ui_name": request.user.first_name or request.user.username, "legacy_latest": request.user.survey_records.first()})

@login_required
@require_GET
def activity(request):
    require_role(request.user, "patient")
    return render(request, "quiz/ui/activity.html", {**shell("activity"), **calendar_context(request, real_entries(request.user, request), reverse("activity")), "records": request.user.survey_records.all()})


@login_required
@require_GET
def medicines(request):
    require_role(request.user, "patient")
    return render(request, "quiz/ui/medicines.html", {**shell("medicines"), **care_context(request, request.user)})


@login_required
@require_GET
def contact(request):
    require_role(request.user, "patient")
    doctors = [{"name": rel.doctor.get_full_name() or rel.doctor.username, "email": rel.doctor.email,
        "phone": rel.doctor.profile.contact_phone, "address": rel.doctor.profile.contact_address,
        "specialty": "Ответственный врач" if rel.is_primary else "Наблюдающий врач", "primary": rel.is_primary, "initials": (rel.doctor.first_name[:1] or rel.doctor.username[:1]).upper()} for rel in CareRelationship.objects.filter(patient=request.user, status="active", doctor__is_active=True).select_related("doctor__profile")]
    return render(request, "quiz/ui/contact.html", {**shell("contact"), "doctors": doctors, **care_context(request, request.user)})


def doctor_list(request):
    query = request.GET.get("q", "")[:100]
    attention = request.GET.get("filter", "attention" if request.user.profile.preferences.get("alerts_only") else "all") == "attention"
    patients = accessible_patients(request.user)
    if query:
        from django.db.models import Q
        patients = patients.filter(Q(user__username__icontains=query) | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query))
    if attention:
        patients = patients.filter(user__alerts__status__in=["open", "acknowledged"]).distinct()
    rows = []
    for p in patients[:100]:
        alert = alerts.active_for(p.user.pk).first()
        latest = runs_for(p.user).first()
        rows.append({"id": p.user.pk, "name": p.user.get_full_name() or p.user.username, "initials": (p.user.first_name[:1] or p.user.username[:1]).upper(),
                     "state": "attention" if alert else "normal", "status": "Требует внимания" if alert else "Нет открытых сигналов",
                     "alert_title": alert.evidence_snapshot.get("title", "Требует внимания") if alert else "",
                     "note": f"Последний опрос: {timezone.localtime(latest.started_at):%d.%m %H:%M}" if latest else "Отдельных ответов пока нет",
                     "url": reverse("patient_records", args=[p.user.pk])})
    return render(request, "quiz/ui/doctor.html", {**shell("doctor", role="doctor"), "patient_cards": rows, "q": query, "attention": attention, "list_url": reverse("home")})


@login_required
@require_GET
def patient_card(request, pk):
    try:
        require_patient_access(request.user, pk, clinical_write=True)
    except DomainError:
        raise PermissionDenied()
    patient = get_object_or_404(Profile.objects.select_related("user"), user_id=pk).user
    screening_runs = runs_for(patient).filter(questionnaire_version__questionnaire__code__in=[screening.CODE, checkin.CODE])
    latest_screening = screening_runs.filter(status="submitted").first() or screening_runs.first()
    patient_alerts = list(alerts.active_for(patient.pk))
    patient_alerts.sort(key=lambda a: {"urgent": 0, "high": 1, "review": 2}.get(a.evidence_snapshot.get("priority"), 3))
    return render(request, "quiz/ui/patient_card.html", {**shell("doctor", role="doctor"),
        "screening_result": screening.results(latest_screening), "screening_run": latest_screening,
        "response_quality": response_quality.describe(latest_screening), "patient_alerts": patient_alerts,
        "patient_alert_groups": alerts.card_groups(patient_alerts),
        "patient": patient, "patient_name": patient.get_full_name() or patient.username,
        "patient_state": "attention" if patient_alerts else "normal",
        "records": patient.survey_records.all(), "survey_runs": runs_for(patient)[:100],
        **calendar_context(request, real_entries(patient, request), reverse("patient_records", args=[pk])),
        "card_demo": False, **care_context(request, patient, doctor=True)})


@require_GET
def preview(request, screen):
    # A separate synthetic tour shares the same templates; it never queries patient data.
    if not settings.DEMO_SURVEY_ENABLED:
        raise PermissionDenied()
    allowed = {"today", "activity", "medicines", "contact", "doctor", "patient", "moderator", "survey"}
    if screen not in allowed:
        from django.http import Http404
        raise Http404()
    role = "doctor" if screen in {"doctor", "patient"} else "moderator" if screen == "moderator" else "patient"
    context = shell(screen, demo=True, role=role)
    state = request.GET.get("state", "normal")
    if state not in {"normal", "empty", "attention"}:
        state = "normal"
    if screen == "patient":
        default_patient = next(p for p in ui_demo.PATIENTS if p["state"] == state)
        p = next((p for p in ui_demo.PATIENTS if p["code"] == request.GET.get("patient")), default_patient)
        state = p["state"]
    context.update(demo_state=state, feature_demo=True, medicines=ui_demo.MEDICINES if state != "empty" else [], doctors=ui_demo.DOCTORS if state != "empty" else [])
    if screen == "today":
        context["demo_entries"] = ui_demo.entries(state)
        context.update(calendar_context(request, ui_demo.entries(state), reverse("ui_preview", args=[screen]), demo=True))
    if screen in {"activity", "patient"}:
        context.update(calendar_context(request, ui_demo.entries(state), reverse("ui_preview", args=[screen]), demo=True))
    if screen == "doctor":
        query = request.GET.get("q", "")[:100]
        attention = request.GET.get("filter") == "attention"
        context.update(q=query, attention=attention, list_url=reverse("ui_preview", args=["doctor"]),
            patient_cards=[{**p, "url": reverse("ui_preview", args=["patient"]) + "?" + urlencode({"patient": p["code"], "state": p["state"]})} for p in ui_demo.PATIENTS if query.casefold() in p["name"].casefold() and (not attention or p["state"] == "attention")] if state != "empty" else [])
    if screen == "patient":
        context.update(patient_name=p["name"], patient_state=p["state"], routing=p["routing"], routing_reason=p["reason"], card_demo=True)
    if screen == "survey":
        from quiz.services.checkin import definition
        context["sample_question"] = definition()["questions"][0]
    if screen == "moderator":
        context.update(demo_doctor=ui_demo.DOCTORS[0], demo_patient=ui_demo.PATIENTS[0])
    template = "patient_card" if screen == "patient" else screen
    return render(request, f"quiz/ui/{template}.html", context)
