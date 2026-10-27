"""Versioned collection only: no clinical scoring, publication or notifications."""
import re
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from quiz.models import (Questionnaire, QuestionnaireVersion, ContentVersion,
                         SurveyAssignment, SurveySession, AnswerRevision, Outbox)
from .access import has_role, require_patient_access, require_moderator
from .contracts import DomainError, AnswerCommand

SCHEMA = "survey-v2"
CODE = re.compile(r"^[a-zA-Z0-9_.-]{1,100}$")


def invalid():
    raise DomainError("invalid_input")


def uid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        invalid()


def integer(value, maximum=1000000):
    if type(value) is not int or not 0 <= value <= maximum:
        invalid()
    return value


def validate_definition(d):
    """Narrow, explicit single-choice format; unsupported types fail closed."""
    if not isinstance(d, dict) or set(d) != {"schema", "instruction", "recall_window", "source", "completeness", "questions"}:
        invalid()
    if d["schema"] != SCHEMA or d["completeness"] != "all_required":
        invalid()
    for field in ("instruction", "recall_window", "source"):
        if not isinstance(d[field], str) or not 1 <= len(d[field]) <= 4000:
            invalid()
    qs = d["questions"]
    if not isinstance(qs, list) or not 1 <= len(qs) <= 100:
        invalid()
    seen = set()
    for q in qs:
        if not isinstance(q, dict) or set(q) != {"code", "text", "required", "options", "allow_not_applicable", "applicable_when", "safety_relevant"}:
            invalid()
        if not isinstance(q["code"], str) or not CODE.fullmatch(q["code"]) or q["code"] in seen:
            invalid()
        if not isinstance(q["text"], str) or not 1 <= len(q["text"]) <= 4000:
            invalid()
        if any(type(q[k]) is not bool for k in ("required", "allow_not_applicable", "safety_relevant")):
            invalid()
        cond = q["applicable_when"]
        if cond is not None:
            if not isinstance(cond, dict) or set(cond) != {"question", "option"} or cond["question"] not in seen:
                invalid()
            parent = next(x for x in qs if x["code"] == cond["question"])
            if cond["option"] not in [o["code"] for o in parent["options"]] or not q["allow_not_applicable"]:
                invalid()
        opts = q["options"]
        if not isinstance(opts, list) or not 1 <= len(opts) <= 30:
            invalid()
        codes = set()
        for opt in opts:
            if not isinstance(opt, dict) or set(opt) != {"code", "label"}:
                invalid()
            if not isinstance(opt["code"], str) or not CODE.fullmatch(opt["code"]) or opt["code"] in codes:
                invalid()
            if not isinstance(opt["label"], str) or not 1 <= len(opt["label"]) <= 1000:
                invalid()
            codes.add(opt["code"])
        seen.add(q["code"])
    return d


@transaction.atomic
def create_version(actor, questionnaire, definition, *, status="draft"):
    require_moderator(actor)
    if status not in {"draft", "demo"} or (status == "demo" and not settings.DEMO_SURVEY_ENABLED):
        raise DomainError("clinical_publication_disabled")
    validate_definition(definition)
    if not CODE.fullmatch(questionnaire.code) or len(questionnaire.code) > 93:
        invalid()
    Questionnaire.objects.select_for_update().get(pk=questionnaire.pk)
    last = QuestionnaireVersion.objects.filter(questionnaire=questionnaire).order_by("-number").first()
    number = last.number + 1 if last else 1
    content = ContentVersion.objects.create(code="survey-" + questionnaire.code, version=number,
        kind="questionnaire", definition=definition, source_snapshot={"source": definition["source"]})
    return QuestionnaireVersion.objects.create(questionnaire=questionnaire, number=number, content=content, status=status)


def available_version(version):
    if version.status != "demo" or not settings.DEMO_SURVEY_ENABLED:
        raise DomainError("clinical_publication_disabled")


@transaction.atomic
def assign(actor, patient, version, *, key, timezone_name, available_from, available_until,
           observation_start, observation_end, window_code, unscheduled_limit=0):
    require_patient_access(actor, patient.pk, clinical_write=True)
    available_version(version)
    return _assign(actor, patient, version, key=key, timezone_name=timezone_name,
        available_from=available_from, available_until=available_until,
        observation_start=observation_start, observation_end=observation_end,
        window_code=window_code, unscheduled_limit=unscheduled_limit)


