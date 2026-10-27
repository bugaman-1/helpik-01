"""Explicit self-service entry points for the prototype environment."""
from django.conf import settings
from django.db import transaction
from quiz.models import Profile
from .access import is_approved_doctor
from .care import _link, audit
from .contracts import DomainError


def enabled():
    return settings.APP_ENV in {'demo', 'test'} and settings.PROTOTYPE_SELF_SERVICE_ENABLED


def require_enabled():
    if not enabled():
        raise DomainError('forbidden')


@transaction.atomic
def register_doctor(candidate):
    require_enabled()
    if candidate.pk is not None:
        raise DomainError('invalid_input')
    candidate.is_staff = candidate.is_superuser = False
    candidate.save(force_insert=True)
    Profile.objects.create(user=candidate, role='doctor', verification_status='approved',
                           moderator_enabled=False, preferences={'self_registered': True})
    audit(candidate, 'doctor.self_registered', candidate.pk, 'Самостоятельная регистрация врача в прототипе')
    return candidate


@transaction.atomic
def register_patient(candidate):
    require_enabled()
    if candidate.pk is not None:
        raise DomainError('invalid_input')
    candidate.is_staff = candidate.is_superuser = False
    candidate.save(force_insert=True)
    Profile.objects.create(user=candidate, role='patient', moderator_enabled=False,
                           preferences={'self_registered': True})
    audit(candidate, 'patient.self_registered', candidate.pk, 'Самостоятельная регистрация пациента без прикрепления')
    return candidate


@transaction.atomic
def attach_patient(actor, username):
    require_enabled()
    if not is_approved_doctor(actor):
        raise DomainError('forbidden')
    profile = Profile.objects.select_for_update().select_related('user').filter(
        role='patient', user__is_active=True, user__username=username).first()
    if not profile:
        raise DomainError('patient_not_found')
    return _link(profile.user, actor, actor, 'Пациент прикреплён врачом по логину', origin='doctor')
