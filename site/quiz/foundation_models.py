"""B01 schema. Use domain services; clinical consumers are not enabled yet."""
import uuid
from django.conf import settings
from django.db import models
from django.db.models import Q, F

USER = settings.AUTH_USER_MODEL


class AppendOnlyQuerySet(models.QuerySet):
    def bulk_create(self, objs, **kwargs):
        if kwargs.get("update_conflicts"):
            raise ValueError("Append-only record")
        return super().bulk_create(objs, **kwargs)

    def update(self, **kwargs):
        raise ValueError("Append-only record; create a new revision")

    def delete(self):
        raise ValueError("Append-only record")

    def bulk_update(self, *args, **kwargs):
        raise ValueError("Append-only record")


class AppendOnly(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Append-only record; create a new revision")
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Append-only record")


class CareRelationship(models.Model):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="care_doctors")
    doctor = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="care_patients")
    status = models.CharField(max_length=8, default="active", choices=[("active", "Активна"), ("ended", "Завершена")])
    started_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    is_primary = models.BooleanField(default=False)
    origin = models.CharField(max_length=24, default="invitation")
    reason = models.CharField(max_length=500)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patient", "doctor"], condition=Q(status="active"), name="care_active_pair"),
            models.UniqueConstraint(fields=["patient"], condition=Q(status="active", is_primary=True), name="care_one_primary"),
            models.CheckConstraint(condition=~Q(patient=F("doctor")), name="care_not_self"),
            models.CheckConstraint(condition=Q(status="active", ended_at__isnull=True) | Q(status="ended", ended_at__isnull=False, is_primary=False), name="care_status_dates"),
        ]


class AuditEvent(AppendOnly):
    actor = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, related_name="audit_events")
    kind = models.CharField(max_length=64)
    subject_id = models.PositiveBigIntegerField(null=True)
    reason = models.CharField(max_length=500)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Invitation(models.Model):
    token_digest = models.CharField(max_length=64, unique=True)
    role = models.CharField(max_length=10, choices=[("doctor", "Врач"), ("patient", "Пациент")])
    issuer = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="issued_invitations")
    qualification_reference = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    used_at = models.DateTimeField(null=True)
    used_by = models.ForeignKey(USER, on_delete=models.PROTECT, null=True, related_name="used_invitations")


class RateLimitBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window = models.BigIntegerField()
    count = models.PositiveIntegerField(default=0)


class ContentVersion(AppendOnly):
    """Immutable draft definition. Publication requires a later approved adapter."""
    code = models.CharField(max_length=100)
    version = models.PositiveIntegerField()
    kind = models.CharField(max_length=24)
    definition = models.JSONField()
    source_snapshot = models.JSONField(default=dict)
    status = models.CharField(max_length=12, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["code", "version"], name="content_version_unique"), models.CheckConstraint(condition=Q(status="draft"), name="content_draft_until_b04")]


class SurveySession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(USER, on_delete=models.PROTECT)
    content = models.ForeignKey(ContentVersion, on_delete=models.PROTECT)
    status = models.CharField(max_length=12, default="partial")
    timezone = models.CharField(max_length=64, default="Europe/Moscow")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    idempotency_key = models.UUIDField(unique=True)
    questionnaire_version = models.ForeignKey("quiz.QuestionnaireVersion", on_delete=models.PROTECT, null=True)
    assignment = models.ForeignKey("quiz.SurveyAssignment", on_delete=models.PROTECT, null=True)
    slot = models.CharField(max_length=64, null=True)
    observation_start = models.DateTimeField(null=True)
    observation_end = models.DateTimeField(null=True)
    answer_generation = models.PositiveIntegerField(default=0)
    submit_key = models.UUIDField(null=True)
    telemetry_enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["assignment", "slot"], name="session_assignment_slot")]


class AnswerRevision(AppendOnly):
    session = models.ForeignKey(SurveySession, on_delete=models.PROTECT, related_name="revisions")
    question_code = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    state = models.CharField(max_length=24)
    value = models.JSONField(null=True)
    window_start = models.DateTimeField(null=True)
    window_end = models.DateTimeField(null=True)
    received_at = models.DateTimeField(auto_now_add=True)
    client_event_id = models.UUIDField()
    expected_revision = models.PositiveIntegerField(null=True)
    session_generation = models.PositiveIntegerField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["session", "question_code", "revision"], name="answer_revision_unique"),
            models.UniqueConstraint(fields=["session", "client_event_id"], name="answer_event_unique"),
            models.CheckConstraint(condition=Q(revision__gte=1), name="answer_revision_positive"),
            models.CheckConstraint(condition=Q(state="known", value__isnull=False) | Q(state__in=["missing", "not_applicable", "not_checked", "calculation_error"], value__isnull=True), name="answer_state_value"),
        ]


class Assessment(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT)
    session = models.ForeignKey(SurveySession, on_delete=models.PROTECT, null=True)
    algorithm_version = models.CharField(max_length=100)
    schema_version = models.CharField(max_length=24, default="integration-v1")
    input_snapshot = models.JSONField()
    result = models.JSONField()
    status = models.CharField(max_length=24)
    idempotency_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ClinicalAction(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="clinical_actions")
    clinician = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="confirmed_actions")
    proposal_id = models.UUIDField(null=True, blank=True)
    proposal_version = models.PositiveIntegerField(null=True, blank=True)
    expected_plan_version = models.PositiveIntegerField()
    kind = models.CharField(max_length=32)
    decision = models.CharField(max_length=12)
    reason = models.TextField()
    idempotency_key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PrescriptionVersion(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT)
    version = models.PositiveIntegerField()
    action = models.OneToOneField(ClinicalAction, on_delete=models.PROTECT)
    definition = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["patient", "version"], name="prescription_patient_version")]


class Alert(models.Model):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="alerts")
    responsible = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="assigned_alerts")
    evidence = models.ForeignKey(AnswerRevision, on_delete=models.PROTECT, null=True)
    evidence_snapshot = models.JSONField()
    status = models.CharField(max_length=16, default="open", choices=[("open", "Открыт"), ("acknowledged", "Принят врачом"), ("closed", "Закрыт врачом")])
    episode_key = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Outbox(models.Model):
    event_key = models.CharField(max_length=150, unique=True)
    kind = models.CharField(max_length=64)
    alert = models.ForeignKey(Alert, on_delete=models.PROTECT, null=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default="pending")
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField()
    lease_until = models.DateTimeField(null=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["status", "available_at"], name="outbox_due")]