def _assign(actor, patient, version, *, key, timezone_name, available_from, available_until,
            observation_start, observation_end, window_code, unscheduled_limit=0):
    # Internal provisioning helper; the public service above enforces B02 access.
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        invalid()
    if any(not timezone.is_aware(t) for t in [available_from, available_until, observation_start, observation_end]):
        invalid()
    if available_from >= available_until or observation_start > observation_end:
        invalid()
    if not isinstance(window_code, str) or not CODE.fullmatch(window_code):
        invalid()
    integer(unscheduled_limit, 10)
    fields = dict(patient_id=patient.pk, assigned_by_id=actor.pk, version_id=version.pk, timezone=timezone_name,
        available_from=available_from, available_until=available_until, observation_start=observation_start,
        observation_end=observation_end, window_code=window_code, unscheduled_limit=unscheduled_limit)
    old = SurveyAssignment.objects.filter(idempotency_key=uid(key)).first()
    if old:
        if any(getattr(old, k) != v for k, v in fields.items()):
            raise DomainError("idempotency_conflict")
        return old
    return SurveyAssignment.objects.create(idempotency_key=uid(key), **fields)


def own_session(patient, session_id, *, lock=False):
    if not has_role(patient, "patient"):
        raise DomainError("forbidden")
    query = SurveySession.objects.select_related("questionnaire_version__content", "assignment")
    if lock:
        query = query.select_for_update()
    run = query.filter(pk=uid(session_id), patient=patient, questionnaire_version__isnull=False).first()
    if not run:
        raise DomainError("forbidden")
    return run


@transaction.atomic
def start(patient, assignment_id, key, *, unscheduled=False):
    if not has_role(patient, "patient") or type(unscheduled) is not bool:
        raise DomainError("forbidden")
    assignment = SurveyAssignment.objects.select_for_update().filter(pk=assignment_id, patient=patient).select_related("version__content").first()
    if not assignment:
        raise DomainError("forbidden")
    available_version(assignment.version)
    key = uid(key)
    slot = str(key) if unscheduled else "planned"
    existing = SurveySession.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.patient_id != patient.pk or existing.assignment_id != assignment.pk or existing.slot != slot:
            raise DomainError("idempotency_conflict")
        return existing
    old = SurveySession.objects.filter(assignment=assignment, slot=slot).first()
    if old:
        return old
    now = timezone.now()
    if not assignment.available_from <= now < assignment.available_until:
        raise DomainError("window_closed")
    if unscheduled and SurveySession.objects.filter(assignment=assignment).exclude(slot="planned").count() >= assignment.unscheduled_limit:
        raise DomainError("unscheduled_limit")
    return SurveySession.objects.create(patient=patient, content=assignment.version.content,
        questionnaire_version=assignment.version, assignment=assignment, slot=slot, status="draft",
        timezone=assignment.timezone, observation_start=assignment.observation_start,
        observation_end=assignment.observation_end, idempotency_key=key)


def current_answers(run):
    result = {}
    for answer in run.revisions.order_by("question_code", "revision"):
        result[answer.question_code] = answer
    return result


def applicability(question, answers, questions):
    cond = question["applicable_when"]
    if cond is None:
        return True
    parent_question = next(q for q in questions if q["code"] == cond["question"])
    parent_applicable = applicability(parent_question, answers, questions)
    if parent_applicable is not True:
        return parent_applicable
    parent = answers.get(cond["question"])
    if parent is None or parent.state != "known":
        return None
    return parent.value == cond["option"]


def validate_answer(question, state, value, answers, questions):
    if state not in {"known", "missing", "not_applicable"}:
        invalid()
    applies = applicability(question, answers, questions)
    if state == "known":
        if applies is not True or type(value) is not str or value not in [o["code"] for o in question["options"]]:
            invalid()
    elif value is not None:
        invalid()
    elif state == "not_applicable":
        if not question["allow_not_applicable"] or (question["applicable_when"] is not None and applies is not False):
            invalid()


