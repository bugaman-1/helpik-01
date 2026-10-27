"""Risk-oriented tests with synthetic users; no source or demo database access."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.models import (Profile, Questionnaire, QuestionnaireVersion, SurveyAssignment,
                         SurveySession, AnswerRevision, Outbox, InteractionEvent, CareRelationship, SurveyRecord)
from quiz.management.commands.create_demo_surveys import demo_definition
from quiz.services import questionnaires as s, paradata as p
from quiz.services.contracts import AnswerCommand, DomainError


def setup_case(case):
    User = get_user_model()
    case.patient = User.objects.create_user(username="synthetic_b03_test_patient")
    Profile.objects.create(user=case.patient, role="patient")
    case.other = User.objects.create_user(username="synthetic_b03_test_other")
    Profile.objects.create(user=case.other, role="patient")
    case.editor = User.objects.create_user(username="synthetic_b03_test_editor")
    Profile.objects.create(user=case.editor, role="moderator", moderator_enabled=True)
    case.doctor = User.objects.create_user(username="synthetic_b03_test_doctor")
    Profile.objects.create(user=case.doctor, role="doctor", verification_status="approved")
    CareRelationship.objects.create(patient=case.patient, doctor=case.doctor, reason="Synthetic test")
    case.questionnaire = Questionnaire.objects.create(code="synthetic-test", title="Synthetic test")
    case.version = s.create_version(case.editor, case.questionnaire, demo_definition(), status="demo")
    now = timezone.now()
    case.assignment = s.assign(case.doctor, case.patient, case.version, key=uuid.uuid4(), timezone_name="Europe/Moscow",
        available_from=now-timedelta(hours=1), available_until=now+timedelta(hours=1),
        observation_start=now-timedelta(hours=12), observation_end=now,
        window_code="morning", unscheduled_limit=1)
    case.run = s.start(case.patient, case.assignment.pk, uuid.uuid4())


def cmd(run, question="demo.safety", value="yes", expected=0, key=None, state="known"):
    return AnswerCommand(str(run.pk), question, expected, str(key or uuid.uuid4()), state, value)


@override_settings(DEMO_SURVEY_ENABLED=True)
class SurveyTests(TestCase):
    def setUp(self):
        setup_case(self)

    def assert_domain(self, code, function, *args, **kwargs):
        with self.assertRaises(DomainError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def complete(self):
        s.save_answer(self.patient, cmd(self.run, "demo.context", "no"))
        s.save_answer(self.patient, cmd(self.run, "demo.detail", None, state="not_applicable"))
        s.save_answer(self.patient, cmd(self.run))
        self.run.refresh_from_db()

    def test_loss_of_response_exact_retry_and_payload_conflict(self):
        command = cmd(self.run)
        first = s.save_answer(self.patient, command)
        self.assertEqual(s.save_answer(self.patient, command).pk, first.pk)
        self.assertEqual(self.run.revisions.count(), 1)
        self.assertEqual(Outbox.objects.count(), 1)
        self.assert_domain("idempotency_conflict", s.save_answer, self.patient,
            cmd(self.run, value="no", key=command.client_event_id))

    def test_stale_tab_never_replaces_revision(self):
        first = s.save_answer(self.patient, cmd(self.run))
        self.assert_domain("stale_version", s.save_answer, self.patient, cmd(self.run, value="no"))
        self.assertEqual(s.current_answers(self.run)["demo.safety"].pk, first.pk)

    def test_critical_correction_keeps_both_revisions_and_outbox(self):
        first = s.save_answer(self.patient, cmd(self.run))
        second = s.save_answer(self.patient, cmd(self.run, value="no", expected=1))
        self.assertEqual(self.run.revisions.count(), 2)
        self.assertEqual(Outbox.objects.filter(kind="safety.check_requested").count(), 2)
        snapshot = s.canonical_snapshot(self.doctor, self.run.pk)
        self.assertEqual(snapshot["safety_revision_ids"], [first.pk, second.pk])
        self.assertEqual(snapshot["status"], "in_progress")
        self.assertEqual(snapshot["answers"][2]["value"], "no")
        self.assertEqual(snapshot["answers"][0]["state"], "missing")
        self.assertIsNone(snapshot["answers"][0]["value"])

    def test_outbox_failure_rolls_back_answer_and_generation(self):
        with patch("quiz.services.questionnaires.Outbox.objects.create", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                s.save_answer(self.patient, cmd(self.run))
        self.run.refresh_from_db()
        self.assertEqual(self.run.answer_generation, 0)
        self.assertFalse(self.run.revisions.exists())

    def test_required_missing_invalid_option_and_foreign_question(self):
        self.assert_domain("incomplete", s.finish, self.patient, self.run.pk, uuid.uuid4(), 0)
        for command in [cmd(self.run, value="alien"), cmd(self.run, question="foreign"),
                        cmd(self.run, value=None, state="not_applicable"), cmd(self.run, expected=True)]:
            self.assert_domain("invalid_input", s.save_answer, self.patient, command)
        s.save_answer(self.patient, cmd(self.run, value=None, state="missing"))
        self.assert_domain("incomplete", s.finish, self.patient, self.run.pk, uuid.uuid4(), 1)

    def test_applicability_revalidated_after_parent_change(self):
        self.complete()
        s.save_answer(self.patient, cmd(self.run, "demo.context", "yes", expected=1))
        self.assert_domain("invalid_input", s.finish, self.patient, self.run.pk, uuid.uuid4(), 4)
        s.save_answer(self.patient, cmd(self.run, "demo.detail", "no", expected=1))
        self.assertEqual(s.finish(self.patient, self.run.pk, uuid.uuid4(), 5).status, "submitted")

    def test_submit_retries_and_stale_generation(self):
        self.complete()
        self.assert_domain("stale_version", s.finish, self.patient, self.run.pk, uuid.uuid4(), 2)
        key = uuid.uuid4()
        for token in [key, key, uuid.uuid4()]:
            self.assertEqual(s.finish(self.patient, self.run.pk, token, 3).status, "submitted")
        self.assertEqual(Outbox.objects.filter(kind="survey.submitted").count(), 1)
        self.assert_domain("session_closed", s.save_answer, self.patient, cmd(self.run, expected=1))
        self.assertEqual(SurveyRecord.objects.count(), 0)

    def test_abandon_preserves_partial_safety_and_blocks_new_answers(self):
        s.save_answer(self.patient, cmd(self.run))
        s.finish(self.patient, self.run.pk, uuid.uuid4(), 1, abandon=True)
        self.assertEqual(self.run.revisions.count(), 1)
        self.assertTrue(Outbox.objects.filter(kind="safety.check_requested").exists())
        self.assert_domain("session_closed", s.save_answer, self.patient, cmd(self.run, expected=1))

    def test_new_version_does_not_change_started_session(self):
        definition = demo_definition()
        definition["questions"].append(dict(copy.deepcopy(definition["questions"][-1]), code="new.code"))
        newer = s.create_version(self.editor, self.questionnaire, definition, status="demo")
        self.assertEqual(newer.number, 2)
        self.run.refresh_from_db()
        self.assertEqual(len(self.run.questionnaire_version.content.definition["questions"]), 4)
        self.assert_domain("invalid_input", s.save_answer, self.patient, cmd(self.run, question="new.code"))
        with self.assertRaises(ValueError):
            self.version.save()
        with self.assertRaises(ValueError):
            QuestionnaireVersion.objects.filter(pk=self.version.pk).update(status="published")
        self.assert_domain("clinical_publication_disabled", s.create_version, self.editor, self.questionnaire, definition, status="published")

    def test_two_windows_per_day_and_unscheduled_policy(self):
        now = timezone.now()
        second = s.assign(self.doctor, self.patient, self.version, key=uuid.uuid4(), timezone_name="Europe/Moscow",
            available_from=now+timedelta(hours=2), available_until=now+timedelta(hours=4),
            observation_start=self.assignment.observation_start, observation_end=self.assignment.observation_end,
            window_code="evening")
        self.assert_domain("window_closed", s.start, self.patient, second.pk, uuid.uuid4())
        with patch("quiz.services.questionnaires.timezone.now", return_value=now+timedelta(hours=3)):
            evening = s.start(self.patient, second.pk, uuid.uuid4())
        self.assertNotEqual(evening.pk, self.run.pk)
        self.assertEqual(s.start(self.patient, self.assignment.pk, uuid.uuid4()).pk, self.run.pk)
        extra = s.start(self.patient, self.assignment.pk, uuid.uuid4(), unscheduled=True)
        self.assertNotEqual(extra.pk, self.run.pk)
        self.assert_domain("unscheduled_limit", s.start, self.patient, self.assignment.pk, uuid.uuid4(), unscheduled=True)
        # The initial window is frozen, but a started session remains resumable.
        with patch("quiz.services.questionnaires.timezone.now", return_value=now+timedelta(days=1)):
            s.save_answer(self.patient, cmd(self.run))

    def test_object_access_read_write_and_revocation(self):
        for actor in [self.other, self.doctor, self.editor]:
            self.assert_domain("forbidden", s.save_answer, actor, cmd(self.run))
        self.assert_domain("forbidden", s.start, self.other, self.assignment.pk, uuid.uuid4())
        self.assert_domain("forbidden", s.canonical_snapshot, self.other, self.run.pk)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(reverse("survey_detail", args=[self.run.pk])).status_code, 200)
        CareRelationship.objects.filter(patient=self.patient).update(status="ended", ended_at=timezone.now(), is_primary=False)
        self.assertEqual(self.client.get(reverse("survey_detail", args=[self.run.pk])).status_code, 403)

    def test_http_csrf_safe_methods_and_untrusted_fields(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.patient)
        detail = reverse("survey_detail", args=[self.run.pk])
        answer = reverse("survey_answer", args=[self.run.pk])
        self.assertEqual(client.get(answer).status_code, 405)
        self.assertEqual(client.post(answer, {}).status_code, 403)
        response = client.get(detail)
        self.assertEqual(len(response.context['rows']), 4)
        self.assertEqual(self.run.revisions.count(), 0)
        token = client.cookies["csrftoken"].value
        payload = dict(question_code="demo.safety", expected_revision=0, client_event_id=str(uuid.uuid4()), state="known", value="yes")
        for extra in [{"score": 0}, {"patient_id": self.other.pk}, {"urgency": "low"}]:
            self.assertEqual(client.post(answer, json.dumps(payload | extra), content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 400)
        self.assertEqual(client.post(answer, json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)
        payload["client_event_id"] = str(uuid.uuid4())
        self.assertEqual(client.post(answer, json.dumps(payload), content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 409)
        self.assertEqual(client.get(detail).context["rows"][2]["answer"].value, "yes")

    def test_native_form_without_javascript(self):
        self.client.force_login(self.patient)
        response = self.client.post(reverse("survey_answer", args=[self.run.pk]), {
            "question_code": "demo.safety", "expected_revision": "0", "client_event_id": str(uuid.uuid4()), "choice": "option:yes"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.run.revisions.get().value, "yes")

    @override_settings(DEMO_SURVEY_ENABLED=False)
    def test_demo_cannot_execute_in_working_mode(self):
        self.assert_domain("clinical_publication_disabled", s.save_answer, self.patient, cmd(self.run))

    def event(self, seq=1, kind="question_visible", **changes):
        return {"event_id": str(uuid.uuid4()), "stream_id": str(uuid.UUID(int=1)), "sequence": seq,
            "kind": kind, "question_code": "demo.safety", "revision_id": None,
            "client_at": timezone.now().isoformat(), "elapsed_ms": seq * 1000, "save_latency_ms": None, **changes}

    def test_telemetry_consent_independent_of_medical_storage(self):
        self.assert_domain("telemetry_disabled", p.ingest, self.patient, self.run.pk, [self.event()])
        s.save_answer(self.patient, cmd(self.run))
        self.assertEqual(Outbox.objects.count(), 1)
        self.assertFalse(InteractionEvent.objects.exists())
        p.consent(self.patient, self.run.pk, True)
        event = self.event()
        p.ingest(self.patient, self.run.pk, [event])
        p.ingest(self.patient, self.run.pk, [event])
        self.assertEqual(InteractionEvent.objects.count(), 1)
        p.consent(self.patient, self.run.pk, False)
        self.assert_domain("telemetry_disabled", p.ingest, self.patient, self.run.pk, [self.event(2)])
        s.save_answer(self.patient, cmd(self.run, value="no", expected=1))

    def test_telemetry_limits_types_conflicts_and_foreign_revision(self):
        p.consent(self.patient, self.run.pk, True)
        self.assert_domain("invalid_input", p.ingest, self.patient, self.run.pk, [self.event(kind="keystroke")])
        self.assert_domain("invalid_input", p.ingest, self.patient, self.run.pk, [self.event(revision_id=99999)])
        self.assert_domain("invalid_input", p.ingest, self.patient, self.run.pk, [self.event()] * 33)
        self.assert_domain("invalid_input", p.ingest, self.patient, self.run.pk, [self.event(value="medical-answer")])
        for start in range(1, 121, 30):
            p.ingest(self.patient, self.run.pk, [self.event(i) for i in range(start, start+30)])
        self.assert_domain("throttled", p.ingest, self.patient, self.run.pk, [self.event(121)])
        self.assert_domain("idempotency_conflict", p.ingest, self.patient, self.run.pk, [self.event(1)])
        s.save_answer(self.patient, cmd(self.run))
        self.assertEqual(self.run.revisions.count(), 1)

    def test_background_return_slow_network_and_missing_event_quality(self):
        p.consent(self.patient, self.run.pk, True)
        events = [self.event(1), self.event(2, "option_selected", elapsed_ms=1800),
            self.event(3, "visibility_hidden", elapsed_ms=2000), self.event(4, "visibility_visible", elapsed_ms=7000),
            self.event(5, "question_left", elapsed_ms=9000, save_latency_ms=4500)]
        p.ingest(self.patient, self.run.pk, events)
        result = p.summarize(self.run.interactions.all(), enabled=True)
        q = result["questions"][0]
        self.assertEqual((q["first_choice_ms"], q["visible_ms"], q["background_ms"], q["save_latency_ms"]), (800, 3000, 5000, 4500))
        p.ingest(self.patient, self.run.pk, [self.event(7, "page_resume", elapsed_ms=9500)])
        self.assertIn("sequence_gap", p.summarize(self.run.interactions.all(), enabled=True)["quality_flags"])
        unknown = p.summarize([], enabled=False)
        self.assertIn("no_events", unknown["quality_flags"])
        self.assertEqual(unknown["questions"], [])

    def test_clock_reset_and_multiple_tabs_not_summed(self):
        p.consent(self.patient, self.run.pk, True)
        p.ingest(self.patient, self.run.pk, [self.event(), self.event(2, "question_left", elapsed_ms=1), self.event(stream_id=str(uuid.uuid4()))])
        flags = p.summarize(self.run.interactions.all(), enabled=True)["quality_flags"]
        self.assertIn("nonmonotonic_clock", flags)
        self.assertIn("multiple_streams_no_cross_stream_sum", flags)

    def test_assignment_requires_current_doctor_and_exact_repeat(self):
        fields = dict(key=uuid.uuid4(), timezone_name="Europe/Moscow", available_from=self.assignment.available_from,
            available_until=self.assignment.available_until, observation_start=self.assignment.observation_start,
            observation_end=self.assignment.observation_end, window_code="extra")
        self.assert_domain("forbidden", s.assign, self.patient, self.patient, self.version, **fields)
        first = s.assign(self.doctor, self.patient, self.version, **fields)
        self.assertEqual(s.assign(self.doctor, self.patient, self.version, **fields).pk, first.pk)
        self.assert_domain("idempotency_conflict", s.assign, self.doctor, self.patient, self.version,
            **(fields | {"window_code": "changed"}))

    def test_optional_events_have_atomic_batch_total_and_stream_limits(self):
        p.consent(self.patient, self.run.pk, True)
        self.assert_domain("invalid_input", p.ingest, self.patient, self.run.pk,
            [self.event(), self.event(2, kind="bad")])
        self.assertEqual(self.run.interactions.count(), 0)
        for i in range(8):
            p.ingest(self.patient, self.run.pk, [self.event(stream_id=str(uuid.UUID(int=i+1)))])
        self.assert_domain("throttled", p.ingest, self.patient, self.run.pk, [self.event(stream_id=str(uuid.uuid4()))])
        with patch("quiz.services.paradata.MAX_EVENTS", 8):
            self.assert_domain("throttled", p.ingest, self.patient, self.run.pk, [self.event(2)])
        s.save_answer(self.patient, cmd(self.run))

    def test_telemetry_and_finish_http_access_and_safe_methods(self):
        self.client.force_login(self.other)
        for name, payload in [("survey_events", {"events": [self.event()]}),
                              ("survey_finish", {"key": str(uuid.uuid4()), "expected_generation": 0, "abandon": True})]:
            url = reverse(name, args=[self.run.pk])
            self.assertEqual(self.client.get(url).status_code, 405)
            self.assertEqual(self.client.post(url, json.dumps(payload), content_type="application/json").status_code, 403)
        self.assertEqual(self.client.post(reverse("survey_consent", args=[self.run.pk]), {"enabled": "true"}).status_code, 403)


@override_settings(DEMO_SURVEY_ENABLED=True)
class SurveyConcurrencyTests(TransactionTestCase):
    def setUp(self):
        setup_case(self)

    def race(self, function):
        barrier = threading.Barrier(2)
        def work(i):
            close_old_connections()
            patient = get_user_model().objects.get(pk=self.patient.pk)
            barrier.wait(timeout=10)
            try:
                return function(patient, i)
            except DomainError as exc:
                return exc.code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(work, range(2)))

    def test_simultaneous_answer_has_one_winner(self):
        outcomes = self.race(lambda patient, i: s.save_answer(patient, cmd(self.run, value=["yes", "no"][i])).revision)
        self.assertCountEqual(outcomes, [1, "stale_version"])
        self.assertEqual(self.run.revisions.count(), 1)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_simultaneous_retry_one_revision_one_event(self):
        command = cmd(self.run)
        outcomes = self.race(lambda patient, i: s.save_answer(patient, command).pk)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(self.run.revisions.count(), 1)
        self.assertEqual(Outbox.objects.count(), 1)

    def test_simultaneous_submit_one_terminal_event(self):
        s.save_answer(self.patient, cmd(self.run, "demo.context", "no"))
        s.save_answer(self.patient, cmd(self.run, "demo.detail", None, state="not_applicable"))
        s.save_answer(self.patient, cmd(self.run))
        outcomes = self.race(lambda patient, i: s.finish(patient, self.run.pk, uuid.uuid4(), 3).status)
        self.assertEqual(outcomes, ["submitted", "submitted"])
        self.assertEqual(Outbox.objects.filter(kind="survey.submitted").count(), 1)

    def test_simultaneous_planned_start_reuses_one_session(self):
        SurveySession.objects.filter(pk=self.run.pk).update(slot="old")
        outcomes = self.race(lambda patient, i: s.start(patient, self.assignment.pk, uuid.uuid4()).pk)
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(SurveySession.objects.filter(assignment=self.assignment, slot="planned").count(), 1)
