"""Content fidelity, real assignment, corrections, safety and completion."""
import json
import uuid
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.test_b03 import setup_case, cmd
from quiz.models import SurveyAssignment, Outbox, QuestionnaireVersion, SurveySession
from quiz.services import diary, questionnaires as engine
from quiz.services.contracts import DomainError
from quiz.care_forms import AssignmentForm


@override_settings(DEMO_SURVEY_ENABLED=True)
class DiaryTests(TestCase):
    def setUp(self):
        setup_case(self)
        self.version = diary.prepare()

    def test_all_text_and_options_match_register(self):
        register = Path(__file__).with_name('testdata') / 'diary.json'
        rows = json.loads(register.read_text(encoding='utf-8'))
        definition = diary.definition()
        self.assertEqual([q['code'] for q in definition['questions']], [f'D{n:02}' for n in range(1, 14)] + [f'A{n:02}' for n in range(1, 5)])
        for q in definition['questions']:
            original = rows[q['code']]
            self.assertEqual(q['text'], original['text'])
            self.assertEqual([o['label'] for o in q['options']], original['options'])
            self.assertEqual([o['code'] for o in q['options']], [f"{q['code']}.{n}" for n in range(4)])
        self.assertEqual([q['code'] for q in definition['questions'] if q['safety_relevant']], ['D13'])
        self.assertEqual([q['code'] for q in definition['questions'] if q['allow_not_applicable']], ['D10', 'D11'])

    def test_preparation_is_idempotent_preserves_old_content_assigns_nobody(self):
        old = QuestionnaireVersion.objects.get(questionnaire=self.questionnaire)
        before = old.content.definition
        count = SurveyAssignment.objects.count()
        self.assertEqual(diary.prepare().pk, self.version.pk)
        self.assertEqual(SurveyAssignment.objects.count(), count)
        old.refresh_from_db()
        self.assertEqual(old.content.definition, before)
        self.assertEqual(old.content.definition['questions'][0]['code'], 'demo.context')
        self.assertEqual(self.version.status, 'demo')
        self.assertEqual(list(AssignmentForm().fields['version'].queryset), [self.version])
        form = AssignmentForm({'key': uuid.uuid4(), 'version': old.pk, 'day': timezone.localdate(), 'extra': 0})
        self.assertFalse(form.is_valid())

    def assigned_run(self):
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.post(reverse('care_assign', args=[self.patient.pk]), {'key': uuid.uuid4(), 'version': self.version.pk, 'day': timezone.localdate(), 'extra': 0}).status_code, 302)
        assignment = SurveyAssignment.objects.get(version=self.version)
        self.client.force_login(self.patient)
        self.assertContains(self.client.get('/surveys/'), diary.TITLE)
        self.assertEqual(self.client.post(reverse('survey_assignment_start', args=[assignment.pk]), {'key': uuid.uuid4()}).status_code, 302)
        return SurveySession.objects.get(assignment=assignment)

    def test_17_answers_reload_change_finish_and_doctor_history(self):
        run = self.assigned_run()
        url = reverse('survey_detail', args=[run.pk])
        page = self.client.get(url)
        self.assertEqual(len(page.context['rows']), 17)
        self.assertNotContains(page, 'value="missing"')
        self.assertNotContains(page, 'Сохранена ревизия')
        for q in diary.definition()['questions']:
            na = q['code'] in {'D10', 'D11'}
            engine.save_answer(self.patient, cmd(run, q['code'], None if na else q['code'] + '.1', state='not_applicable' if na else 'known'))
        engine.save_answer(self.patient, cmd(run, 'D13', 'D13.0', expected=1))
        with self.assertRaises(DomainError) as conflict:
            engine.save_answer(self.patient, cmd(run, 'D13', 'D13.3', expected=1))
        self.assertEqual(conflict.exception.code, 'stale_version')
        page = self.client.get(url)
        self.assertEqual(page.context['saved_count'], 17)
        self.assertEqual(page.context['rows'][12]['label'], '0 — нет')
        run.refresh_from_db()
        engine.finish(self.patient, run.pk, uuid.uuid4(), run.answer_generation)
        page = self.client.get(url)
        self.assertContains(page, 'Опрос завершён')
        self.assertContains(page, 'Было: 1 — мимолётные мысли, но без планов')
        self.assertContains(page, 'Стало: <b>0 — нет</b>', html=True)
        self.assertNotContains(page, 'Ответы по вопросу безопасности')
        self.client.force_login(self.doctor)
        page = self.client.get(url)
        self.assertContains(page, 'Ответы по вопросу безопасности')
        self.assertEqual([x['label'] for x in page.context['safety_history']], ['1 — мимолётные мысли, но без планов', '0 — нет'])
        self.assertEqual(Outbox.objects.filter(kind='safety.check_requested', payload__session_id=str(run.pk)).count(), 2)
        dynamics = self.client.get(reverse('patient_records', args=[self.patient.pk]), {'period': 'session', 'entry': run.pk})
        entry = dynamics.context['selected_entry']
        self.assertEqual(len(entry['raw']), 17)
        self.assertEqual(entry['raw'][12]['label'], '0 — нет')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_incomplete_and_missing_answers_cannot_finish(self):
        run = self.assigned_run()
        engine.save_answer(self.patient, cmd(run, 'D01', None, state='missing'))
        run.refresh_from_db()
        with self.assertRaises(DomainError) as error:
            engine.finish(self.patient, run.pk, uuid.uuid4(), run.answer_generation)
        self.assertEqual(error.exception.code, 'incomplete')
        run.refresh_from_db()
        self.assertEqual(run.status, 'in_progress')

    def test_working_screens_have_no_development_navigation(self):
        for actor, paths in [(self.patient, ['/', '/surveys/', '/activity/', '/medicines/', '/contact/']), (self.doctor, ['/', reverse('patient_records', args=[self.patient.pk])])]:
            self.client.force_login(actor)
            for path in paths:
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                for forbidden in ['Локальный прототип', 'Посмотреть демо', 'Результат MVP-3', 'Назначить опрос B03', 'Сайт доступен, пока работает сервер']:
                    self.assertNotContains(response, forbidden)

    def test_ui_preparation_backs_up_before_writing(self):
        self.client.force_login(self.editor)
        with patch('quiz.services.diary.backup_database', side_effect=RuntimeError('backup failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse('care_prepare_survey'))
        self.assertEqual(QuestionnaireVersion.objects.filter(questionnaire__code=diary.CODE).count(), 1)
