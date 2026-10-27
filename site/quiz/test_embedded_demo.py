import io
import json
from pathlib import Path
import secrets
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.models import Invitation, Profile


@override_settings(APP_ENV="demo", DEMO_EMBEDDED_FORMS_ENABLED=True)
class EmbeddedDemoTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1:8000")
        self.delivery = settings.BASE_DIR / "runtime" / ("test-invitation-" + uuid.uuid4().hex + ".json")
        self.addCleanup(self.delivery.unlink, missing_ok=True)
        self.override = override_settings(DEMO_INVITATION_FILE=self.delivery)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def login_payload(self):
        self.client.get(reverse("login"))
        return {"username": "synthetic_unknown", "password": "synthetic-invalid", "csrfmiddlewaretoken": self.client.cookies["csrftoken"].value}

    def prepare_invitation(self):
        output = io.StringIO()
        call_command("create_demo_invitation", stdout=output)
        token = json.loads(self.delivery.read_text())["token"]
        self.assertNotIn(token, output.getvalue())
        return token

    def test_null_origin_login_passes_csrf_with_valid_token(self):
        response = self.client.post(reverse("login"), self.login_payload(), HTTP_ORIGIN="null")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Неверный логин или пароль")

    def test_login_with_correct_password_succeeds_in_embedded_flow(self):
        password = secrets.token_urlsafe(24)
        account = get_user_model().objects.create_user(username="synthetic_login", password=password)
        Profile.objects.create(user=account, role="patient")
        payload = self.login_payload()
        payload.update(username=account.username, password=password)
        self.assertEqual(self.client.post(reverse("login"), payload, HTTP_ORIGIN="null").status_code, 302)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_missing_and_wrong_tokens_still_rejected(self):
        self.assertEqual(self.client.post(reverse("login"), {"username": "synthetic_unknown"}, HTTP_ORIGIN="null").status_code, 403)
        payload = self.login_payload()
        payload["csrfmiddlewaretoken"] = "x" * 64
        self.assertEqual(self.client.post(reverse("login"), payload, HTTP_ORIGIN="null").status_code, 403)

    def test_compatibility_is_limited_to_explicit_loopback_demo(self):
        payload = self.login_payload()
        for change in [{"APP_ENV": "local"}, {"APP_ENV": "production-like"}, {"DEMO_EMBEDDED_FORMS_ENABLED": False}]:
            with override_settings(**change):
                self.assertEqual(self.client.post(reverse("login"), payload, HTTP_ORIGIN="null").status_code, 403)
        self.assertEqual(self.client.post(reverse("login"), payload, HTTP_ORIGIN="null", REMOTE_ADDR="192.0.2.2").status_code, 403)
        with override_settings(ALLOWED_HOSTS=["external.invalid"]):
            self.assertEqual(self.client.post(reverse("login"), payload, HTTP_ORIGIN="null", HTTP_HOST="external.invalid").status_code, 403)

    def test_other_foreign_origins_still_rejected(self):
        self.assertEqual(self.client.post(reverse("login"), self.login_payload(), HTTP_ORIGIN="https://untrusted.invalid").status_code, 403)

    def test_temporary_invitation_registers_patient_in_embedded_flow(self):
        token = self.prepare_invitation()
        issuer = get_user_model().objects.get(username="synthetic_invitation_doctor")
        self.assertFalse(issuer.has_usable_password())
        self.assertFalse(issuer.is_staff)
        page = self.client.get(reverse("invitation_exchange"))
        self.assertEqual(page.context["demo_token"], token)
        csrf = self.client.cookies["csrftoken"].value
        response = self.client.post(reverse("invitation_exchange"), {"token": token, "csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="null")
        self.assertEqual(response.status_code, 302)
        password = secrets.token_urlsafe(24)
        response = self.client.post(reverse("register"), {"username": "synthetic_registered", "password1": password, "password2": password, "role": "doctor", "csrfmiddlewaretoken": csrf}, HTTP_ORIGIN="null")
        self.assertEqual(response.status_code, 302)
        account = get_user_model().objects.get(username="synthetic_registered")
        self.assertEqual(account.profile.role, "patient")
        self.assertEqual(account.care_doctors.get().doctor_id, issuer.pk)
        self.assertIsNotNone(Invitation.objects.get().used_at)
        self.assertIsNone(self.client.get(reverse("invitation_exchange")).context["demo_token"])

    def test_invitation_is_not_displayed_remotely_or_after_expiry(self):
        token = self.prepare_invitation()
        page = self.client.get(reverse("invitation_exchange"), REMOTE_ADDR="192.0.2.1")
        self.assertNotContains(page, token)
        with override_settings(APP_ENV="production-like"):
            self.assertNotContains(self.client.get(reverse("invitation_exchange")), token)
        Invitation.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertNotContains(self.client.get(reverse("invitation_exchange")), token)

    def test_get_does_not_issue_a_code_and_existing_delivery_is_not_overwritten(self):
        self.client.get(reverse("invitation_exchange"))
        self.assertEqual(Invitation.objects.count(), 0)
        self.prepare_invitation()
        before = self.delivery.read_bytes()
        with self.assertRaises(CommandError):
            call_command("create_demo_invitation", stdout=io.StringIO())
        self.assertEqual(self.delivery.read_bytes(), before)
        self.assertEqual(Invitation.objects.count(), 1)

    def test_command_is_disabled_outside_explicit_demo(self):
        with override_settings(APP_ENV="production-like"), self.assertRaises(CommandError):
            call_command("create_demo_invitation", stdout=io.StringIO())
        self.assertFalse(self.delivery.exists())
        self.assertFalse(get_user_model().objects.exists())
