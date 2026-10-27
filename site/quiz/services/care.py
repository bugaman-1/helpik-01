from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from quiz.models import Profile, CareRelationship, AuditEvent, Alert, Outbox
from .access import require_moderator, is_approved_doctor
from .contracts import DomainError


def reason_required(reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise DomainError("reason_required")
    return reason.strip()


def audit(actor, kind, subject_id, reason, **metadata):
    AuditEvent.objects.create(actor=actor, kind=kind, subject_id=subject_id, reason=reason, metadata=metadata)


def _link(patient, doctor, actor, reason, origin="invitation"):
    """Caller owns an IMMEDIATE atomic transaction."""
    if not Profile.objects.filter(user=patient, role="patient", user__is_active=True).exists() or not is_approved_doctor(doctor):
        raise DomainError("forbidden")
    existing = CareRelationship.objects.filter(patient=patient, doctor=doctor, status="active").first()
    if existing:
        return existing
    # First new relationship gets responsibility; legacy imports require explicit selection.
    primary = not CareRelationship.objects.filter(patient=patient, status="active").exists()
    relationship = CareRelationship.objects.create(patient=patient, doctor=doctor, started_at=timezone.now(), reason=reason, is_primary=primary, origin=origin)
    audit(actor, "care.attached", patient.pk, reason, relationship_id=relationship.pk, doctor_id=doctor.pk, primary=primary)
    from .alerts import sync_patient
    sync_patient(patient.pk)
    return relationship


@transaction.atomic
def attach(actor, patient_id, doctor_id, reason):
    require_moderator(actor)
    reason = reason_required(reason)
    User = get_user_model()
    patient = User.objects.filter(pk=patient_id).first()
    doctor = User.objects.filter(pk=doctor_id).first()
    if not patient or not doctor:
        raise DomainError("forbidden")
    return _link(patient, doctor, actor, reason, "moderator")


def _transfer(actor, patient_id, replacement_id, reason):
    replacement = CareRelationship.objects.filter(patient_id=patient_id, doctor_id=replacement_id, status="active").select_related("doctor").first()
    if not replacement or not is_approved_doctor(replacement.doctor):
        raise DomainError("replacement_required")
    previous = list(CareRelationship.objects.filter(patient_id=patient_id, status="active", is_primary=True).values_list("doctor_id", flat=True))
    CareRelationship.objects.filter(patient_id=patient_id, status="active", is_primary=True).update(is_primary=False)
    CareRelationship.objects.filter(pk=replacement.pk).update(is_primary=True)
    alerts = Alert.objects.filter(patient_id=patient_id).exclude(status="closed").exclude(responsible_id=replacement_id)
    ids = list(alerts.values_list("id", flat=True))
    alerts.update(responsible_id=replacement_id)
    event = AuditEvent.objects.create(actor=actor, kind="care.responsibility_transferred", subject_id=patient_id, reason=reason, metadata={"previous_doctors": previous, "doctor_id": replacement_id, "alert_ids": ids})
    for alert_id in ids:
        Outbox.objects.create(event_key=f"care-transfer:{event.pk}:{alert_id}", kind="alert.responsibility_changed", alert_id=alert_id, available_at=timezone.now())


@transaction.atomic
def transfer(actor, patient_id, replacement_id, reason):
    require_moderator(actor)
    _transfer(actor, patient_id, replacement_id, reason_required(reason))


@transaction.atomic
def detach(actor, patient_id, doctor_id, reason, replacement_id=None, end_care=False):
    require_moderator(actor)
    reason = reason_required(reason)
    relationship = CareRelationship.objects.filter(patient_id=patient_id, doctor_id=doctor_id, status="active").first()
    if relationship is None:
        return  # Repeat does not create another event.
    alerts = Alert.objects.filter(patient_id=patient_id, responsible_id=doctor_id).exclude(status="closed")
    others = CareRelationship.objects.filter(patient_id=patient_id, status="active").exclude(pk=relationship.pk)
    if replacement_id == doctor_id:
        raise DomainError("replacement_required")
    if replacement_id:
        _transfer(actor, patient_id, replacement_id, reason)
    elif alerts.exists() or (relationship.is_primary and (others.exists() or not end_care)):
        raise DomainError("replacement_required")
    CareRelationship.objects.filter(pk=relationship.pk).update(status="ended", ended_at=timezone.now(), is_primary=False)
    audit(actor, "care.detached", patient_id, reason, relationship_id=relationship.pk, doctor_id=doctor_id, end_care=end_care)


@transaction.atomic
def verify_doctor(actor, doctor_id, status, reason):
    require_moderator(actor)
    reason = reason_required(reason)
    if status not in {"approved", "revoked"}:
        raise DomainError("invalid_input")
    profile = Profile.objects.filter(user_id=doctor_id, role="doctor").first()
    if not profile:
        raise DomainError("forbidden")
    if status == "revoked" and (CareRelationship.objects.filter(doctor_id=doctor_id, status="active", is_primary=True).exists() or Alert.objects.filter(responsible_id=doctor_id).exclude(status="closed").exists()):
        raise DomainError("replacement_required")
    if profile.verification_status != status:
        previous = profile.verification_status
        profile.verification_status = status
        profile.save(update_fields=["verification_status"])
        audit(actor, "doctor.verification", doctor_id, reason, previous=previous, status=status)