@transaction.atomic
def save_answer(patient, command: AnswerCommand):
    run = own_session(patient, command.session_id, lock=True)
    available_version(run.questionnaire_version)
    integer(command.expected_revision)
    key = uid(command.client_event_id)
    old = run.revisions.filter(client_event_id=key).first()
    if old:
        if (old.question_code, old.expected_revision, old.state, old.value) != (command.question_code, command.expected_revision, command.state, command.value):
            raise DomainError("idempotency_conflict")
        return old
    if run.status not in {"draft", "in_progress"}:
        raise DomainError("session_closed")
    qs = run.questionnaire_version.content.definition["questions"]
    question = next((q for q in qs if q["code"] == command.question_code), None)
    if question is None:
        invalid()
    answers = current_answers(run)
    current = answers.get(command.question_code)
    if (current.revision if current else 0) != command.expected_revision:
        raise DomainError("stale_version")
    validate_answer(question, command.state, command.value, answers, qs)
    revision = AnswerRevision.objects.create(session=run, question_code=command.question_code,
        revision=command.expected_revision + 1, expected_revision=command.expected_revision,
        state=command.state, value=command.value, client_event_id=key,
        session_generation=run.answer_generation + 1,
        window_start=run.observation_start, window_end=run.observation_end)
    run.answer_generation += 1
    run.status = "in_progress"
    run.save(update_fields=["answer_generation", "status"])
    if question["safety_relevant"]:
        Outbox.objects.create(event_key=f"survey-safety:{revision.pk}", kind="safety.check_requested",
            payload={"schema": SCHEMA, "session_id": str(run.pk), "answer_revision_id": revision.pk,
                     "questionnaire_version_id": run.questionnaire_version_id}, available_at=timezone.now())
        from .alerts import evaluate
        evaluate(run)
    return revision


@transaction.atomic
def finish(patient, session_id, key, expected_generation, *, abandon=False):
    run = own_session(patient, session_id, lock=True)
    available_version(run.questionnaire_version)
    key = uid(key)
    integer(expected_generation)
    target = "abandoned" if abandon else "submitted"
    if run.answer_generation != expected_generation:
        raise DomainError("stale_version")
    if run.status == target:
        return run
    if run.status not in {"draft", "in_progress"}:
        raise DomainError("session_closed")
    if not abandon:
        answers = current_answers(run)
        qs = run.questionnaire_version.content.definition["questions"]
        for q in qs:
            answer = answers.get(q["code"])
            if answer:
                validate_answer(q, answer.state, answer.value, answers, qs)
            applies = applicability(q, answers, qs)
            if q["required"] and (not answer or answer.state == "missing" or applies is None):
                raise DomainError("incomplete")
    run.status, run.submit_key, run.completed_at = target, key, timezone.now()
    run.save(update_fields=["status", "submit_key", "completed_at"])
    if not abandon:
        from .alerts import evaluate
        evaluate(run)
        from .sitec import record_completed
        record_completed(run)
    Outbox.objects.create(event_key=f"survey-{target}:{run.pk}", kind=f"survey.{target}",
        payload={"schema": SCHEMA, "session_id": str(run.pk), "answer_generation": run.answer_generation},
        available_at=timezone.now())
    return run


@transaction.atomic
def canonical_snapshot(actor, session_id):
    run = SurveySession.objects.select_related("questionnaire_version__content").get(pk=uid(session_id), questionnaire_version__isnull=False)
    require_patient_access(actor, run.patient_id)
    answers = current_answers(run)
    return {"schema": SCHEMA, "session_id": str(run.pk), "questionnaire_version_id": run.questionnaire_version_id,
        "status": run.status, "answer_generation": run.answer_generation, "timezone": run.timezone,
        "observation_start": run.observation_start.isoformat(), "observation_end": run.observation_end.isoformat(),
        "answers": [{"question_code": q["code"], "state": answers[q["code"]].state if q["code"] in answers else "missing",
            "value": answers[q["code"]].value if q["code"] in answers else None,
            "revision_id": answers[q["code"]].pk if q["code"] in answers else None}
            for q in run.questionnaire_version.content.definition["questions"]],
        "safety_revision_ids": list(run.revisions.filter(question_code__in=[q["code"] for q in run.questionnaire_version.content.definition["questions"] if q["safety_relevant"]]).order_by("pk").values_list("pk", flat=True))}
