"""Current operational access only. Never use legacy Profile.doctors."""
from quiz.models import Profile, CareRelationship
from .contracts import DomainError


def has_role(user, role):
    return bool(user and user.is_authenticated and Profile.objects.filter(user_id=user.pk, user__is_active=True, role=role).exists())


def is_moderator(user):
    return has_role(user, "moderator") and Profile.objects.filter(user_id=user.pk, moderator_enabled=True).exists()


def is_approved_doctor(user):
    return has_role(user, "doctor") and Profile.objects.filter(user_id=user.pk, verification_status="approved").exists()


def require_moderator(user):
    if not is_moderator(user):
        raise DomainError("forbidden")


def can_access_patient(user, patient_id, *, clinical_write=False):
    if not Profile.objects.filter(user_id=patient_id, role="patient", user__is_active=True).exists():
        return False
    if not clinical_write and has_role(user, "patient") and user.pk == patient_id:
        return True
    return is_approved_doctor(user) and CareRelationship.objects.filter(patient_id=patient_id, doctor_id=user.pk, status="active").exists()


def require_patient_access(user, patient_id, *, clinical_write=False):
    if not can_access_patient(user, patient_id, clinical_write=clinical_write):
        raise DomainError("forbidden")


def accessible_patients(user):
    if not is_approved_doctor(user):
        return Profile.objects.none()
    return Profile.objects.filter(role="patient", user__is_active=True, user__care_doctors__doctor_id=user.pk, user__care_doctors__status="active").select_related("user")
