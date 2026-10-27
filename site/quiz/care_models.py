"""Small persistent records for the MVP-2 care screens."""
from django.db import models
from .foundation_models import AppendOnly, USER


class CareNote(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="care_notes")
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="written_care_notes")
    text = models.TextField(max_length=4000)
    key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class CareMessage(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="care_messages")
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="written_care_messages")
    kind = models.CharField(max_length=16, choices=[("message", "Сообщение"), ("contact", "Запрос контакта")])
    text = models.TextField(max_length=4000)
    key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class MedicationIntake(models.Model):
    prescription = models.ForeignKey("quiz.PrescriptionVersion", on_delete=models.PROTECT, related_name="intakes")
    medication_id = models.UUIDField()
    due_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=[("taken", "Принято"), ("skipped", "Пропущено")])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["prescription", "medication_id", "due_at"], name="intake_prescription_slot"),
            models.CheckConstraint(condition=models.Q(status__in=["taken", "skipped"]), name="intake_valid_status"),
        ]


class AdverseEvent(AppendOnly):
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="adverse_events")
    medicine = models.CharField(max_length=200, blank=True)
    text = models.TextField(max_length=4000)
    key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReportedMedication(AppendOnly):
    """A reconciled external medicine; each correction is a new immutable row."""
    patient = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="reported_medications")
    author = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="recorded_medications")
    entry_id = models.UUIDField()
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    catalog_code = models.CharField(max_length=80, blank=True)
    regimen = models.CharField(max_length=500, blank=True)
    start = models.DateField(null=True, blank=True)
    end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=[("taking", "Принимает"), ("stopped", "Приём завершён")])
    source = models.CharField(max_length=16, choices=[("patient", "Со слов пациента"), ("documents", "Из медицинских документов"), ("doctor", "Уточнено врачом")])
    note = models.TextField(max_length=2000, blank=True)
    key = models.UUIDField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patient", "entry_id", "revision"], name="reported_medication_revision"),
            models.CheckConstraint(condition=models.Q(status__in=["taking", "stopped"]), name="reported_medication_status"),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name="reported_medication_positive_revision"),
        ]
