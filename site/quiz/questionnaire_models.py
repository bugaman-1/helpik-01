"""survey-v2 additions; legacy aggregates and sessions are retained."""
import uuid
from django.db import models
from django.db.models import Q, F
from .foundation_models import AppendOnly, USER


class Questionnaire(AppendOnly):
    code = models.SlugField(max_length=100, unique=True)
    title = models.CharField(max_length=200)


class QuestionnaireVersion(AppendOnly):
    questionnaire = models.ForeignKey(Questionnaire, on_delete=models.PROTECT)
    number = models.PositiveIntegerField()
    content = models.OneToOneField("quiz.ContentVersion", on_delete=models.PROTECT)
    status = models.CharField(max_length=12, choices=[("draft", "Черновик"), ("demo", "Демонстрационная"), ("published", "Опубликована")])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["questionnaire", "number"], name="questionnaire_version_number"),
            models.CheckConstraint(condition=Q(status__in=["draft", "demo"]), name="questionnaire_publication_gate"),
        ]


class SurveyAssignment(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="survey_assignments")
    assigned_by = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="issued_surveys")
    version = models.ForeignKey(QuestionnaireVersion, on_delete=models.PROTECT)
    timezone = models.CharField(max_length=64)
    available_from = models.DateTimeField()
    available_until = models.DateTimeField()
    observation_start = models.DateTimeField()
    observation_end = models.DateTimeField()
    window_code = models.CharField(max_length=100)
    unscheduled_limit = models.PositiveSmallIntegerField(default=0)
    idempotency_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(available_until__gt=F("available_from")), name="assignment_availability_order"),
            models.CheckConstraint(condition=Q(observation_end__gte=F("observation_start")), name="assignment_observation_order"),
        ]


class InteractionEvent(AppendOnly):
    session = models.ForeignKey("quiz.SurveySession", on_delete=models.PROTECT, related_name="interactions")
    event_id = models.UUIDField(default=uuid.uuid4)
    stream_id = models.UUIDField()
    sequence = models.PositiveIntegerField()
    kind = models.CharField(max_length=32)
    question_code = models.CharField(max_length=100, blank=True)
    revision = models.ForeignKey("quiz.AnswerRevision", on_delete=models.PROTECT, null=True)
    client_at = models.DateTimeField()
    elapsed_ms = models.PositiveBigIntegerField()
    save_latency_ms = models.PositiveIntegerField(null=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "event_id"], name="interaction_event_unique"),
            models.UniqueConstraint(fields=["session", "stream_id", "sequence"], name="interaction_stream_sequence"),
        ]
        indexes = [models.Index(fields=["session", "received_at"], name="interaction_rate")]


class PushSubscription(models.Model):
    user = models.ForeignKey(USER, on_delete=models.CASCADE, related_name="push_subscriptions")
    endpoint = models.CharField(max_length=2048, unique=True)
    subscription = models.JSONField()
    origin = models.URLField()
    enabled = models.BooleanField(default=True)
    last_error = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
