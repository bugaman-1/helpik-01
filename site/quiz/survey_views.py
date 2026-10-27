import json
import uuid
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.db import OperationalError
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.debug import sensitive_post_parameters
from django.utils import timezone
from quiz.models import SurveyAssignment, SurveySession
from quiz.services import questionnaires as service, paradata
from quiz.services.access import has_role, require_patient_access
from quiz.services.contracts import AnswerCommand, DomainError

ERRORS = {
    "forbidden": (403, "Доступ запрещён."),
    "stale_version": (409, "Ответы изменились в другой вкладке. Откройте сохранённые ответы и сравните со своим выбором."),
    "idempotency_conflict": (409, "Не удалось сохранить этот выбор. Откройте сохранённые ответы."),
    "session_closed": (409, "Прохождение уже закрыто. Откройте сохранённую версию."),
    "incomplete": (400, "Ответьте на все обязательные вопросы и сохраните ответы."),
    "clinical_publication_disabled": (409, "Клиническая версия ещё не опубликована."),
    "window_closed": (409, "Окно для начала прохождения закрыто."),
    "unscheduled_limit": (409, "Внеплановое прохождение недоступно по этому назначению."),
    "telemetry_disabled": (403, "Сбор событий выключен. Ответы можно сохранять."),
    "throttled": (429, "Достигнут технический лимит событий. Ответы можно сохранять."),
}


def error_response(request, code, message, status):
    if request.content_type == "application/json":
        return JsonResponse({"error": code, "message": message}, status=status)
    return render(request, "quiz/action_error.html", {"message": message}, status=status)


def endpoint(fn):
    @wraps(fn)
    def wrapped(request, *args, **kwargs):
        try:
            return fn(request, *args, **kwargs)
        except DomainError as exc:
            status, message = ERRORS.get(exc.code, (400, "Проверьте данные и применимость ответа."))
            return error_response(request, exc.code, message, status)
        except (ValueError, TypeError, OverflowError):
            return error_response(request, "invalid_input", "Проверьте формат данных.", 400)
        except OperationalError:
            response = error_response(request, "temporarily_unavailable", "Не удалось сохранить. Повторите отправку.", 503)
            response["Retry-After"] = "2"
            return response
    return wrapped


def body(request, fields):
    if len(request.body) > 16384:
        service.invalid()
    data = json.loads(request.body)
    if not isinstance(data, dict) or set(data) != set(fields):
        service.invalid()
    return data


@login_required
@require_GET
@endpoint
def index(request):
    if not has_role(request.user, "patient"):
        raise DomainError("forbidden")
    from quiz.services.diary import CODE
    from quiz.services.checkin import CODE as SCREENING_CODE
    assignments = SurveyAssignment.objects.filter(patient=request.user, version__questionnaire__code__in=[CODE, SCREENING_CODE], available_until__gt=timezone.now()).select_related("version__questionnaire").order_by("available_from")[:100]
    rows = [{"assignment": a, "key": uuid.uuid4(), "extra_key": uuid.uuid4()} for a in assignments]
    runs = SurveySession.objects.filter(patient=request.user, questionnaire_version__isnull=False).select_related("questionnaire_version__questionnaire").order_by("-started_at")[:100]
    for run in runs:
        run.status_label = {"draft": "Не начат", "in_progress": "Заполняется", "submitted": "Завершён", "abandoned": "Прекращён"}.get(run.status, "Нет данных")
    return render(request, "quiz/surveys.html", {"assignments": rows, "runs": runs})


@login_required
@require_POST
@endpoint
def screening_start(request):
    from quiz.services.checkin import start as begin
    run = begin(request.user)
    return redirect("survey_detail", session_id=run.pk)


@login_required
@require_POST
@endpoint
def start(request, assignment_id):
    run = service.start(request.user, assignment_id, request.POST.get("key"), unscheduled=request.POST.get("mode") == "unscheduled")
    return redirect("survey_detail", session_id=run.pk)


