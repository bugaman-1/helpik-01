import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from quiz.models import Profile, Invitation, RateLimitBucket
from .access import is_approved_doctor, is_moderator, has_role
from .care import _link, audit, reason_required
from .contracts import DomainError


def digest_token(token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise DomainError("invitation_unavailable")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@transaction.atomic
def throttle(scope, identity, *, limit=10, seconds=900):
    # IP only from REMOTE_ADDR, never untrusted X-Forwarded-For.
    key = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{identity}".encode(), hashlib.sha256).hexdigest()
    window = int(timezone.now().timestamp()) // seconds
    bucket, _ = RateLimitBucket.objects.get_or_create(key=key, defaults={"window": window})
    if bucket.window != window:
        bucket.window, bucket.count = window, 0
    if bucket.count >= limit:
        return False
    bucket.count += 1
    bucket.save(update_fields=["window", "count"])
    return True


@transaction.atomic
def issue(actor, *, role, qualification_reference="", hours=48):
    if role != "patient" or not is_approved_doctor(actor):
        raise DomainError("forbidden")
    qualification_reference = ""
    if type(hours) is not int or not 1 <= hours <= 168:
        raise DomainError("invalid_input")
    token = secrets.token_urlsafe(32)
    invitation = Invitation.objects.create(token_digest=digest_token(token), role=role, issuer=actor, qualification_reference=qualification_reference, expires_at=timezone.now() + timedelta(hours=hours))
    audit(actor, "invitation.issued", invitation.pk, "Doctor qualification checked" if role == "doctor" else "Patient invitation", role=role)
    return invitation, token


def _available(digest):
    invitation = Invitation.objects.filter(token_digest=digest, used_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now()).select_related("issuer").first()
    if not invitation or invitation.role != "patient" or not is_approved_doctor(invitation.issuer):
        raise DomainError("invitation_unavailable")
    return invitation


def exchange(token):
    digest = digest_token(token)
    _available(digest)
    return digest


def _claim(invitation):
    changed = Invitation.objects.filter(pk=invitation.pk, used_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now()).update(used_at=timezone.now())
    if changed != 1:
        raise DomainError("invitation_unavailable")


@transaction.atomic
def register_from_context(digest, candidate):
    invitation = _available(digest)
    _claim(invitation)
    candidate.save(force_insert=True)
    Profile.objects.create(user=candidate, role=invitation.role, verification_status="approved" if invitation.role == "doctor" else "unverified")
    if invitation.role == "patient":
        _link(candidate, invitation.issuer, candidate, "Patient accepted invitation")
    Invitation.objects.filter(pk=invitation.pk).update(used_by=candidate)
    audit(candidate, "invitation.used", invitation.pk, "Registration", role=invitation.role)
    return candidate


@transaction.atomic
def accept_for_patient(actor, digest):
    if not has_role(actor, "patient"):
        raise DomainError("forbidden")
    invitation = _available(digest)
    if invitation.role != "patient":
        raise DomainError("invitation_unavailable")
    _claim(invitation)
    relationship = _link(actor, invitation.issuer, actor, "Existing patient explicitly accepted invitation")
    Invitation.objects.filter(pk=invitation.pk).update(used_by=actor)
    audit(actor, "invitation.used", invitation.pk, "Existing patient accepted relationship")
    return relationship


@transaction.atomic
def revoke(actor, invitation_id, reason):
    invitation = Invitation.objects.filter(pk=invitation_id).first()
    if not invitation or not (is_moderator(actor) or (is_approved_doctor(actor) and invitation.issuer_id == actor.pk)):
        raise DomainError("forbidden")
    reason = reason_required(reason)
    if Invitation.objects.filter(pk=invitation_id, used_at__isnull=True, revoked_at__isnull=True).update(revoked_at=timezone.now()):
        audit(actor, "invitation.revoked", invitation.pk, reason)
