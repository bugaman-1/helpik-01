"""Only synthetic users and inputs. No dependency on source db.sqlite3."""
import io
import secrets
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.models import (Profile, CareRelationship, SurveyRecord, Invitation, AuditEvent,
                         Alert, Outbox, ContentVersion, SurveySession, AnswerRevision, Assessment)
from quiz.services import invitations, care
from quiz.services.access import can_access_patient
from quiz.services.contracts import DomainError, ValueEnvelope, UnavailableTherapyAdapter
from quiz.services.surveys import start_demo, save_demo_answer

User = get_user_model()


def user(name, role, approved=False):
    account = User.objects.create_user(username="synthetic_" + name)
    Profile.objects.create(user=account, role=role, verification_status="approved" if approved else "unverified", moderator_enabled=role == "moderator")
    return account


class AccessTests(TestCase):
    def setUp(self):
        self.mod = user("moderator", "moderator")
        self.doctor = user("doctor", "doctor", True)
        self.other = user("other", "doctor", True)
        self.pending = user("pending", "doctor")
        self.patient = user("a", "patient")
        self.patient_b = user("b", "patient")
        care.attach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic assignment")
        SurveyRecord.objects.create(user=self.patient, user_name="SYNTHETIC-A", positive_count=1)
        SurveyRecord.objects.create(user=self.patient_b, user_name="SYNTHETIC-B", negative_count=1)

    def test_patient_card_role_matrix_and_no_admin_bypass(self):
        url = reverse("patient_records", args=[self.patient.pk])
        for actor, expected in [(self.doctor, 200), (self.other, 403), (self.pending, 403), (self.mod, 403), (self.patient, 403), (self.patient_b, 403)]:
            self.client.force_login(actor)
            self.assertEqual(self.client.get(url).status_code, expected)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.mod.is_staff = True
        self.mod.save()
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_own_history_and_graphs_cannot_select_another_patient(self):
        self.client.force_login(self.patient)
        response = self.client.get(reverse("activity"), {"patient_id": self.patient_b.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["records"].values_list("user_id", flat=True)), [self.patient.pk])
        session = self.client.session
        session["last_record_id"] = self.patient_b.survey_records.first().pk
        session.save()
        response = self.client.get(reverse("stats"))
        self.assertEqual(response.context["total"], 0)
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get(reverse("activity")).status_code, 403)

    def test_stale_tab_loses_access_after_detach_or_revocation(self):
        self.client.force_login(self.doctor)
        url = reverse("patient_records", args=[self.patient.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        care.detach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic end", end_care=True)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertFalse(can_access_patient(self.doctor, self.patient.pk))
        care.attach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic return")
        Profile.objects.filter(user=self.doctor).update(verification_status="revoked")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_legacy_m2m_does_not_grant_access(self):
        self.patient.profile.doctors.add(self.other)
        self.assertFalse(can_access_patient(self.other, self.patient.pk))

    def test_disabled_account_denied_even_with_cached_user(self):
        User.objects.filter(pk=self.doctor.pk).update(is_active=False)
        self.assertFalse(can_access_patient(self.doctor, self.patient.pk))

    def test_get_mutations_forbidden_and_csrf_enforced(self):
        self.client.force_login(self.mod)
        before = CareRelationship.objects.count()
        paths = [("attach_doctor", [self.patient.pk]), ("detach_doctor", [self.patient.pk, self.doctor.pk]), ("verify_doctor", [self.doctor.pk]), ("transfer_responsibility", [self.patient.pk])]
        for name, args in paths:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 405)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.mod)
        self.assertEqual(csrf.post(reverse("detach_doctor", args=[self.patient.pk, self.doctor.pk]), {"reason": "Synthetic"}).status_code, 403)
        self.assertEqual(CareRelationship.objects.count(), before)
        for name in ("invitation_exchange", "register", "invitations", "start_survey"):
            self.assertEqual(csrf.post(reverse(name), {}).status_code, 403)

    def test_unauthorized_attachment_post(self):
        for actor in (self.patient, self.doctor, self.other, self.pending):
            self.client.force_login(actor)
            response = self.client.post(reverse("attach_doctor", args=[self.patient.pk]), {"doctor_id": self.other.pk, "reason": "Synthetic"})
            self.assertEqual(response.status_code, 403)

    def test_moderator_receives_only_administrative_data(self):
        self.client.force_login(self.mod)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "SYNTHETIC-A")
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 200)
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 403)

    def test_primary_and_open_alert_require_transfer(self):
        care.attach(self.mod, self.patient.pk, self.other.pk, "Synthetic second")
        alert = Alert.objects.create(patient=self.patient, responsible=self.doctor, evidence_snapshot={"synthetic": True}, episode_key="synthetic-episode")
        with self.assertRaises(DomainError):
            care.detach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic", end_care=True)
        with self.assertRaises(DomainError):
            care.verify_doctor(self.mod, self.doctor.pk, "revoked", "Synthetic")
        care.detach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic transfer", self.other.pk)
        alert.refresh_from_db()
        self.assertEqual(alert.responsible_id, self.other.pk)
        self.assertEqual(alert.status, "open")
        self.assertEqual(Outbox.objects.filter(alert=alert).count(), 1)
        self.assertTrue(CareRelationship.objects.get(patient=self.patient, doctor=self.other).is_primary)
        count = AuditEvent.objects.count()
        care.detach(self.mod, self.patient.pk, self.doctor.pk, "Repeat")
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_last_primary_needs_explicit_end_and_reason(self):
        with self.assertRaises(DomainError):
            care.detach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic")
        with self.assertRaises(DomainError):
            care.detach(self.mod, self.patient.pk, self.doctor.pk, "", end_care=True)
        care.detach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic end", end_care=True)
        self.assertEqual(CareRelationship.objects.get(patient=self.patient).status, "ended")

    def test_database_rejects_duplicate_active_pair_and_primary(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CareRelationship.objects.create(patient=self.patient, doctor=self.doctor, reason="Synthetic duplicate")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CareRelationship.objects.create(patient=self.patient, doctor=self.other, is_primary=True, reason="Synthetic duplicate lead")


class InvitationTests(TestCase):
    def setUp(self):
        self.mod = user("moderator", "moderator")
        self.doctor = user("doctor", "doctor", True)
        self.pending = user("pending", "doctor")
        self.patient = user("patient", "patient")

    def test_only_approved_doctor_issues_patient_invitation(self):
        for actor, role in [(self.doctor, "doctor"), (self.patient, "patient"), (self.pending, "patient"), (self.mod, "patient")]:
            with self.assertRaises(DomainError):
                invitations.issue(actor, role=role, qualification_reference="Synthetic")
        with self.assertRaises(DomainError):
            invitations.issue(self.mod, role="doctor")
        invite, token = invitations.issue(self.doctor, role="patient")
        self.assertNotEqual(invite.token_digest, token)
        self.assertEqual(len(token), 43)
        self.assertEqual(len(invite.token_digest), 64)

    def test_server_role_ignores_post_tampering(self):
        invite, token = invitations.issue(self.doctor, role="patient")
        response = self.client.post(reverse("invitation_exchange"), {"token": token})
        self.assertRedirects(response, reverse("register"))
        self.assertNotIn(token, str(dict(self.client.session)))
        password = secrets.token_urlsafe(24)
        response = self.client.post(reverse("register"), {"username": "synthetic_new", "password1": password, "password2": password, "role": "doctor", "doctor_id": self.pending.pk})
        self.assertEqual(response.status_code, 302)
        account = User.objects.get(username="synthetic_new")
        self.assertEqual(account.profile.role, "patient")
        self.assertEqual(account.care_doctors.get().doctor_id, self.doctor.pk)
        invite.refresh_from_db()
        self.assertEqual(invite.used_by_id, account.pk)
        self.assertIsNotNone(invite.used_at)
        self.assertNotIn("invitation_context", self.client.session)

    def test_successful_registration_with_real_csrf_checks(self):
        _, token = invitations.issue(self.doctor, role="patient")
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("invitation_exchange"))
        csrf = client.cookies["csrftoken"].value
        response = client.post(reverse("invitation_exchange"), {"token": token, "csrfmiddlewaretoken": csrf})
        self.assertEqual(response.status_code, 302)
        password = secrets.token_urlsafe(24)
        response = client.post(reverse("register"), {"username": "synthetic_csrf", "password1": password, "password2": password, "csrfmiddlewaretoken": csrf})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username="synthetic_csrf").exists())

    def test_doctor_invitation_is_disabled(self):
        with self.assertRaises(DomainError):
            invitations.issue(self.mod, role="doctor", qualification_reference="Synthetic administrative verification")

    def test_revocation_between_exchange_and_submit_is_rechecked(self):
        invite, token = invitations.issue(self.doctor, role="patient")
        digest = invitations.exchange(token)
        invitations.revoke(self.doctor, invite.pk, "Synthetic revoke after exchange")
        candidate = User(username="synthetic_too_late")
        candidate.set_unusable_password()
        with self.assertRaises(DomainError):
            invitations.register_from_context(digest, candidate)
        self.assertFalse(User.objects.filter(username="synthetic_too_late").exists())

    def test_no_registration_without_invitation(self):
        before = User.objects.count()
        self.client.post(reverse("register"), {"username": "synthetic_noinvite", "role": "doctor"})
        self.assertEqual(User.objects.count(), before)

    def test_expired_revoked_used_and_issuer_revoked(self):
        for mode in ["expired", "revoked", "used", "issuer"]:
            Profile.objects.filter(user=self.doctor).update(verification_status="approved")
            invite, token = invitations.issue(self.doctor, role="patient")
            if mode == "expired":
                Invitation.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
            elif mode == "revoked":
                invitations.revoke(self.doctor, invite.pk, "Synthetic revoke")
            elif mode == "used":
                invitations.accept_for_patient(self.patient, invite.token_digest)
            else:
                Profile.objects.filter(user=self.doctor).update(verification_status="revoked")
            with self.assertRaises(DomainError):
                invitations.exchange(token)

    def test_atomic_rollback_preserves_invitation_on_failure(self):
        invite, _ = invitations.issue(self.doctor, role="patient")
        candidate = User(username="synthetic_rollback")
        candidate.set_unusable_password()
        with patch("quiz.services.invitations._link", side_effect=RuntimeError("Synthetic failure")):
            with self.assertRaises(RuntimeError):
                invitations.register_from_context(invite.token_digest, candidate)
        invite.refresh_from_db()
        self.assertIsNone(invite.used_at)
        self.assertFalse(User.objects.filter(username="synthetic_rollback").exists())

    def test_existing_patient_adds_second_doctor_without_duplicate_user(self):
        other = user("second", "doctor", True)
        care.attach(self.mod, self.patient.pk, self.doctor.pk, "Synthetic")
        invite, token = invitations.issue(other, role="patient")
        before = User.objects.count()
        self.client.force_login(self.patient)
        self.client.post(reverse("invitation_exchange"), {"token": token})
        self.assertEqual(self.client.get(reverse("accept_invitation")).status_code, 200)
        invite.refresh_from_db()
        self.assertIsNone(invite.used_at)
        self.assertEqual(self.client.post(reverse("accept_invitation"), {"confirm": "yes"}).status_code, 302)
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(self.patient.care_doctors.filter(status="active").count(), 2)

    def test_expired_server_context_cannot_register(self):
        invite, _ = invitations.issue(self.doctor, role="patient")
        session = self.client.session
        session["invitation_context"] = {"digest": invite.token_digest, "until": 0}
        session.save()
        self.assertRedirects(self.client.get(reverse("register")), reverse("invitation_exchange"))

    def test_generic_registration_error_does_not_disclose_username(self):
        invite, token = invitations.issue(self.doctor, role="patient")
        self.client.post(reverse("invitation_exchange"), {"token": token})
        errors = []
        for username in [self.patient.username, "synthetic_unused"]:
            response = self.client.post(reverse("register"), {"username": username, "password1": "x", "password2": "y"})
            errors.append(response.context["error"])
            self.assertNotContains(response, "уже существует")
        self.assertEqual(errors[0], errors[1])

    def test_rate_limit_and_private_referrer_no_cache(self):
        for _ in range(10):
            response = self.client.post(reverse("invitation_exchange"), {"token": "invalid"}, HTTP_X_FORWARDED_FOR=secrets.token_hex(4))
            self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse("invitation_exchange"), {"token": "invalid"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("no-store", response["Cache-Control"])

    def test_revoke_cannot_be_called_by_other_doctor_or_get(self):
        invite, _ = invitations.issue(self.doctor, role="patient")
        self.client.force_login(self.doctor)
        url = reverse("revoke_invitation", args=[invite.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        other = user("other", "doctor", True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"reason": "Synthetic"}).status_code, 403)


@override_settings(DEMO_SURVEY_ENABLED=True)
class PreservationTests(TestCase):
    def setUp(self):
        self.patient = user("demo", "patient")

    def test_demo_repeat_does_not_duplicate_completion_or_answers(self):
        run = start_demo(self.patient, uuid.uuid4())
        questions = run.content.definition["questions"]
        for question in questions:
            save_demo_answer(self.patient, run.pk, question["id"], "neutral")
            save_demo_answer(self.patient, run.pk, question["id"], "neutral")
        self.assertEqual(SurveyRecord.objects.filter(user=self.patient).count(), 1)
        self.assertEqual(run.revisions.count(), len(questions))
        self.assertEqual(self.patient.survey_records.get().neutral_count, len(questions))

    def test_demo_rejects_other_patient_or_skipping_and_disabled_mode(self):
        run = start_demo(self.patient, uuid.uuid4())
        other = user("other", "patient")
        with self.assertRaises(DomainError):
            save_demo_answer(other, run.pk, run.content.definition["questions"][0]["id"], "positive")
        with self.assertRaises(DomainError):
            save_demo_answer(self.patient, run.pk, run.content.definition["questions"][-1]["id"], "positive")
        with override_settings(DEMO_SURVEY_ENABLED=False), self.assertRaises(DomainError):
            start_demo(self.patient, uuid.uuid4())

    def test_malformed_demo_identifier_is_rejected_without_server_error(self):
        self.client.force_login(self.patient)
        self.assertEqual(self.client.post(reverse("question", args=[1]), {"run_id": "not-a-uuid", "value": "neutral"}).status_code, 400)

    def test_immutable_revision_and_assessment_and_zero_is_known(self):
        run = start_demo(self.patient, uuid.uuid4())
        first = AnswerRevision.objects.create(session=run, question_code="synthetic-safety", revision=1, state="known", value=1, client_event_id=uuid.uuid4())
        AnswerRevision.objects.create(session=run, question_code="synthetic-safety", revision=2, state="known", value=0, client_event_id=uuid.uuid4())
        with self.assertRaises(ValueError):
            AnswerRevision.objects.filter(pk=first.pk).update(value=0)
        with self.assertRaises(ValueError):
            first.delete()
        assessment = Assessment.objects.create(patient=self.patient, session=run, algorithm_version="synthetic-1", input_snapshot={"answer_ids": [first.pk]}, result={"safety_flags": ["synthetic-positive"]}, status="not_evaluated", idempotency_key=uuid.uuid4())
        with self.assertRaises(ValueError):
            assessment.save()
        self.assertEqual(AnswerRevision.objects.get(pk=first.pk).value, 1)
        self.assertEqual(ValueEnvelope("known", 0).value, 0)
        with self.assertRaises(ValueError):
            ValueEnvelope("missing", 0)
        with self.assertRaises(DomainError):
            UnavailableTherapyAdapter().confirm()

    def test_bootstrap_never_promotes_or_prints_password(self):
        with self.assertRaises(CommandError):
            call_command("create_moderator", self.patient.username, reason="Synthetic")
        self.patient.refresh_from_db()
        self.assertEqual(self.patient.profile.role, "patient")
        password = secrets.token_urlsafe(24)
        output = io.StringIO()
        with patch("quiz.management.commands.bootstrap_moderator.getpass", return_value=password):
            call_command("bootstrap_moderator", "synthetic_new_moderator", reason="Synthetic", stdout=output)
        self.assertNotIn(password, output.getvalue())
        self.assertFalse(User.objects.get(username="synthetic_new_moderator").is_staff)

    def test_disabled_legacy_moderator_needs_explicit_reset(self):
        moderator = user("legacy_mod", "moderator")
        Profile.objects.filter(user=moderator).update(moderator_enabled=False)
        self.client.force_login(moderator)
        self.assertEqual(self.client.get(reverse("home")).status_code, 403)
        with self.assertRaises(CommandError):
            call_command("bootstrap_moderator", moderator.username, reason="Synthetic")
        with patch("quiz.management.commands.bootstrap_moderator.getpass", return_value=secrets.token_urlsafe(24)):
            call_command("bootstrap_moderator", moderator.username, activate_existing=True, reason="Synthetic explicit reset", stdout=io.StringIO())
        self.assertTrue(Profile.objects.get(user=moderator).moderator_enabled)


class ConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_two_connections_can_use_invitation_only_once(self):
        doctor = user("race_doctor", "doctor", True)
        invite, _ = invitations.issue(doctor, role="patient")
        barrier = threading.Barrier(2)

        def attempt(index):
            close_old_connections()
            candidate = User(username=f"synthetic_racer_{index}")
            candidate.set_unusable_password()
            barrier.wait(timeout=10)
            try:
                invitations.register_from_context(invite.token_digest, candidate)
                return "created"
            except DomainError as exc:
                return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [1, 2]))
        self.assertCountEqual(results, ["created", "invitation_unavailable"])
        self.assertEqual(User.objects.filter(username__startswith="synthetic_racer_").count(), 1)
        self.assertEqual(CareRelationship.objects.count(), 1)
        self.assertEqual(Profile.objects.filter(role="patient").count(), 1)

    def test_existing_patient_accept_race_does_not_duplicate_relationship(self):
        doctor = user("race_doctor", "doctor", True)
        patient = user("race_patient", "patient")
        invite, _ = invitations.issue(doctor, role="patient")
        barrier = threading.Barrier(2)

        def attempt(_):
            close_old_connections()
            barrier.wait(timeout=10)
            try:
                invitations.accept_for_patient(patient, invite.token_digest)
                return "accepted"
            except DomainError as exc:
                return exc.code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, [1, 2]))
        self.assertCountEqual(results, ["accepted", "invitation_unavailable"])
        self.assertEqual(CareRelationship.objects.count(), 1)
