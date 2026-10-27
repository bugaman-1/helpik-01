"""Synthetic UI regressions: access, read-only previews and role-bound invitations."""
from urllib.parse import urlparse, parse_qs
from django.test import TestCase, override_settings
from django.urls import reverse
from quiz.test_b02 import user
from quiz.services import invitations, care
from quiz.models import SurveyRecord, SurveySession, Invitation


@override_settings(DEMO_SURVEY_ENABLED=True)
class MobileUITests(TestCase):
    def test_preview_screens_states_and_no_mutation(self):
        for screen in ('today', 'activity', 'medicines', 'contact', 'doctor', 'patient', 'moderator', 'survey'):
            url = reverse('ui_preview', args=[screen])
            for state in ('normal', 'empty', 'attention'):
                response = self.client.get(url, {'state': state})
                self.assertEqual(response.status_code, 200, (screen, state))
                self.assertContains(response, 'Демо')
            self.assertEqual(self.client.post(url, {'message': 'SYNTHETIC'}).status_code, 405)
        self.assertEqual(SurveySession.objects.count(), 0)
        self.assertEqual(SurveyRecord.objects.count(), 0)
        self.assertEqual(Invitation.objects.count(), 0)

    @override_settings(DEMO_SURVEY_ENABLED=False)
    def test_preview_disabled_outside_demo(self):
        self.assertEqual(self.client.get('/demo/ui/today/').status_code, 403)

    def test_patient_navigation_retains_identity_and_empty_state(self):
        response = self.client.get('/demo/ui/patient/', {'patient': 'elena', 'state': 'empty'})
        self.assertEqual(response.context['entries'], [])
        query = parse_qs(urlparse(response.context['next_url']).query)
        self.assertEqual(query['patient'], ['elena'])
        self.assertEqual(query['state'], ['empty'])
        self.assertContains(response, 'Елена Морозова')
        self.assertContains(self.client.get('/demo/ui/patient/', {'state': 'empty'}), 'Елена Морозова')

    def test_patient_pages_reject_other_roles_and_posts(self):
        patient = user('ui_patient', 'patient')
        doctor = user('ui_doctor', 'doctor', True)
        for actor in (patient, doctor):
            self.client.force_login(actor)
            for path in ('/activity/', '/medicines/', '/contact/'):
                self.assertEqual(self.client.get(path).status_code, 200 if actor == patient else 403)
                self.assertEqual(self.client.post(path).status_code, 405)

    def test_doctor_search_never_expands_access(self):
        moderator = user('ui_moderator', 'moderator')
        doctor = user('ui_doctor', 'doctor', True)
        own = user('ui_own_patient', 'patient')
        other = user('ui_hidden_patient', 'patient')
        care.attach(moderator, own.pk, doctor.pk, 'Synthetic UI assignment')
        self.client.force_login(doctor)
        response = self.client.get('/', {'q': 'hidden'})
        self.assertEqual(response.context['patient_cards'], [])
        self.assertEqual(self.client.get(reverse('patient_records', args=[other.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('patient_records', args=[own.pk])).status_code, 200)

    def test_forged_role_field_cannot_change_patient_invitation(self):
        doctor = user('ui_issuer', 'doctor', True)
        invite, token = invitations.issue(doctor, role='patient')
        response = self.client.post('/invite/', {'token': token, 'selected_role': 'doctor'})
        self.assertRedirects(response, '/register/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/register/').context['invitation_role'], 'patient')
        invite.refresh_from_db()
        self.assertIsNone(invite.used_at)

    def test_safety_correction_remains_visible_to_attending_doctor(self):
        from quiz.test_b03 import setup_case, cmd
        from quiz.services import questionnaires
        setup_case(self)
        questionnaires.save_answer(self.patient, cmd(self.run, value='yes'))
        questionnaires.save_answer(self.patient, cmd(self.run, value='no', expected=1))
        self.client.force_login(self.doctor)
        response = self.client.get(reverse('survey_detail', args=[self.run.pk]))
        self.assertEqual([r['label'] for r in response.context['safety_history']],
                         ['Да (синтетический пример)', 'Нет (синтетический пример)'])
        self.assertContains(response, 'История ответов и исправлений')
        self.assertContains(response, 'Было: Да (синтетический пример)')
        self.assertContains(response, 'Стало: <b>Нет (синтетический пример)</b>', html=True)

    def test_calendar_can_open_old_archive_beyond_recent_records(self):
        from datetime import datetime, timezone
        patient = user('ui_archive', 'patient')
        old = SurveyRecord.objects.create(user=patient, user_name='SYNTHETIC')
        SurveyRecord.objects.filter(pk=old.pk).update(created_at=datetime(2020, 2, 10, 12, tzinfo=timezone.utc))
        SurveyRecord.objects.bulk_create([SurveyRecord(user=patient, user_name='SYNTHETIC') for _ in range(201)])
        self.client.force_login(patient)
        response = self.client.get('/activity/', {'date': '2020-02-10', 'period': 'day', 'entry': f'legacy-{old.pk}'})
        self.assertEqual(response.context['selected_entry']['id'], f'legacy-{old.pk}')
        self.assertEqual(len(response.context['entries']), 1)
