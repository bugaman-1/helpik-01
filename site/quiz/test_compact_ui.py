import uuid
from django.test import TestCase, override_settings
from django.urls import reverse
from quiz.test_b03 import setup_case, cmd
from quiz.models import SurveySession, SurveyAssignment
from quiz.services import screening, questionnaires


@override_settings(DEMO_SURVEY_ENABLED=True)
class CompactUITests(TestCase):
    def setUp(self):
        setup_case(self)

    def test_screening_resume_and_complete_separate_totals_and_safety_history(self):
        from quiz.services import checkin as screening
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(reverse('screening_start')).status_code, 405)
        response = self.client.post(reverse('screening_start'))
        run = SurveySession.objects.get(patient=self.patient, questionnaire_version__questionnaire__code=screening.CODE)
        self.assertRedirects(response, reverse('survey_detail', args=[run.pk]))
        self.assertEqual(screening.start(self.patient).pk, run.pk)
        self.assertEqual(screening.results(run)['depression'], None)
        for q in screening.definition()['questions']:
            value = 1 if q['code'].startswith('BDI') else 2
            questionnaires.save_answer(self.patient, cmd(run, q['code'], f"{q['code']}.{value}"))
        questionnaires.save_answer(self.patient, cmd(run, 'BDI04', 'BDI04.0', expected=1))
        run.refresh_from_db()
        questionnaires.finish(self.patient, run.pk, uuid.uuid4(), run.answer_generation)
        run.refresh_from_db()
        result = screening.results(run)
        self.assertEqual({key: result[key] for key in ('depression', 'anxiety', 'safety')}, {'depression': 4, 'anxiety': 50, 'safety': True})
        self.assertEqual(screening.start(self.patient).pk, run.pk)
        self.assertContains(self.client.get('/'), 'Опрос заполнен')
        self.client.force_login(self.doctor)
        self.assertContains(self.client.get(reverse('patient_records', args=[self.patient.pk])), 'Этот ответ важно обсудить лично')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('survey_detail', args=[run.pk])).status_code, 403)

    def test_settings_saved_role_navigation_and_contact_links(self):
        for actor, role in [(self.patient, 'patient'), (self.doctor, 'doctor'), (self.editor, 'moderator')]:
            self.client.force_login(actor)
            page = self.client.get('/')
            self.assertContains(page, f'data-role="{role}"')
            self.assertContains(self.client.get('/settings/'), 'Настройки')
        self.client.force_login(self.doctor)
        response = self.client.post('/settings/', {'action': 'save', 'theme': 'dark', 'alerts_only': 'on', 'email': 'doctor@example.test', 'contact_phone': '+7 999 123-45-67', 'contact_address': 'Тестовый кабинет'})
        self.assertRedirects(response, '/settings/')
        self.assertContains(self.client.get('/'), 'data-theme="dark"')
        self.assertTrue(self.client.get('/').context['attention'])
        self.assertFalse(self.client.get('/?filter=all').context['attention'])
        self.client.force_login(self.patient)
        page = self.client.get('/contact/')
        self.assertContains(page, 'href="mailto:doctor@example.test"')
        self.assertContains(page, 'href="tel:+7 999 123-45-67"')
        self.assertContains(page, 'Тестовый кабинет')
        self.client.post('/settings/', {'action': 'save', 'theme': 'dark'})
        self.assertNotContains(self.client.get('/'), 'Напоминание:')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get('/contact/'), 'doctor@example.test')

    def test_account_close_requires_password_and_preserves_records(self):
        self.patient.set_password('synthetic-test-password')
        self.patient.save()
        self.client.force_login(self.patient)
        count = SurveySession.objects.count()
        self.client.post('/settings/', {'action': 'close', 'password': 'wrong', 'confirm': 'on'})
        self.patient.refresh_from_db()
        self.assertTrue(self.patient.is_active)
        response = self.client.post('/settings/', {'action': 'close', 'password': 'synthetic-test-password', 'confirm': 'on'})
        self.assertRedirects(response, '/login/')
        self.patient.refresh_from_db()
        self.assertFalse(self.patient.is_active)
        self.assertEqual(SurveySession.objects.count(), count)
        self.assertEqual(self.client.get('/settings/').status_code, 302)

    def test_start_requires_patient_and_get_does_not_create_data(self):
        self.client.force_login(self.patient)
        before = SurveyAssignment.objects.count()
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(before, SurveyAssignment.objects.count())
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.post(reverse('screening_start')).status_code, 403)
