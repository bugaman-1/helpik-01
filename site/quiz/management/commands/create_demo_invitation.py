"""Explicit temporary invitation for isolated synthetic registration testing."""
import json
from pathlib import Path
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from quiz.models import Profile, AuditEvent
from quiz.services.invitations import issue


class Command(BaseCommand):
    help = "Issue one temporary local demo patient invitation; never prints the token."

    def handle(self, *args, **options):
        if settings.APP_ENV != "demo" or not settings.DEMO_EMBEDDED_FORMS_ENABLED:
            raise CommandError("Explicit local demo mode is required.")
        path = Path(settings.DEMO_INVITATION_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise CommandError("Demo invitation delivery file already exists; not overwriting it.")
        # Exclusive reservation plus rollback prevents accidental replacement or
        # creating an invitation whose delivery artifact cannot be written.
        with path.open("x", encoding="utf-8") as stream:
            try:
                with transaction.atomic():
                    User = get_user_model()
                    if User.objects.filter(username="synthetic_invitation_doctor").exists():
                        raise CommandError("Synthetic issuer already exists; no account was changed.")
                    doctor = User(username="synthetic_invitation_doctor")
                    doctor.set_unusable_password()
                    doctor.save(force_insert=True)
                    Profile.objects.create(user=doctor, role="doctor", verification_status="approved")
                    AuditEvent.objects.create(actor=doctor, kind="demo.synthetic_issuer", subject_id=doctor.pk, reason="User-requested isolated synthetic registration test; not real credential verification")
                    invitation, token = issue(doctor, role="patient", hours=24)
                    json.dump({"token": token, "expires_at": invitation.expires_at.isoformat()}, stream)
                    stream.flush()
            except Exception:
                # Keep the reserved artifact as a failure marker, not a usable token.
                raise
        self.stdout.write("Temporary patient invitation prepared for the local registration form; expires in 24 hours. Token not logged.")
