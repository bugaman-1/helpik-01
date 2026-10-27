"""Legacy demonstration only. B03 replaces this with published content workflows."""
import hashlib
import json
import uuid
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from quiz.models import ContentVersion, SurveySession, AnswerRevision, Question, SurveyRecord
from .access import has_role
from .contracts import DomainError


def require_demo(patient):
    if not settings.DEMO_SURVEY_ENABLED or not has_role(patient, "patient"):
        raise DomainError("forbidden")


@transaction.atomic
def start_demo(patient, key):
    require_demo(patient)
    key = uuid.UUID(str(key))
    existing = SurveySession.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.patient_id != patient.pk:
            raise DomainError("forbidden")
        return existing
    questions = list(Question.objects.order_by("order", "pk").values("id", "text", "order"))
    if not questions:
        raise DomainError("invalid_input")
    definition = {"questions": questions, "options": ["positive", "neutral", "negative"], "clinical": False}
    code = "legacy-demo-" + hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:32]
    content, _ = ContentVersion.objects.get_or_create(code=code, version=1, defaults={"kind": "demo", "definition": definition})
    return SurveySession.objects.create(patient=patient, content=content, idempotency_key=key)


@transaction.atomic
def save_demo_answer(patient, run_id, question_id, value):
    require_demo(patient)
    run = SurveySession.objects.filter(pk=run_id, patient=patient, content__kind="demo").select_related("content").first()
    if not run:
        raise DomainError("forbidden")
    questions = run.content.definition["questions"]
    ids = [q["id"] for q in questions]
    if question_id not in ids or value not in run.content.definition["options"]:
        raise DomainError("invalid_input")
    code = str(question_id)
    existing = run.revisions.filter(question_code=code).first()
    if existing:
        if existing.value != value:
            raise DomainError("invalid_input")
    else:
        answered = set(run.revisions.values_list("question_code", flat=True))
        if run.status != "partial" or any(str(q) not in answered for q in ids[:ids.index(question_id)]):
            raise DomainError("invalid_input")
        AnswerRevision.objects.create(session=run, question_code=code, revision=1, state="known", value=value, client_event_id=uuid.uuid5(run.id, code))
    if run.revisions.count() == len(ids):
        values = list(run.revisions.values_list("value", flat=True))
        record, _ = SurveyRecord.objects.get_or_create(submission_key=run.id, defaults={"user": patient, "user_name": patient.username, "origin": "demo_aggregate", **{k + "_count": values.count(k) for k in run.content.definition["options"]}})
        SurveySession.objects.filter(pk=run.pk, status="partial").update(status="completed", completed_at=timezone.now())
        return None, record.pk
    return ids[ids.index(question_id) + 1], None
