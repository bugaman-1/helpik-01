"""Regression tests for the supplied daily questionnaire and both scoring keys."""
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from quiz.models import Alert, SurveySession
from quiz.services import checkin, screening, questionnaires as engine, response_quality
from quiz.test_b03 import setup_case, cmd


@override_settings(DEMO_SURVEY_ENABLED=True)
class CheckinTests(TestCase):
    def setUp(self):
        setup_case(self)

    def complete(self, *, mood=0, safety=0, anxiety=20, control=0):
        run = checkin.start(self.patient)
        remaining = anxiety - 20
        for q in checkin.definition()['questions']:
            code = q['code']
            value = mood if code.startswith('BDI') else control
            if code == 'BDI04':
                value = safety
            if code.startswith('STAI'):
                portion = min(3, remaining)
                remaining -= portion
                value = 3 - portion if int(code[4:]) in checkin.REVERSE else portion
            engine.save_answer(self.patient, cmd(run, code, f'{code}.{value}'))
        run.refresh_from_db()
        engine.finish(self.patient, run.pk, uuid.uuid4(), run.answer_generation)
        run.refresh_from_db()
        return run

    def test_definition_and_reverse_key_full_range(self):
        self.assertEqual(len(checkin.definition()['questions']), 26)
        now = timezone.now()
        for offset, score in enumerate([20, 30, 31, 45, 46, 80]):
            with patch('django.utils.timezone.now', return_value=now + timedelta(days=offset)):
                run = self.complete(anxiety=score, control=3)
                result = screening.results(run)
                self.assertEqual(result['anxiety'], score)
                self.assertEqual(result['depression'], 0)
                self.assertEqual(result['anxiety_band']['label'], 'Низкая' if score <= 30 else 'Умеренная' if score <= 45 else 'Высокая')

    def test_daily_resume_new_day_and_old_results_preserved(self):
        legacy = screening.start(self.patient)
        run = self.complete(mood=1)
        self.assertNotEqual(legacy.questionnaire_version_id, run.questionnaire_version_id)
        self.assertEqual(checkin.start(self.patient).pk, run.pk)
        self.assertEqual(screening.results(run)['depression'], 4)
        with patch('django.utils.timezone.now', return_value=timezone.now() + timedelta(days=1)):
            self.assertNotEqual(checkin.start(self.patient).pk, run.pk)
        self.assertIn('phq', screening.results(legacy))

    def test_safety_immediate_escalation_and_retained(self):
        run = checkin.start(self.patient)
        engine.save_answer(self.patient, cmd(run, 'BDI04', 'BDI04.1'))
        self.assertEqual(Alert.objects.get().evidence_snapshot['priority'], 'review')
        engine.save_answer(self.patient, cmd(run, 'BDI04', 'BDI04.2', expected=1))
        self.assertEqual(Alert.objects.get().evidence_snapshot['priority'], 'urgent')
        engine.save_answer(self.patient, cmd(run, 'BDI04', 'BDI04.0', expected=2))
        self.assertTrue(checkin.results(run)['safety'])
        self.assertFalse(self.patient.clinical_actions.exists())

    def test_rise_uses_recent_same_version_baseline(self):
        now = timezone.now()
        for ago in [3, 2, 1]:
            with patch('django.utils.timezone.now', return_value=now - timedelta(days=ago)):
                self.complete(mood=0)
        run = self.complete(mood=1)
        self.assertEqual(checkin.change_from_baseline(run, 4), {'baseline': 0, 'delta': 4})
        self.assertEqual(Alert.objects.get().evidence_snapshot['rule'], 'change')
        self.assertFalse(self.patient.clinical_actions.exists())

    def test_no_baseline_no_rise_alert_and_anxiety_threshold(self):
        self.complete(mood=3, anxiety=45)
        self.assertFalse(Alert.objects.exists())

    def test_daily_route_private_quality_and_chart(self):
        run = self.complete(anxiety=46)
        self.client.force_login(self.patient)
        self.assertRedirects(self.client.post(reverse('screening_start')), reverse('survey_detail', args=[run.pk]))
        page = self.client.get('/')
        self.assertContains(page, 'Опрос заполнен')
        self.assertContains(page, 'Текущая тревога')
        self.assertNotContains(page, 'Особенности прохождения')
        self.assertNotContains(page, 'PHQ-9')
        self.client.force_login(self.doctor)
        page = self.client.get(reverse('patient_records', args=[self.patient.pk]))
        self.assertContains(page, 'Особенности прохождения')
        self.assertContains(page, 'quality-scale')
        self.assertNotContains(page, 'Как интерпретировать')
        self.assertLess(page.content.index(b'patient-alerts'), page.content.index(b'page-heading'))

    def test_control_pair_is_new_anxiety_item(self):
        run = self.complete(anxiety=20, control=3)
        quality = response_quality.describe(run)
        self.assertEqual(quality['difference'], 3)
        self.assertEqual(quality['level'], 'incomplete')
        self.assertEqual(checkin.results(run)['anxiety'], 20)
