"""Synthetic integration scenarios: persistent care, isolation and safe retries."""
import uuid
from datetime import datetime, time, timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.models import (CareRelationship, SurveySession, QuestionnaireVersion, PrescriptionVersion,
                         MedicationIntake, CareMessage, CareNote, AdverseEvent, ClinicalAction, Profile)
from quiz.test_b02 import user
from quiz.test_b03 import setup_case, cmd
from quiz.services import questionnaires, invitations


@override_settings(DEMO_SURVEY_ENABLED=True)
class MVP2Tests(TestCase):
    def setUp(self):
        setup_case(self)
        self.foreign_doctor = user("mvp2_foreign", "doctor", True)
        self.second = user("mvp2_second", "doctor", True)
        CareRelationship.objects.create(patient=self.patient, doctor=self.second, reason="Synthetic second")
        self.day = timezone.localdate()
        self.morning = timezone.make_aware(datetime.combine(self.day, time(7)))
        self.noon = timezone.make_aware(datetime.combine(self.day, time(12)))
        self.card = reverse("patient_records", args=[self.patient.pk])

    def post(self, name, actor, data, args=None, status=302):
        self.client.force_login(actor)
        response = self.client.post(reverse(name, args=args), data)
        self.assertEqual(response.status_code, status, response.content[:1500])
        return response

    def prescription(self, **changes):
        return {"key": str(uuid.uuid4()), "expected_version": 0, "name": "SYNTHETIC MEDICINE",
            "dose": "1.25", "unit": "synthetic-unit", "times": "08:00, 20:00", "start": self.day.isoformat(),
            "end": (self.day+timedelta(days=2)).isoformat(), "note": "Synthetic instruction", "reason": "Synthetic decision",
            "confirm": "on", **changes}

    def create_prescription(self):
        payload = self.prescription()
        with patch("django.utils.timezone.now", return_value=self.morning):
            self.post("care_prescribe", self.doctor, payload, [self.patient.pk])
        return payload, PrescriptionVersion.objects.get(patient=self.patient)

    def test_registration_to_survey_to_care_persists_on_reload(self):
        from quiz.services.diary import prepare, definition
        version = prepare()
        invite, token = invitations.issue(self.doctor, role="patient")
        self.assertEqual(self.client.post('/invite/', {"token": token, "selected_role": "patient"}).status_code, 302)
        password = "Synthetic!Mvp2-NotARealSecret-827"
        self.assertEqual(self.client.post('/register/', {"username": "synthetic_mvp2_registered", "password1": password, "password2": password}).status_code, 302)
        patient = get_user_model().objects.get(username="synthetic_mvp2_registered")
        self.assertTrue(CareRelationship.objects.filter(patient=patient, doctor=self.doctor, status="active").exists())
        self.post("care_assign", self.doctor, {"key": uuid.uuid4(), "version": version.pk, "day": self.day, "extra": 3}, [patient.pk])
        self.client.force_login(patient)
        assignment = patient.survey_assignments.get()
        key = str(uuid.uuid4())
        url = reverse("survey_assignment_start", args=[assignment.pk])
        self.assertEqual(self.client.post(url, {"key": key}).status_code, 302)
        run = SurveySession.objects.get(patient=patient)
        self.assertEqual(self.client.post(url, {"key": key}).status_code, 302)
        for q in definition()['questions']:
            code = q['code']
            choice = 'not_applicable' if q['allow_not_applicable'] else 'option:' + q['options'][0]['code']
            self.assertEqual(self.client.post(reverse("survey_answer", args=[run.pk]), {"question_code": code, "expected_revision": 0,
                "client_event_id": uuid.uuid4(), "choice": choice}).status_code, 302)
        run.refresh_from_db()
        self.assertEqual(self.client.post(reverse("survey_finish", args=[run.pk]), {"key": uuid.uuid4(), "expected_generation": run.answer_generation}).status_code, 302)
        self.assertEqual(self.client.post(url, {"key": uuid.uuid4(), "mode": "unscheduled"}).status_code, 302)
        self.assertEqual(SurveySession.objects.filter(patient=patient).count(), 2)
        self.assertEqual(run.revisions.count(), 17)
        response = self.client.get('/activity/', {"period": "day", "date": self.day})
        self.assertEqual(len(response.context['entries']), 2)
        self.assertContains(response, "Самочувствие по датам")
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get('/'), patient.username)
        self.assertEqual(self.client.get(reverse('patient_records', args=[patient.pk])).status_code, 200)
        with patch("django.utils.timezone.now", return_value=self.morning):
            self.post('care_prescribe', self.doctor, self.prescription(), [patient.pk])
        with patch("django.utils.timezone.now", return_value=self.noon):
            self.client.force_login(patient)
            response = self.client.get('/medicines/')
            row = response.context['schedule_rows'][0]
            self.post('care_intake', patient, {"prescription": row['prescription'], "medication_id": row['medication_id'], "due_at": row['due_at'].isoformat(), "status": "taken"})
        payload = {"key": uuid.uuid4(), "kind": "message", "text": "SYNTHETIC persisted message"}
        self.post('care_message', patient, payload, [patient.pk])
        self.post('care_message', patient, payload, [patient.pk])
        self.assertEqual(CareMessage.objects.filter(patient=patient).count(), 1)
        # A new HTTP client reloads exclusively from persistent records.
        fresh = Client()
        fresh.force_login(patient)
        self.assertContains(fresh.get('/medicines/'), 'Принято')
        self.assertContains(fresh.get('/contact/'), payload['text'])
        fresh.force_login(self.doctor)
        self.assertContains(fresh.get(reverse('patient_records', args=[patient.pk])), payload['text'])

    def test_shared_dynamics_raw_answers_counts_and_session(self):
        questionnaires.save_answer(self.patient, cmd(self.run, 'demo.context', 'yes'))
        questionnaires.save_answer(self.patient, cmd(self.run, 'demo.context', 'no', expected=1))
        extra = questionnaires.start(self.patient, self.assignment.pk, uuid.uuid4(), unscheduled=True)
        questionnaires.save_answer(self.patient, cmd(extra, 'demo.context', 'yes'))
        for period in ('month', 'week', 'day', 'session'):
            query = {"period": period, "date": self.day, "parameter": f"{self.version.pk}:demo.context", "entry": self.run.pk}
            self.client.force_login(self.patient)
            patient_view = self.client.get('/activity/', query)
            self.client.force_login(self.doctor)
            doctor_view = self.client.get(self.card, query)
            self.assertEqual([{k: v for k, v in e.items() if k != 'url'} for e in patient_view.context['entries']],
                             [{k: v for k, v in e.items() if k != 'url'} for e in doctor_view.context['entries']])
            self.assertEqual(patient_view.context['observation_count'], 1 if period == 'session' else 2)
            self.assertEqual(patient_view.context['observation_days'], 1)
            self.assertContains(patient_view, 'Нет (синтетический пример)')
        self.client.force_login(self.patient)
        missing = self.client.get('/activity/', {"parameter": f"{self.version.pk}:demo.note"})
        self.assertEqual(missing.context['missing_count'], 2)
        self.assertEqual(missing.context['observation_count'], 0)
        self.assertEqual(self.client.get('/activity/', {"date": '2020-01-01'}).context['entries'], [])

    def test_prescription_revision_keeps_past_marks_and_rejects_stale_plan(self):
        original, plan = self.create_prescription()
        med = plan.definition['medicines'][0]
        due = self.morning+timedelta(hours=1)
        mark = {"prescription": plan.pk, "medication_id": med['id'], "due_at": due.isoformat(), "status": "taken"}
        with patch('django.utils.timezone.now', return_value=self.noon):
            self.post('care_intake', self.patient, mark)
            self.post('care_intake', self.patient, mark)
            self.assertEqual(MedicationIntake.objects.count(), 1)
            update = self.prescription(expected_version=1, medication_id=med['id'], dose='2')
            self.post('care_prescribe', self.second, update, [self.patient.pk])
            self.post('care_prescribe', self.second, update, [self.patient.pk])
            self.post('care_prescribe', self.doctor, self.prescription(), [self.patient.pk], 409)
            self.client.force_login(self.patient)
            rows = self.client.get('/medicines/').context['schedule_rows']
            self.assertEqual([r['medicine']['dose'] for r in rows], ['1.25', '2'])
            self.assertEqual(rows[0]['status'], 'taken')
            self.assertEqual(rows[1]['status'], '')
            self.post('care_intake', self.patient, {**mark, 'status': 'skipped'})
            self.assertEqual(MedicationIntake.objects.get().status, 'skipped')
        self.assertEqual(PrescriptionVersion.objects.count(), 2)
        plan.refresh_from_db()
        self.assertEqual(plan.definition['medicines'][0]['dose'], '1.25')
        self.post('care_prescribe', self.doctor, {**original, 'dose': '7'}, [self.patient.pk], 409)

    def test_access_matrix_and_revoked_relationship(self):
        _, plan = self.create_prescription()
        med = plan.definition['medicines'][0]
        self.post('care_message', self.patient, {'key': uuid.uuid4(), 'kind': 'message', 'text': 'PRIVATE SYNTHETIC'}, [self.patient.pk])
        for actor in (self.other, self.foreign_doctor, self.editor):
            self.client.force_login(actor)
            self.assertEqual(self.client.get(self.card).status_code, 403)
            self.post('care_message', actor, {'key': uuid.uuid4(), 'kind': 'message', 'text': 'attack'}, [self.patient.pk], 403)
            self.post('care_prescribe', actor, self.prescription(), [self.patient.pk], 403)
            self.post('care_note', actor, {'key': uuid.uuid4(), 'text': 'attack'}, [self.patient.pk], 403)
            self.post('care_intake', actor, {'prescription': plan.pk, 'medication_id': med['id'], 'due_at': self.noon.isoformat(), 'status': 'taken'}, status=403)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/contact/'), 'PRIVATE SYNTHETIC')
        self.assertNotContains(self.client.get('/medicines/'), 'SYNTHETIC MEDICINE')
        self.client.force_login(self.second)
        self.assertContains(self.client.get(self.card), 'PRIVATE SYNTHETIC')
        CareRelationship.objects.filter(patient=self.patient, doctor=self.second).update(status='ended', ended_at=timezone.now())
        self.post('care_message', self.second, {'key': uuid.uuid4(), 'kind': 'message', 'text': 'stale'}, [self.patient.pk], 403)
        Profile.objects.filter(user=self.doctor).update(verification_status='revoked')
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(self.card).status_code, 403)

    def test_stop_preserves_history_and_rejects_unscheduled_or_future_intake(self):
        _, plan = self.create_prescription()
        med = plan.definition['medicines'][0]
        due = self.morning+timedelta(hours=1)
        mark = {'prescription': plan.pk, 'medication_id': med['id'], 'due_at': due.isoformat(), 'status': 'taken'}
        with patch('django.utils.timezone.now', return_value=self.noon):
            self.post('care_intake', self.patient, {**mark, 'due_at': (due+timedelta(minutes=1)).isoformat()}, status=400)
            self.post('care_intake', self.patient, {**mark, 'due_at': (due+timedelta(hours=12)).isoformat()}, status=400)
            self.post('care_intake', self.patient, mark)
            data = {'key': uuid.uuid4(), 'expected_version': 1, 'reason': 'Synthetic stop', 'confirm': 'on'}
            self.post('care_stop', self.second, data, [self.patient.pk, med['id']])
            self.post('care_stop', self.second, data, [self.patient.pk, med['id']])
            self.client.force_login(self.patient)
            response = self.client.get('/medicines/')
            self.assertEqual(response.context['medicines'], [])
            self.assertEqual(len(response.context['schedule_rows']), 1)
            self.assertEqual(response.context['schedule_rows'][0]['status'], 'taken')
            self.assertEqual(self.client.get('/medicines/', {'date': self.day+timedelta(days=1)}).context['schedule_rows'], [])
        self.assertEqual(PrescriptionVersion.objects.count(), 2)
        self.assertEqual(MedicationIntake.objects.count(), 1)

    def test_server_validation_notes_adverse_and_confirmed_plan(self):
        for changes in ({'dose': '-1'}, {'times': '25:99'}, {'unit': ''}, {'end': '2020-01-01'}, {'confirm': ''}):
            self.post('care_prescribe', self.doctor, self.prescription(**changes), [self.patient.pk], 400)
        self.assertEqual(PrescriptionVersion.objects.count(), 0)
        self.post('care_note', self.doctor, {'key': uuid.uuid4(), 'text': 'Synthetic note'}, [self.patient.pk])
        self.post('care_adverse', self.patient, {'key': uuid.uuid4(), 'medicine': 'Synthetic', 'text': 'Synthetic event'})
        data = {'key': uuid.uuid4(), 'expected_version': 0, 'reason': 'Synthetic confirmed plan', 'confirm': 'on'}
        self.post('care_plan', self.doctor, {**data, 'confirm': ''}, [self.patient.pk], 400)
        self.assertEqual(ClinicalAction.objects.count(), 0)
        self.post('care_plan', self.doctor, data, [self.patient.pk])
        self.post('care_plan', self.doctor, data, [self.patient.pk])
        self.assertEqual(ClinicalAction.objects.count(), 1)
        self.assertIsNone(ClinicalAction.objects.get().proposal_id)
        self.assertEqual((CareNote.objects.count(), AdverseEvent.objects.count()), (1, 1))
        self.client.get(self.card)
        self.assertEqual(ClinicalAction.objects.count(), 1)

    def test_post_only_csrf_and_moderator_integration(self):
        self.client.force_login(self.editor)
        self.assertContains(self.client.get('/'), 'Подготовить дневник')
        self.assertEqual(self.client.post(reverse('care_prepare_survey')).status_code, 302)
        count = QuestionnaireVersion.objects.count()
        self.client.post(reverse('care_prepare_survey'))
        self.assertEqual(QuestionnaireVersion.objects.count(), count)
        self.assertRedirects(self.client.get('/invitations/'), reverse('create_doctor'))
        self.post('attach_doctor', self.editor, {'doctor_id': self.foreign_doctor.pk, 'reason': 'Synthetic moderator UI'}, [self.patient.pk])
        self.assertEqual(CareRelationship.objects.filter(patient=self.patient, status='active').count(), 3)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.doctor)
        paths = [reverse(n, args=[self.patient.pk]) for n in ('care_message', 'care_note', 'care_prescribe', 'care_plan', 'care_assign')]
        paths += [reverse('care_intake'), reverse('care_adverse'), reverse('care_prepare_survey')]
        self.client.force_login(self.doctor)
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 405)
            self.assertEqual(csrf.post(path, {}).status_code, 403)
