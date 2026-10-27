import uuid
from datetime import timedelta
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from quiz.test_b03 import setup_case
from quiz.test_b02 import user
from quiz.models import ReportedMedication, PrescriptionVersion, ClinicalAction, CareRelationship
from quiz.care_forms import ReportedMedicationForm
from quiz.services import drug_catalog as catalog, medication_review as service
from quiz.services.care_records import save_action
from quiz.services.contracts import DomainError


@override_settings(DEMO_SURVEY_ENABLED=True)
class MedicationTests(TestCase):
    def setUp(self):
        setup_case(self)
        self.today = timezone.localdate()
        self.card = reverse('patient_records', args=[self.patient.pk])

    def record_data(self, name, **changes):
        return {'key': uuid.uuid4(), 'entry_id': uuid.uuid4(), 'expected_revision': 0, 'name': name,
            'regimen': 'Синтетическая запись', 'start': self.today-timedelta(days=2), 'end': None,
            'status': 'taking', 'source': 'patient', 'note': '', **changes}

    def add(self, name, **changes):
        return service.save_reported(self.doctor, self.patient, self.record_data(name, **changes))

    def prescription(self, name, **changes):
        return {'key': uuid.uuid4(), 'expected_version': 0, 'medication_id': None, 'name': name,
            'dose': '1', 'unit': 'синтетическая единица', 'times': ['08:00'], 'start': self.today,
            'end': self.today+timedelta(days=3), 'note': '', 'reason': 'Синтетический тест', 'confirm': True, **changes}

    def review(self, name, start=None, end=None, exclude=''):
        return service.review(name, start or self.today, end or self.today, service.medication_context(self.patient, exclude))

    def assert_error(self, code, callback, *args, **kwargs):
        with self.assertRaises(DomainError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_catalog_recognizes_explicit_aliases_and_strengths_without_fuzzy_matches(self):
        for name, code in [('АВИАНДР® 20 мг', 'aviandr'), ('Ципролет', 'ciprofloxacin'), ('Клозапин 25 мг', 'clozapine'), ('Зивокс', 'linezolid')]:
            self.assertEqual(catalog.resolve(name)['code'], code)
        for name in ['Авиандр-подобный', 'неизвестный препарат', 'Ципролет А', 'аспирин']:
            self.assertIsNone(catalog.resolve(name))
        self.assertEqual(len({d['code'] for d in catalog.CATALOG}), len(catalog.CATALOG))

    def test_word_interactions_symmetric_not_all_antibiotics_are_contraindicated(self):
        self.add('Ципрофлоксацин')
        check = self.review('Клозапин')
        self.assertEqual(check['level'], 'warning')
        self.assertTrue(any('CYP1A2' in x['detail'] for x in check['findings']))
        self.assertEqual(self.review('Авиандр')['level'], 'note')
        self.add('Клозапин')
        self.assertEqual(self.review('Ципрофлоксацин')['level'], 'warning')
        self.add('Азитромицин')
        self.assertTrue(any(f['id'].startswith('antipsychotic-qt') for f in self.review('Галоперидол')['findings']))

    def test_aviandr_linezolid_is_blocked_and_explains_both_sources(self):
        self.add('Линезолид')
        check = self.review('маритупирдин')
        self.assertEqual(check['level'], 'blocked')
        sources = {s['url'] for f in check['findings'] for s in f['sources']}
        self.assertIn(catalog.SOURCES['vidal']['url'], sources)
        self.assertIn(catalog.SOURCES['linezolid']['url'], sources)
        self.assertEqual(self.review('Сертралин')['level'], 'blocked')

    def test_courses_future_dates_and_14_complete_calendar_days_after_maoi(self):
        self.add('Моклобемид', start=self.today-timedelta(days=20), end=self.today-timedelta(days=14), status='stopped')
        self.assertEqual(self.review('Авиандр')['level'], 'blocked')
        self.assertEqual(self.review('Авиандр', self.today+timedelta(days=1), self.today+timedelta(days=1))['level'], 'unknown')
        self.add('Ципрофлоксацин', end=self.today-timedelta(days=1))
        self.assertEqual(self.review('Клозапин')['level'], 'unknown')
        self.assertEqual(self.review('Клозапин', self.today-timedelta(days=1), self.today-timedelta(days=1))['level'], 'warning')
        self.add('Азитромицин', start=self.today+timedelta(days=5), end=self.today+timedelta(days=8))
        self.assertEqual(self.review('Галоперидол')['level'], 'unknown')
        self.assertEqual(self.review('Галоперидол', self.today+timedelta(days=6), self.today+timedelta(days=7))['level'], 'warning')

    def test_unknown_and_material_no_interaction_notes_never_claim_safe(self):
        self.add('Неизвестное средство')
        self.add('Амоксициллин')
        check = self.review('Оланзапин')
        self.assertEqual(check['level'], 'note')
        self.assertIn('Неизвестное средство', check['unknown'])
        self.assertIn('без подтверждения', check['findings'][0]['detail'])
        self.assertEqual(self.review('Неизвестный кандидат')['recognized'], None)
        self.assertEqual(self.review('Неизвестный кандидат')['level'], 'unknown')

    def test_statins_alternatives_are_explicit_and_rechecked(self):
        self.add('Флувоксамин')
        data = service.picker(self.patient, 'Симвастатин')
        self.assertEqual(data['selected']['level'], 'warning')
        self.assertEqual({d['code'] for d in data['alternatives']}, {'rosuvastatin', 'pravastatin'})
        self.assertTrue(all(d['level'] == 'note' for d in data['alternatives']))
        self.add('Розувастатин')
        alternatives = service.picker(self.patient, 'Симвастатин')['alternatives']
        self.assertEqual(next(d for d in alternatives if d['code'] == 'rosuvastatin')['level'], 'warning')

    def test_estrogens_lamotrigine_and_vidal_cautions(self):
        self.add('КОК')
        self.assertEqual(self.review('Ламотриджин')['level'], 'warning')
        self.assertEqual(self.review('Амитриптилин')['level'], 'warning')
        self.assertEqual(self.review('Клозапин')['level'], 'warning')
        self.assertEqual(self.review('Сертралин')['level'], 'note')
        self.add('Димедрол')
        findings = self.review('Авиандр')['findings']
        self.assertTrue(any(f['id'].startswith('aviandr-sedative') for f in findings))
        self.assertTrue(any(f['id'].startswith('aviandr-cyp2d6') for f in findings))

    def test_reported_history_exact_retry_and_stale_correction(self):
        data = self.record_data('Ципролет')
        first = service.save_reported(self.doctor, self.patient, data)
        self.assertEqual(service.save_reported(self.doctor, self.patient, data).pk, first.pk)
        self.assertEqual(first.catalog_code, 'ciprofloxacin')
        second = service.save_reported(self.doctor, self.patient, {**data, 'key': uuid.uuid4(), 'expected_revision': 1,
            'status': 'stopped', 'end': self.today})
        self.assertEqual(second.revision, 2)
        self.assertEqual(len(service.latest_reported(self.patient)), 1)
        self.assertEqual(ReportedMedication.objects.count(), 2)
        first.refresh_from_db()
        self.assertEqual(first.status, 'taking')
        self.assert_error('stale_medication', service.save_reported, self.doctor, self.patient,
            {**data, 'key': uuid.uuid4(), 'expected_revision': 1})
        self.assert_error('idempotency_conflict', service.save_reported, self.doctor, self.patient, {**data, 'name': 'Другое'})
        with self.assertRaises(ValueError):
            ReportedMedication.objects.filter(pk=first.pk).delete()

    def test_reported_status_follows_dates_without_extra_fields(self):
        for end, status in [(None, 'taking'), (self.today, 'taking'), (self.today+timedelta(days=1), 'taking'), (self.today-timedelta(days=1), 'stopped')]:
            form = ReportedMedicationForm(self.record_data('Линезолид', end=end))
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['status'], status)
            self.assertEqual(form.cleaned_data['source'], 'doctor')
            self.assertNotIn('status', form.fields)
            self.assertNotIn('source', form.fields)
        self.assertFalse(ReportedMedicationForm(self.record_data('Линезолид', end=self.today-timedelta(days=3))).is_valid())

    def test_blocked_server_save_and_no_javascript_form_keep_input(self):
        self.add('Линезолид')
        data = self.prescription('Авиандр 20 мг')
        self.assert_error('medication_contraindicated', save_action, self.doctor, self.patient, data, kind='prescription')
        self.assertEqual(ClinicalAction.objects.count(), 0)
        self.client.force_login(self.doctor)
        post = {**data, 'medication_id': '', 'times': '08:00', 'confirm': 'on'}
        response = self.client.post(reverse('care_prescribe', args=[self.patient.pk]), post)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Противопоказанное сочетание', status_code=409)
        self.assertEqual(response.context['prescription_form']['name'].value(), data['name'])
        self.assertFalse(PrescriptionVersion.objects.exists())

    def test_prescription_without_reason_field_keeps_confirmation_and_retry(self):
        self.client.force_login(self.doctor)
        data = self.prescription('Авиандр')
        data.pop('reason')
        payload = {**data, 'times': '08:00', 'medication_id': '', 'confirm': 'on'}
        path = reverse('care_prescribe', args=[self.patient.pk])
        self.assertEqual(self.client.post(path, {**payload, 'confirm': ''}).status_code, 400)
        self.assertFalse(ClinicalAction.objects.exists())
        self.assertEqual(self.client.post(path, payload).status_code, 302)
        self.assertEqual(self.client.post(path, payload).status_code, 302)
        self.assertEqual(ClinicalAction.objects.count(), 1)
        self.assertEqual(ClinicalAction.objects.get().reason, 'Назначение лекарства')
        card = self.client.get(self.card)
        for text in ['План и решение врача', 'Заметка врача', 'Назначить опрос', 'Основание назначения / изменения', 'История схем', 'Подтверждённая схема', 'Источник сведений']:
            self.assertNotIn(text, card.content.decode())

    def test_caution_needs_ack_and_current_token_snapshot_and_retry(self):
        self.add('Ципрофлоксацин')
        data = self.prescription('Клозапин')
        self.assert_error('medication_review_required', save_action, self.doctor, self.patient, data, kind='prescription')
        check = self.review(data['name'], data['start'], data['end'])
        approved = {**data, 'interaction_ack': True, 'interaction_token': check['token']}
        self.add('Другое средство')
        self.assert_error('medication_context_changed', save_action, self.doctor, self.patient, approved, kind='prescription')
        approved['interaction_token'] = self.review(data['name'], data['start'], data['end'])['token']
        action = save_action(self.doctor, self.patient, approved, kind='prescription')
        self.assertEqual(save_action(self.doctor, self.patient, approved, kind='prescription').pk, action.pk)
        snapshot = PrescriptionVersion.objects.get().definition['interaction_review']
        self.assertTrue(snapshot['acknowledged'])
        self.assertIn('Другое средство', snapshot['unknown'])
        changed_dates = {**approved, 'key': uuid.uuid4(), 'end': data['end']+timedelta(days=1), 'expected_version': 1}
        self.assert_error('medication_context_changed', save_action, self.doctor, self.patient, changed_dates, kind='prescription')

    def test_editing_does_not_warn_for_self_but_cannot_erase_maoi_interval(self):
        first = self.prescription('Моклобемид')
        save_action(self.doctor, self.patient, first, kind='prescription')
        plan = PrescriptionVersion.objects.get()
        med_id = plan.definition['medicines'][0]['id']
        self.assertEqual(self.review('Моклобемид', exclude=med_id)['level'], 'unknown')
        replacement = self.prescription('Авиандр', medication_id=uuid.UUID(med_id), expected_version=1,
            start=self.today+timedelta(days=5), end=self.today+timedelta(days=8))
        self.assert_error('medication_contraindicated', save_action, self.doctor, self.patient, replacement, kind='prescription')
        stop = {'key': uuid.uuid4(), 'expected_version': 1, 'reason': 'Тестовая отмена', 'confirm': True}
        save_action(self.doctor, self.patient, stop, kind='stop', stop_id=med_id)
        self.assertEqual(self.review('Авиандр')['level'], 'blocked')

    def test_permissions_csrf_and_no_patient_data_leak(self):
        foreign = user('medication_foreign_doctor', 'doctor', True)
        paths = [reverse(n, args=[self.patient.pk]) for n in ['care_medication_save', 'care_medication_check']]
        payload = {k: '' if v is None else v for k, v in self.record_data('Ципрофлоксацин').items()}
        for actor in [self.patient, self.other, self.editor, foreign]:
            self.client.force_login(actor)
            for path in paths:
                self.assertEqual(self.client.post(path, payload).status_code, 403)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.doctor)
        for path in paths:
            self.assertEqual(csrf.post(path, payload).status_code, 403)
            self.assertEqual(csrf.get(path).status_code, 405)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.post(paths[0], payload).status_code, 302)
        response = self.client.post(paths[1], {'name': 'Клозапин', 'start': self.today, 'end': self.today})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['selected']['level'], 'warning')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertContains(self.client.get(self.card), 'Что пациент уже принимает')
        CareRelationship.objects.filter(patient=self.patient, doctor=self.doctor).update(status='ended', ended_at=timezone.now())
        self.assertEqual(self.client.post(paths[1], {'name': 'Клозапин', 'start': self.today, 'end': self.today}).status_code, 403)

    def test_foreign_entry_id_cannot_be_attached_to_second_patient(self):
        first = self.add('Ципрофлоксацин')
        CareRelationship.objects.create(patient=self.other, doctor=self.doctor, reason='Synthetic test')
        self.assert_error('forbidden', service.save_reported, self.doctor, self.other,
            self.record_data('Ципрофлоксацин', entry_id=first.entry_id))

    def test_additional_specialist_sources_distinguish_exact_drugs_and_levels(self):
        cases = [
            ('olanzapine', 'ciprofloxacin', 'warning'), ('olanzapine', 'fluvoxamine', 'warning'),
            ('aviandr', 'ciprofloxacin', 'note'), ('aviandr', 'coc_estrogen', 'note'),
            ('quetiapine', 'clarithromycin', 'blocked'), ('quetiapine', 'erythromycin', 'blocked'),
            ('quetiapine', 'azithromycin', 'warning'), ('quetiapine', 'nefazodone', 'blocked'),
            ('simvastatin', 'clarithromycin', 'blocked'), ('simvastatin', 'nefazodone', 'blocked'),
            ('atorvastatin', 'clarithromycin', 'warning'), ('pravastatin', 'erythromycin', 'warning'),
            ('rosuvastatin', 'clarithromycin', 'note'), ('simvastatin', 'azithromycin', 'note'),
            ('sertraline', 'tramadol', 'warning'), ('sertraline', 'linezolid', 'blocked'),
            ('escitalopram', 'linezolid', 'warning'), ('rosuvastatin', 'linezolid', 'warning'),
            ('atorvastatin', 'linezolid', 'warning'),
        ]
        for first, second, level in cases:
            for candidate, other in [(first, second), (second, first)]:
                context = {'plan_version': 0, 'medicines': [{'id': 'synthetic-pair', 'revision': 1,
                    'name': catalog.BY_CODE[other]['name'], 'code': other, 'start': None, 'end': None,
                    'status': 'taking', 'origin': 'reported'}]}
                check = service.review(catalog.BY_CODE[candidate]['name'], self.today, self.today, context)
                self.assertEqual(check['level'], level, (candidate, other, check))
                self.assertTrue(all(f.get('sources') for f in check['findings']))

    def test_sertraline_directional_intervals_and_moclobemide_uncertainty(self):
        self.add('Селегилин', start=self.today-timedelta(days=30), end=self.today-timedelta(days=14), status='stopped')
        self.assertEqual(self.review('Сертралин')['level'], 'blocked')
        self.assertEqual(self.review('Сертралин', self.today+timedelta(days=1), self.today+timedelta(days=1))['level'], 'unknown')
        self.add('Сертралин', start=self.today-timedelta(days=30), end=self.today-timedelta(days=7), status='stopped')
        self.assertEqual(self.review('Моклобемид')['level'], 'blocked')
        self.assertEqual(self.review('Моклобемид', self.today+timedelta(days=1), self.today+timedelta(days=1))['level'], 'unknown')
        self.add('Моклобемид', start=self.today-timedelta(days=30), end=self.today-timedelta(days=1), status='stopped')
        check = self.review('Сертралин', self.today+timedelta(days=1), self.today+timedelta(days=1))
        self.assertEqual(check['level'], 'warning')
        self.assertTrue(any('не устанавливает' in f['detail'] for f in check['findings']))

    def test_insufficient_evidence_comment_distinguishes_searched_pair(self):
        self.add('Ламотриджин')
        check = self.review('Авиандр')
        self.assertEqual(check['level'], 'unknown')
        gap = check['uncertainty'][0]
        self.assertEqual(gap['checked_at'], '27.10.2026')
        self.assertEqual(len(gap['sources']), 3)
        self.assertIn('Недостаточно информации', gap['comment'])
        unrecognized = self.review('Неизвестный препарат')['uncertainty'][0]
        self.assertIsNone(unrecognized['checked_at'])
        self.assertEqual(unrecognized['sources'], [])

    def test_source_document_is_doctor_only(self):
        url = reverse('drug_source_document')
        self.client.force_login(self.patient)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.doctor)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertTrue(b''.join(response.streaming_content).startswith(b'PK'))
