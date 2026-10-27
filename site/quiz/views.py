import calendar
from datetime import date

from django.contrib.auth import login, get_user_model
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from .access_views import domain_view
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import RegisterForm
from .models import Profile, Question, SurveyRecord

User = get_user_model()

MOODS = [
    ("positive", "😄", "Отлично"),
    ("neutral", "😐", "Нормально"),
    ("negative", "😞", "Плохо"),
]
MOOD_KEYS = [value for value, _, _ in MOODS]


def _role(user):
    return getattr(getattr(user, "profile", None), "role", None)


def _is_role(user, role):
    from .services.access import has_role
    return has_role(user, role)


def _mood_score(record):
    """Среднее настроение прохождения: -1 = 😞, 0 = 😐, +1 = 😄."""
    total = record.positive_count + record.neutral_count + record.negative_count
    if total == 0:
        return None
    return round((record.positive_count - record.negative_count) / total, 2)


def _mood_class(mood):
    if mood is None:
        return "none"
    if mood > 0.33:
        return "pos"
    if mood < -0.33:
        return "neg"
    return "neu"


def _mood_emoji(mood):
    if mood is None:
        return "—"
    if mood > 0.33:
        return "😄"
    if mood < -0.33:
        return "😞"
    return "😐"


MONTH_NAMES = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _activity_context(records):
    """Месячная сетка: среднее настроение по дням за последние 12 месяцев."""
    today = timezone.localdate()
    # 12 последних месяцев, включая текущий
    year, month = today.year, today.month
    for _ in range(11):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    start = date(year, month, 1)

    # среднее по каждой записи -> среднее по дням
    daily = {}
    for record in records.filter(created_at__date__gte=start):
        day = timezone.localtime(record.created_at).date()
        score = _mood_score(record)
        if score is not None:
            daily.setdefault(day, []).append(score)
    day_mood = {day: round(sum(scores) / len(scores), 2) for day, scores in daily.items()}

    # 12 месяцев, начиная с 11 месяцев назад
    months = []
    for _ in range(12):
        last_day = calendar.monthrange(year, month)[1]
        cells = []
        for day in range(1, 32):
            if day > last_day:
                cells.append({"day": day, "date": None, "is_future": False, "mood": None, "mood_class": "empty", "empty": True})
                continue
            d = date(year, month, day)
            mood = day_mood.get(d)
            cells.append({
                "day": day,
                "date": d.isoformat(),
                "is_future": d > today,
                "mood": mood,
                "mood_class": "none" if d > today else _mood_class(mood),
                "emoji": _mood_emoji(mood) if mood is not None else "",
                "empty": False,
            })
        months.append({
            "year": year,
            "month": month,
            "label": f"{month:02d}",
            "name": MONTH_NAMES[month - 1],
            "cells": cells,
        })
        month += 1
        if month == 13:
            month = 1
            year += 1

    # сводка по месяцам (среднее по дням с данными)
    monthly = []
    for m in months:
        values = [c["mood"] for c in m["cells"] if c["mood"] is not None]
        avg = round(sum(values) / len(values), 2) if values else None
        monthly.append({
            "label": m["label"],
            "name": m["name"],
            "avg": avg,
            "days": len(values),
            "emoji": _mood_emoji(avg),
            "bar_class": _mood_class(avg),
        })

    total_days = len(day_mood)
    overall = round(sum(day_mood.values()) / total_days, 2) if total_days else None

    return {
        "months": months,
        "monthly_avg": monthly,
        "activity_days": total_days,
        "activity_avg": overall,
        "activity_emoji": _mood_emoji(overall),
    }


@login_required
def home(request):
    from uuid import uuid4
    from django.conf import settings
    from .services.access import is_moderator, is_approved_doctor, accessible_patients, has_role
    if is_approved_doctor(request.user):
        rows = [{"patient": p.user, "records": p.user.survey_records.all()} for p in accessible_patients(request.user)]
        return render(request, "quiz/dashboard.html", {"patients": rows})
    if is_moderator(request.user):
        patients = Profile.objects.filter(role="patient").select_related("user").prefetch_related("user__care_doctors__doctor")
        doctors = Profile.objects.filter(role="doctor").select_related("user")
        return render(request, "quiz/moderator.html", {"patients": patients, "doctors": doctors})
    if has_role(request.user, "patient"):
        return render(request, "quiz/patient_home.html", {"demo_enabled": settings.DEMO_SURVEY_ENABLED, "start_key": str(uuid4())})
    return render(request, "quiz/pending.html", status=403)


@login_required
def patient_records(request, pk):
    from .services.access import can_access_patient
    if not can_access_patient(request.user, pk, clinical_write=True):
        raise PermissionDenied()
    patient = get_object_or_404(User, pk=pk)
    records = patient.survey_records.all()
    from .models import SurveySession
    survey_runs = SurveySession.objects.filter(patient=patient, questionnaire_version__isnull=False).order_by("-started_at")[:100]
    return render(request, "quiz/patient_records.html", {"patient": patient, "records": records, "survey_runs": survey_runs, **_activity_context(records)})


@login_required
def activity(request):
    if not _is_role(request.user, Profile.Role.PATIENT):
        raise PermissionDenied()
    records = request.user.survey_records.all()
    activity = _activity_context(records)
    records = records.order_by("-created_at")
    return render(
        request,
        "quiz/activity.html",
        {"records": records, **activity},
    )


@login_required
@require_POST
@domain_view
def start_survey(request):
    from .services.surveys import start_demo
    run = start_demo(request.user, request.POST.get("idempotency_key", ""))
    request.session["demo_run_id"] = str(run.pk)
    first = run.content.definition["questions"][0]["id"]
    return redirect("question", pk=first)


@login_required
@require_http_methods(["GET", "POST"])
@domain_view
def question(request, pk):
    from .models import SurveySession
    from .services.surveys import require_demo, save_demo_answer
    require_demo(request.user)
    run_id = request.POST.get("run_id") if request.method == "POST" else request.session.get("demo_run_id")
    if not run_id:
        return redirect("home")
    run = get_object_or_404(SurveySession.objects.select_related("content"), pk=run_id, patient=request.user, content__kind="demo")
    questions = run.content.definition["questions"]
    q = next((q for q in questions if q["id"] == pk), None)
    if q is None:
        return HttpResponseBadRequest("Неизвестный вопрос")
    if request.method == "POST":
        next_id, record_id = save_demo_answer(request.user, run_id, pk, request.POST.get("value"))
        if record_id:
            request.session["last_record_id"] = record_id
            return redirect("stats")
        return redirect("question", pk=next_id)
    return render(request, "quiz/question.html", {"question": q, "moods": MOODS, "run_id": run.pk, "question_count": len(questions)})


@login_required
def stats(request):
    if not _is_role(request.user, Profile.Role.PATIENT):
        raise PermissionDenied()
    record = SurveyRecord.objects.filter(
        id=request.session.get("last_record_id"),
        user=request.user,
    ).first()
    if record is None:
        return render(request, "quiz/stats.html", {"rows": [], "total": 0, "total_passes": 0})

    counts = {
        "positive": record.positive_count,
        "neutral": record.neutral_count,
        "negative": record.negative_count,
    }
    total = sum(counts.values())

    rows = []
    for value, emoji, label in MOODS:
        count = counts[value]
        percent = round(count / total * 100) if total else 0
        rows.append({"value": value, "emoji": emoji, "label": label, "count": count, "percent": percent})

    return render(
        request,
        "quiz/stats.html",
        {"rows": rows, "total": total, "total_passes": 1},
    )
