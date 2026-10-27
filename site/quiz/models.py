from django.conf import settings
from django.db import models


class Question(models.Model):
    text = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.text


class Profile(models.Model):
    class Role(models.TextChoices):
        DOCTOR = "doctor", "Врач"
        PATIENT = "patient", "Пациент"
        MODERATOR = "moderator", "Модератор"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    verification_status = models.CharField(max_length=16, default="unverified", choices=[("unverified", "Не проверен"), ("approved", "Допущен"), ("revoked", "Отозван")])
    moderator_enabled = models.BooleanField(default=False)
    preferences = models.JSONField(default=dict, blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_address = models.CharField(max_length=300, blank=True)
    doctors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="patient_profiles",
    )

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"


class SurveyRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="survey_records",
    )
    origin = models.CharField(max_length=24, default="legacy_aggregate")
    submission_key = models.UUIDField(null=True, blank=True, unique=True)
    user_name = models.CharField(max_length=150)
    positive_count = models.PositiveIntegerField(default=0)
    neutral_count = models.PositiveIntegerField(default=0)
    negative_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user_name}: +{self.positive_count}/={self.neutral_count}/-{self.negative_count}"

from .foundation_models import (CareRelationship, AuditEvent, Invitation, RateLimitBucket, ContentVersion, SurveySession, AnswerRevision, Assessment, ClinicalAction, PrescriptionVersion, Alert, Outbox)
from .questionnaire_models import Questionnaire, QuestionnaireVersion, SurveyAssignment, InteractionEvent, PushSubscription
from .care_models import CareNote, CareMessage, MedicationIntake, AdverseEvent, ReportedMedication