@login_required
@require_GET
@endpoint
def detail(request, session_id):
    run = get_object_or_404(SurveySession.objects.select_related("questionnaire_version__content", "questionnaire_version__questionnaire"), pk=session_id, questionnaire_version__isnull=False)
    require_patient_access(request.user, run.patient_id)
    answers = service.current_answers(run)
    definition = run.questionnaire_version.content.definition
    rows = [{"question": q, "answer": answers.get(q["code"]), "key": uuid.uuid4(),
             "applicability": service.applicability(q, answers, definition["questions"])} for q in definition["questions"]]
    # Vary only the extra item's position; preserve both original scale orders.
    control = next((row for row in rows if row["question"]["code"] == "CHECK01"), None)
    if control:
        rows.remove(control)
        is_daily = run.questionnaire_version.questionnaire.code == 'daily-checkin-opros2'
        rows.insert((19 + run.pk.int % 5) if is_daily else (13 + run.pk.int % 3), control)
    for row in rows:
        answer = row["answer"]
        row["label"] = answer_label(row["question"], answer) if answer else "Нет ответа"
        row["saved"] = answer is not None and answer.state != "missing"
        row["additional"] = row["question"]["code"] in {"A01", "A02", "A03", "A04"}
        row["na_hint"] = {"D10": "Выберите «Не применимо», если на сегодня нет назначенных лекарств.", "D11": "Выберите «Не применимо», если сегодня не работали и не учились."}.get(row["question"]["code"], "")
        condition = row["question"]["applicable_when"]
        if condition:
            parent = next(q for q in definition["questions"] if q["code"] == condition["question"])
            label = next(o["label"] for o in parent["options"] if o["code"] == condition["option"])
            row["condition_text"] = f'На вопрос «{parent["text"]}» выбран ответ «{label}».'
    writable = run.patient_id == request.user.pk and run.status in {"draft", "in_progress"}
    history = list(run.revisions.order_by("received_at", "pk"))
    readable_history, previous = [], {}
    questions_by_code = {q["code"]: q for q in definition["questions"]}
    for revision in history:
        question = questions_by_code[revision.question_code]
        label = answer_label(question, revision)
        readable_history.append({"question": question["text"], "old": previous.get(revision.question_code), "new": label, "at": revision.received_at})
        previous[revision.question_code] = label
    safety_codes = [q["code"] for q in definition["questions"] if q["safety_relevant"]]
    safety_history = []
    for revision in [r for r in history if r.question_code in safety_codes]:
        question = next(q for q in definition["questions"] if q["code"] == revision.question_code)
        label = {"missing": "Нет ответа", "not_applicable": "Не применимо"}.get(revision.state)
        if label is None:
            label = next((o["label"] for o in question["options"] if o["code"] == revision.value), "Неизвестное значение")
        safety_history.append({"question": question["text"], "revision": revision.revision, "label": label, "received_at": revision.received_at})
    from quiz.services.screening import results
    from quiz.services.response_quality import describe
    return render(request, "quiz/survey_detail.html", {"screening_result": results(run), "run": run, "definition": definition, "rows": rows,
        "writable": writable, "owner": run.patient_id == request.user.pk, "submit_key": uuid.uuid4(),
        "status_label": {"draft": "Не начат", "in_progress": "Заполняется", "submitted": "Завершён", "abandoned": "Прекращён"}.get(run.status, "Нет данных"),
        "history": readable_history, "safety_history": safety_history,
        "is_doctor": run.patient_id != request.user.pk,
        "response_quality": describe(run) if run.patient_id != request.user.pk else None,
        "saved_count": sum(row["saved"] for row in rows)})


def answer_label(question, answer):
    return {"missing": "Нет ответа", "not_applicable": "Не применимо"}.get(answer.state) or next((o["label"] for o in question["options"] if o["code"] == answer.value), "Нет данных")


@login_required
@require_POST
@sensitive_post_parameters()
@endpoint
def answer(request, session_id):
    if request.content_type == "application/json":
        data = body(request, ["question_code", "expected_revision", "client_event_id", "state", "value"])
    else:
        raw = request.POST.get("choice", "")
        data = {"question_code": request.POST.get("question_code"), "expected_revision": int(request.POST.get("expected_revision", "-1")),
                "client_event_id": request.POST.get("client_event_id"), "state": "known" if raw.startswith("option:") else raw,
                "value": raw[7:] if raw.startswith("option:") else None}
        if set(request.POST) - {"csrfmiddlewaretoken", "question_code", "expected_revision", "client_event_id", "choice"}:
            service.invalid()
    revision = service.save_answer(request.user, AnswerCommand(session_id=str(session_id), **data))
    if request.content_type != "application/json":
        return redirect("survey_detail", session_id=session_id)
    return JsonResponse({"revision": revision.revision, "revision_id": revision.pk,
        "answer_generation": revision.session_generation, "received_at": revision.received_at.isoformat()})


@login_required
@require_POST
@endpoint
def finish(request, session_id):
    json_request = request.content_type == "application/json"
    data = body(request, ["key", "expected_generation", "abandon"]) if json_request else {
        "key": request.POST.get("key"), "expected_generation": int(request.POST.get("expected_generation", "-1")),
        "abandon": request.POST.get("abandon") == "true"}
    if type(data["abandon"]) is not bool:
        service.invalid()
    run = service.finish(request.user, session_id, **data)
    return JsonResponse({"status": run.status}) if json_request else redirect("survey_detail", session_id=session_id)


@login_required
@require_POST
@endpoint
def telemetry_consent(request, session_id):
    paradata.consent(request.user, session_id, request.POST.get("enabled") == "true")
    return redirect("survey_detail", session_id=session_id)


@login_required
@require_POST
@endpoint
def events(request, session_id):
    data = body(request, ["events"])
    return JsonResponse({"accepted": paradata.ingest(request.user, session_id, data["events"])})
