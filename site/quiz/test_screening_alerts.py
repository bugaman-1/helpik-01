"""Synthetic scoring, alerting, access and exploratory-quality regressions."""
import base64
import json
import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.models import Alert, CareRelationship, InteractionEvent, Invitation, Profile, PushSubscription, SurveySession
from quiz.services import screening, questionnaires as engine, alerts, response_quality, notifications, invitations
from quiz.services.contracts import DomainError
from quiz.test_b03 import setup_case, cmd


@override_settings(DEMO_SURVEY_ENABLED=True)
class ScreeningAlertTests(TestCase):
    def setUp(self):
        setup_case(self)
        self.screening = screening.start(self.patient)

    def complete(self, run=None, phq=0, gad=0, control=0):
        run = run or self.screening
        values = {}
        for prefix, score, count in [('PHQ', phq, 9), ('GAD', gad, 7)]:
            for n in range(1, count + 1):
                # Keep PHQ09 zero so score boundary tests do not raise safety alerts.
                val = min(3, score) if not (prefix == 'PHQ' and n == 9) else 0
                score -= val
                values[f'{prefix}{n:02}'] = val
        for question in run.questionnaire_version.content.definition['questions']:
            code = question['code']
            engine.save_answer(self.patient, cmd(run, code, f'{code}.{values.get(code, control)}'))
        run.refresh_from_db()
        engine.finish(self.patient, run.pk, uuid.uuid4(), run.answer_generation)
        run.refresh_from_db()
        return run

    def timing_events(self, run, seconds=5, pause=False):
        stream = uuid.uuid4()
        now = timezone.now()
        events = []
        at = 1000
        def event(code, kind, t):
            events.append(InteractionEvent(session=run, stream_id=stream, sequence=len(events)+1,
                question_code=code, kind=kind, elapsed_ms=int(t), client_at=now))
        for q in run.questionnaire_version.content.definition['questions']:
            code = q['code']
            event(code, 'question_visible', at)
            if pause:
                event(code, 'visibility_hidden', at+1000)
                event(code, 'visibility_visible', at+61000)
                at += 60000
            event(code, 'option_selected', at+seconds*1000)
            event(code, 'question_left', at+seconds*1000+200)
            at += seconds*1000+500
        InteractionEvent.objects.bulk_create(events)

    def test_reference_bands_at_every_boundary(self):
        for score, label in [(0,'Минимальная'), (4,'Минимальная'), (5,'Лёгкая'), (9,'Лёгкая'),
                             (10,'Умеренная'), (14,'Умеренная'), (15,'Умеренно тяжёлая'), (19,'Умеренно тяжёлая'), (20,'Тяжёлая'), (27,'Тяжёлая')]:
            self.assertEqual(screening.severity(score, 'phq')['label'], label)
        for score, label in [(0,'Минимальная'), (4,'Минимальная'), (5,'Лёгкая'), (9,'Лёгкая'), (10,'Умеренная'), (14,'Умеренная'), (15,'Выраженная'), (21,'Выраженная')]:
            self.assertEqual(screening.severity(score, 'gad')['label'], label)

    def test_control_is_unscored_and_low_scores_do_not_alert(self):
        self.complete(phq=9, gad=9, control=3)
        result = screening.results(self.screening)
        self.assertEqual((result['phq'], result['gad']), (9, 9))
        self.assertFalse(Alert.objects.exists())
        self.assertEqual(response_quality.describe(self.screening)['difference'], 0)

    def test_threshold_ten_and_idempotent_finish(self):
        self.complete(phq=10)
        alert = Alert.objects.get()
        self.assertEqual(alert.evidence_snapshot['priority'], 'review')
        alerts.evaluate(self.screening)
        engine.finish(self.patient, self.screening.pk, uuid.uuid4(), self.screening.answer_generation)
        self.assertEqual(Alert.objects.count(), 1)

    def test_higher_score_priority_and_no_clinical_action(self):
        self.complete(gad=15)
        self.assertEqual(Alert.objects.get().evidence_snapshot['priority'], 'high')
        self.assertFalse(self.patient.clinical_actions.exists())

    def test_safety_immediate_and_correction_cannot_remove_signal(self):
        engine.save_answer(self.patient, cmd(self.screening, 'PHQ09', 'PHQ09.1'))
        alert = Alert.objects.get()
        self.assertEqual(alert.evidence_snapshot['priority'], 'urgent')
        self.screening.refresh_from_db()
        self.assertEqual(self.screening.status, 'in_progress')
        engine.save_answer(self.patient, cmd(self.screening, 'PHQ09', 'PHQ09.0', expected=1))
        self.assertEqual(Alert.objects.count(), 1)
        self.assertTrue(screening.results(self.screening)['safety'])

    def test_alert_is_first_in_card_and_list_mark_is_accessible(self):
        self.complete(gad=10)
        self.client.force_login(self.doctor)
        page = self.client.get(reverse('patient_records', args=[self.patient.pk]))
        html = page.content.decode()
        self.assertLess(html.index('id="patient-alerts"'), html.index('КАРТОЧКА ПАЦИЕНТА'))
        self.assertContains(self.client.get('/'), 'aria-label="Требует внимания врача"')
        self.assertContains(page, 'Особенности прохождения')
        self.client.force_login(self.patient)
        for path in ['/', reverse('survey_detail', args=[self.screening.pk])]:
            page = self.client.get(path)
            self.assertNotContains(page, 'Особенности прохождения')
            self.assertNotContains(page, 'Медиана выбора ответа')
            self.assertNotContains(page, 'Что менялось')
        self.assertEqual(self.client.get(reverse('notification_feed')).status_code, 403)

    def test_new_positive_after_review_reopens_safety_signal(self):
        engine.save_answer(self.patient, cmd(self.screening, 'PHQ09', 'PHQ09.1'))
        alert = Alert.objects.get()
        alerts.resolve(self.doctor, alert.pk, 'closed', 'Synthetic review')
        engine.save_answer(self.patient, cmd(self.screening, 'PHQ09', 'PHQ09.3', expected=1))
        alert.refresh_from_db()
        self.assertEqual(alert.status, 'open')
        self.assertEqual(Alert.objects.count(), 1)
        self.assertEqual(alert.evidence.value, 'PHQ09.3')

    def test_feed_multiple_doctors_and_revoked_relationship(self):
        self.complete(gad=10)
        second = get_user_model().objects.create_user(username='synthetic_second_doctor')
        Profile.objects.create(user=second, role='doctor', verification_status='approved')
        CareRelationship.objects.create(patient=self.patient, doctor=second, reason='Synthetic')
        for actor in [self.doctor, second]:
            self.client.force_login(actor)
            data = self.client.get(reverse('notification_feed')).json()
            self.assertEqual(data['count'], 1)
            self.assertEqual(data['items'][0]['patient_id'], self.patient.pk)
        CareRelationship.objects.filter(doctor=second).update(status='ended', ended_at=timezone.now(), is_primary=False)
        self.assertEqual(self.client.get(reverse('notification_feed')).json()['count'], 0)
        alert = Alert.objects.get()
        self.assertEqual(self.client.post(reverse('alert_review', args=[alert.pk]), {'status': 'closed', 'note':'Synthetic'}).status_code, 403)

    def test_card_groups_repeated_alerts_preserving_older_urgent_evidence(self):
        self.complete(phq=10)
        with patch('django.utils.timezone.now', return_value=timezone.now()-timedelta(days=2)):
            urgent = Alert.objects.create(patient=self.patient, responsible=self.doctor, episode_key=str(uuid.uuid4()),
                evidence_snapshot={'rule':'safety','priority':'urgent','title':'Нужна проверка безопасности','message':'Synthetic urgent evidence'})
        Alert.objects.create(patient=self.patient, responsible=self.doctor, episode_key=str(uuid.uuid4()),
            evidence_snapshot={'rule':'safety','priority':'review','title':'Нужна проверка безопасности','message':'Synthetic later evidence'})
        self.client.force_login(self.doctor)
        page = self.client.get(reverse('patient_records', args=[self.patient.pk]))
        groups = page.context['patient_alert_groups']
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['alert'].pk, urgent.pk)
        self.assertEqual(groups[0]['count'], 2)
        self.assertContains(page, 'data-contact-patient', count=1)
        self.assertContains(page, 'href="#patient-messages"')
        self.assertNotContains(page, 'Беру в работу')
        self.assertContains(page, 'Synthetic urgent evidence')
        self.assertContains(page, 'Synthetic later evidence')
        self.assertEqual(Alert.objects.filter(status='open').count(), 3)

    def test_doctor_acknowledges_and_closes_with_reason(self):
        self.complete(phq=10)
        alert = Alert.objects.get()
        self.client.force_login(self.doctor)
        url = reverse('alert_review', args=[alert.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {'status':'acknowledged'}).status_code, 302)
        self.assertEqual(self.client.get(reverse('notification_feed')).json()['count'], 0)
        self.assertContains(self.client.get(reverse('patient_records', args=[self.patient.pk])), 'Принят врачом')
        self.assertEqual(self.client.post(url, {'status':'closed'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'status':'closed','note':'Synthetic review completed'}).status_code, 302)
        self.assertFalse(alerts.active_for(self.patient.pk).exists())
        alerts.evaluate(self.screening)
        self.assertEqual(Alert.objects.count(), 1)

    def test_missing_telemetry_is_not_low_quality_and_patient_can_opt_out(self):
        self.complete()
        data = response_quality.describe(self.screening)
        self.assertEqual(data['level'], 'incomplete')
        self.assertEqual(data['marker_count'], 0)
        self.assertIsNone(data['median_seconds'])
        self.client.force_login(self.patient)
        self.client.post(reverse('survey_consent', args=[self.screening.pk]), {'enabled':'false'})
        self.screening.refresh_from_db()
        self.assertFalse(self.screening.telemetry_enabled)

    def test_active_time_excludes_hidden_tab_and_control_excluded_from_timing(self):
        self.complete()
        self.timing_events(self.screening, seconds=5, pause=True)
        data = response_quality.describe(self.screening)
        self.assertEqual(data['median_seconds'], 5)
        self.assertEqual(data['timed_count'], 16)
        self.assertEqual(data['level'], 'ordinary')
        self.assertEqual(data['hidden'], 17)

    def test_fast_and_control_difference_are_exploratory_not_clinical(self):
        self.complete(control=3)
        self.timing_events(self.screening, seconds=0.8)
        data = response_quality.describe(self.screening)
        self.assertEqual(data['marker_count'], 2)
        self.assertEqual(data['level'], 'review')
        self.assertIsNone(data['baseline_seconds'])
        self.assertEqual((screening.results(self.screening)['phq'], screening.results(self.screening)['gad']), (0,0))
        self.assertFalse(Alert.objects.exists())

    def test_personal_baseline_uses_only_previous_same_revision_sessions(self):
        version = self.screening.questionnaire_version
        for n, seconds in enumerate((4,5,6), 1):
            now = timezone.now()
            assignment = engine._assign(self.patient, self.patient, version, key=uuid.uuid4(), timezone_name='Europe/Moscow',
                available_from=now-timedelta(hours=1), available_until=now+timedelta(hours=1),
                observation_start=now-timedelta(days=14), observation_end=now, window_code='test', unscheduled_limit=0)
            run = engine.start(self.patient, assignment.pk, uuid.uuid4())
            self.complete(run)
            run.telemetry_enabled = True
            run.save(update_fields=['telemetry_enabled'])
            self.timing_events(run, seconds=seconds)
            SurveySession.objects.filter(pk=run.pk).update(started_at=now-timedelta(days=n+1), completed_at=now-timedelta(days=n))
        self.complete()
        self.timing_events(self.screening, seconds=1.8)
        data = response_quality.describe(self.screening)
        self.assertEqual(data['baseline_count'], 3)
        self.assertEqual(data['baseline_seconds'], 5)
        self.assertEqual(data['fast_seconds'], 2)
        self.assertEqual(data['marker_count'], 1)

    def test_gaps_and_multiple_streams_do_not_produce_fake_timing(self):
        self.complete()
        self.timing_events(self.screening)
        event = self.screening.interactions.first()
        InteractionEvent.objects.create(session=self.screening, stream_id=uuid.uuid4(), sequence=1,
            kind='page_resume', question_code='', elapsed_ms=1, client_at=timezone.now())
        data = response_quality.describe(self.screening)
        self.assertIsNone(data['median_seconds'])
        self.assertIn('multiple_streams_no_cross_stream_sum', data['quality_flags'])

    def test_only_moderator_can_create_doctor_without_code(self):
        path = reverse('create_doctor')
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.client.force_login(self.editor)
        password = 'synthetic-strong-demo-phrase-123'
        response = self.client.post(path, {'username':'synthetic_new_physician','password1':password,'password2':password})
        self.assertEqual(response.status_code, 302)
        doctor = get_user_model().objects.get(username='synthetic_new_physician')
        self.assertEqual(doctor.profile.role, 'doctor')
        self.assertEqual(doctor.profile.verification_status, 'approved')
        self.assertTrue(doctor.check_password(password))
        self.assertFalse(doctor.is_staff)
        with self.assertRaises(DomainError):
            invitations.issue(self.editor, role='doctor', qualification_reference='Synthetic')
        old = Invitation.objects.create(issuer=self.editor, role='doctor', token_digest=invitations.digest_token(secrets.token_urlsafe(32)), expires_at=timezone.now()+timedelta(days=1))
        with self.assertRaises(DomainError):
            invitations.register_from_context(old.token_digest, get_user_model()(username='synthetic_bad'))

    def test_push_subscription_validation_and_mocked_delivery(self):
        key_path = settings.BASE_DIR / 'runtime' / ('test-vapid-' + uuid.uuid4().hex + '.pem')
        self.addCleanup(key_path.unlink, missing_ok=True)
        with override_settings(WEBPUSH_PRIVATE_KEY=key_path, CSRF_TRUSTED_ORIGINS=['http://testserver']):
            call_command('prepare_notifications', verbosity=0)
            subscription = {'endpoint':'https://fcm.googleapis.com/fcm/send/synthetic', 'keys':{
                'auth':base64.urlsafe_b64encode(b'0'*16).decode().rstrip('='), 'p256dh':notifications.public_key()}}
            self.assertTrue(notifications.valid_subscription(subscription))
            self.assertFalse(notifications.valid_subscription({**subscription, 'endpoint':'https://127.0.0.1/private'}))
            self.client.force_login(self.doctor)
            response = self.client.post(reverse('push_subscription'), data=json.dumps(subscription), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(PushSubscription.objects.count(), 1)
            self.complete(gad=10)
            with patch('pywebpush.webpush') as send:
                notifications.deliver_alert(Alert.objects.get().pk)
                self.assertEqual(send.call_count, 1)
                payload = json.loads(send.call_args.kwargs['data'])
                self.assertNotIn(self.patient.username, json.dumps(payload))
                self.assertEqual(send.call_args.kwargs['timeout'], 3)
            CareRelationship.objects.filter(patient=self.patient).update(status='ended', ended_at=timezone.now(), is_primary=False)
            with patch('pywebpush.webpush') as send:
                notifications.deliver_alert(Alert.objects.get().pk)
                send.assert_not_called()
            self.client.post(reverse('push_subscription'), data=json.dumps({'remove':True, 'endpoint':subscription['endpoint']}), content_type='application/json')
            self.assertFalse(PushSubscription.objects.exists())
