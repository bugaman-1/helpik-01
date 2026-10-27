from getpass import getpass
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from quiz.models import Profile, AuditEvent


class Command(BaseCommand):
    help = "Create a new moderator interactively, or explicitly reset an existing moderator. Never prints passwords."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--activate-existing", action="store_true")
        parser.add_argument("--reason", required=True)

    def handle(self, *args, **options):
        if not options["reason"].strip() or len(options["reason"]) > 500:
            raise CommandError("Administrative reason is required (max 500 characters).")
        User = get_user_model()
        existing = User.objects.filter(username=options["username"]).first()
        if existing and not (options["activate_existing"] and Profile.objects.filter(user=existing, role="moderator").exists()):
            raise CommandError("Account exists. No role or password was changed.")
        if not existing and options["activate_existing"]:
            raise CommandError("Existing moderator not found.")
        candidate = existing or User(username=options["username"])
        password = getpass("New password (hidden): ")
        if password != getpass("Repeat password (hidden): "):
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password, candidate)
            candidate.full_clean(exclude=["password"])
        except ValidationError as exc:
            raise CommandError("Invalid username or password; use a unique username and a stronger password.") from exc
        candidate.set_password(password)
        with transaction.atomic():
            candidate.save()
            profile, _ = Profile.objects.get_or_create(user=candidate, defaults={"role": "moderator"})
            if profile.role != "moderator":
                raise CommandError("Refusing to promote existing account.")
            profile.moderator_enabled = True
            profile.save(update_fields=["moderator_enabled"])
            AuditEvent.objects.create(actor=candidate, kind="moderator.bootstrap", subject_id=candidate.pk, reason=options["reason"], metadata={"existing": bool(existing)})
        self.stdout.write(self.style.SUCCESS("Moderator access configured. No credentials logged."))
