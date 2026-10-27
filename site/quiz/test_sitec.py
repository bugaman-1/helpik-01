"""Real exported-model parity, telemetry boundaries, snapshots and access tests."""
import copy
import uuid
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from quiz.models import Assessment, InteractionEvent
from quiz.services import checkin, screening, sitec, response_quality, questionnaires as engine
from quiz.services.sitec_inference import probability
from quiz.test_b03 import setup_case, cmd


class ExportedModelTests(SimpleTestCase):
    def test_predictions_match_original_sklearn_fixtures(self):
        data = sitec.bundle()
        for fixture in data['fixtures']:
            with self.subTest(model=fixture['model'], input=fixture['input']):
                self.assertAlmostEqual(probability(data['models'][fixture['model']], fixture['input']),
                                       fixture['probability'], places=12)
        for validation in data['validation'].values():
            self.assertGreaterEqual(validation['rows'], 10000)
            self.assertTrue(validation['identical_classes'])
            self.assertLess(validation['max_absolute_error'], 1e-12)


@override_settings(DEMO_SURVEY_ENABLED=True)
class SitecIntegrationTests(TestCase):
    def setUp(self):
        setup_case(self)
        self.checkin = checkin.start(self.patient)

    def answers(self, control=0):
        for i, q in enumerate(checkin.definition()['questions']):
            code = q['code']
            option = control if code == 'CHECK01' else 0 if code == 'BDI04' else i % 4
            engine.save_answer(self.patient, cmd(self.checkin, code, f'{code}.{option}'))
        self.checkin.refresh_from_db()

    def events(self, seconds=5, pause=False, revisit=False):
        stream, rows, at = uuid.uuid4(), [], 1000
        def add(code, kind, elapsed):
            rows.append(InteractionEvent(session=self.checkin, stream_id=stream, sequence=len(rows) + 1,
                        question_code=code, kind=kind, elapsed_ms=round(elapsed), client_at=timezone.now(), received_at=timezone.now()))
        for q in checkin.definition()['questions']:
            code = q['code']
            add(code, 'question_visible', at)
            if pause:
                add(code, 'visibility_hidden', at + 1000)
                add(code, 'visibility_visible', at + 61000)
                at += 60000
            add(code, 'option_selected', at + seconds * 1000)
            add(code, 'question_left', at + seconds * 1000 + 200)
            at += seconds * 1000 + 500
        if revisit:
            add('STAI03', 'question_revisited', at)
            add('STAI03', 'answer_changed', at + 1000)
            add('STAI03', 'question_left', at + 1200)
        return rows

    def finish(self):
        self.checkin.refresh_from_db()
        engine.finish(self.patient, self.checkin.pk, uuid.uuid4(), self.checkin.answer_generation)
        self.checkin.refresh_from_db()
        return sitec.describe(self.checkin)

    def test_completion_stores_model_features_control_and_observations(self):
        self.answers(control=3)
        engine.save_answer(self.patient, cmd(self.checkin, 'STAI03', 'STAI03.0', expected=1))
        InteractionEvent.objects.bulk_create(self.events(pause=True, revisit=True))
        result = self.finish()
        self.assertTrue(result['computed'])
        self.assertTrue(result['saved'])
        self.assertTrue(result['control_flag'])
        saved = Assessment.objects.get(session=self.checkin)
        features = saved.input_snapshot['features']
        self.assertAlmostEqual(features['duration_sec'], 26 * 5.2 + 1.2)
        self.assertAlmostEqual(features['avg_sec_per_answer'], (26 * 5.2 + 1.2) / 26)
        self.assertEqual(features['back_navigation_count'], 1)
        self.assertNotIn('CHECK01', features)
        self.assertNotIn('answer_changes_count', features)
        self.assertEqual(result['observations']['changes'], 1)
        self.assertEqual(result['observations']['pauses'], 26)
        for name, field in [('baseline', 'attention_probability'), ('reliability', 'reliability_probability')]:
            self.assertAlmostEqual(result[field], probability(sitec.bundle()['models'][name], features))
        # Neither predictions nor quality flags create prescriptions or actions.
        self.assertFalse(self.patient.clinical_actions.exists())

    def test_retry_and_late_events_preserve_original_snapshot(self):
        self.answers()
        InteractionEvent.objects.bulk_create(self.events())
        first = self.finish()
        InteractionEvent.objects.create(session=self.checkin, stream_id=uuid.uuid4(), sequence=1,
            kind='page_resume', question_code='', elapsed_ms=1, client_at=timezone.now())
        self.assertEqual(self.finish(), first)
        self.assertEqual(Assessment.objects.filter(session=self.checkin).count(), 1)

    def test_no_telemetry_does_not_mean_normal_or_anomalous(self):
        self.answers()
        result = self.finish()
        self.assertEqual(result['status'], 'insufficient_data')
        self.assertNotIn('needs_attention', result)
        self.assertNotIn('is_unreliable', result)

    def test_disabled_collection_and_missing_final_interval_block_prediction(self):
        self.answers()
        rows = self.events()
        InteractionEvent.objects.bulk_create(rows[:-1])
        self.assertEqual(self.finish()['status'], 'insufficient_data')
        with patch.object(self.checkin, 'telemetry_enabled', False):
            self.assertEqual(sitec.calculate(self.checkin)[0]['status'], 'insufficient_data')

    def test_stream_gaps_and_clock_issues_block_prediction(self):
        self.answers()
        cases = ('gap', 'backwards_clock', 'second_stream')
        for case in cases:
            rows = self.events()
            if case == 'gap':
                rows = rows[:5] + rows[6:]
            elif case == 'backwards_clock':
                rows[5].elapsed_ms = 0
            else:
                rows[-1].stream_id = uuid.uuid4()
                rows[-1].sequence = 1
            # Exercise extraction on the complete input without writing snapshots.
            with self.subTest(case=case), patch.object(self.checkin, 'status', 'submitted'), \
                    patch.object(self.checkin.interactions.__class__, 'order_by', return_value=rows):
                result = sitec.calculate(self.checkin)[0]
                self.assertEqual(result['status'], 'insufficient_data')

    def test_speed_rule_uses_exact_open_boundaries(self):
        self.answers()
        for average, flag in [(1.499, True), (1.5, False), (20, False), (20.001, True)]:
            rows = self.events(seconds=average - .2)
            with self.subTest(average=average), patch.object(self.checkin, 'status', 'submitted'), \
                    patch.object(self.checkin.interactions.__class__, 'order_by', return_value=rows):
                result = sitec.calculate(self.checkin)[0]
                self.assertEqual(result['status'], 'computed')
                self.assertEqual(result['speed_alert'], flag)
                if flag:
                    self.assertTrue(result['needs_attention'])

    def test_control_is_separate_from_the_two_model_inputs(self):
        self.answers(control=0)
        InteractionEvent.objects.bulk_create(self.events())
        with patch.object(self.checkin, 'status', 'submitted'):
            original, inputs = sitec.calculate(self.checkin)
        engine.save_answer(self.patient, cmd(self.checkin, 'CHECK01', 'CHECK01.3', expected=1))
        with patch.object(self.checkin, 'status', 'submitted'):
            changed, changed_inputs = sitec.calculate(self.checkin)
        self.assertEqual(inputs['features'], changed_inputs['features'])
        self.assertEqual(original['attention_probability'], changed['attention_probability'])
        self.assertEqual(original['reliability_probability'], changed['reliability_probability'])
        self.assertNotEqual(original['observations']['control_difference'], changed['observations']['control_difference'])

    def test_missing_model_does_not_break_completion_or_hide_safety(self):
        self.answers()
        engine.save_answer(self.patient, cmd(self.checkin, 'BDI04', 'BDI04.2', expected=1))
        with patch('quiz.services.sitec.bundle', side_effect=OSError('missing artifact')):
            result = self.finish()
        self.assertEqual(self.checkin.status, 'submitted')
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue(checkin.results(self.checkin)['safety'])
        self.assertTrue(self.patient.alerts.filter(evidence_snapshot__rule='safety').exists())

    def test_wrong_questionnaire_and_modified_schema_are_not_scored(self):
        self.assertIsNone(sitec.describe(screening.start(self.patient)))
        definition = copy.deepcopy(self.checkin.questionnaire_version.content.definition)
        definition['questions'][0]['options'][0]['label'] = 'Different meaning'
        self.checkin.questionnaire_version.content.definition = definition
        self.assertEqual(sitec.calculate(self.checkin)[0]['status'], 'insufficient_data')

    def test_model_is_shown_only_to_authorized_doctor(self):
        self.answers()
        InteractionEvent.objects.bulk_create(self.events())
        self.finish()
        path = reverse('survey_detail', args=[self.checkin.pk])
        self.client.force_login(self.patient)
        for url in (path, reverse('home')):
            patient_page = self.client.get(url)
            self.assertNotContains(patient_page, 'Доверие к прохождению')
            self.assertNotContains(patient_page, 'Оценки моделей')
            self.assertNotContains(patient_page, 'Достоверность прохождения')
            self.assertContains(patient_page, 'Результаты опроса')
            self.assertContains(patient_page, 'Сумма пяти пунктов самочувствия')
            self.assertContains(patient_page, 'Текущая тревога')
        self.client.force_login(self.doctor)
        for url in (path, reverse('patient_records', args=[self.patient.pk])):
            doctor_page = self.client.get(url)
            self.assertContains(doctor_page, 'Доверие к прохождению')
            self.assertContains(doctor_page, 'Оценки моделей')
            self.assertContains(doctor_page, 'Достоверность прохождения')
            self.assertContains(doctor_page, 'Результаты опроса')
            html = doctor_page.content.decode()
            start = html.index('<details class="trust-details">')
            end = html.index('</details>', start)
            self.assertIn('Необходимость консультации специалиста', html[start:end])
            self.assertIn('Достоверность прохождения', html[start:end])
            self.assertNotIn(' open', html[start:html.index('>', start)])
            self.assertIn('<summary>Подробнее</summary>', html[start:end])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(path).status_code, 403)

    def test_displayed_reliability_is_complement_and_snapshot_is_unchanged(self):
        self.answers()
        InteractionEvent.objects.bulk_create(self.events())
        result = self.finish()
        saved = Assessment.objects.get(session=self.checkin)
        self.assertAlmostEqual(result['trust_probability'], 1 - saved.result['reliability_probability'])
        self.assertAlmostEqual(result['trust_percent'], result['trust_probability'] * 100)
        self.assertAlmostEqual(result['attention_percent'], saved.result['attention_probability'] * 100)
        self.assertNotIn('trust_probability', saved.result)


class TrustScaleTests(SimpleTestCase):
    def quality(self, *, seconds=7, difference=0, changes=0, revisits=0, pattern=False):
        return {'model_result': {'computed': True, 'speed_alert': seconds < 1.5 or seconds > 20,
                    'is_unreliable': pattern, 'attention_probability': .99,
                    'observations': {'average_seconds': seconds, 'control_difference': difference,
                                     'changes': changes, 'revisits': revisits}},
                'median_seconds': seconds, 'tempo_level': 'ordinary', 'fast_seconds': 1.5,
                'difference': difference, 'changes': changes, 'revisits': revisits,
                'timing_available': True}

    def scale(self, **kwargs):
        return response_quality.trust_scale(self.quality(**kwargs), complete=True, question_count=26)

    def test_high_attention_probability_does_not_reduce_response_trust(self):
        self.assertEqual(self.scale()['level'], 'ordinary')

    def test_fast_and_slow_are_both_review_signals_including_rounded_boundary(self):
        for seconds, title in ((.8, 'Очень быстрое прохождение'), (35, 'Длительное прохождение')):
            with self.subTest(seconds=seconds):
                scale = self.scale(seconds=seconds)
                self.assertEqual(scale['level'], 'notice')
                self.assertEqual(scale['signals'][0]['title'], title)
        quality = self.quality(seconds=1.5)
        quality['model_result']['speed_alert'] = True  # 1.499 rounded for display
        self.assertEqual(response_quality.trust_scale(quality, complete=True, question_count=26)['signals'][0]['title'],
                         'Очень быстрое прохождение')

    def test_control_disagreement_and_model_anomaly_require_review(self):
        for difference in (2, 3):
            self.assertEqual(self.scale(difference=difference)['level'], 'review')
        self.assertEqual(self.scale(pattern=True)['level'], 'review')

    def test_frequent_navigation_and_changes_are_counted_but_one_edit_is_normal(self):
        self.assertEqual(self.scale(changes=1, revisits=1)['level'], 'ordinary')
        self.assertEqual(self.scale(changes=6)['level'], 'notice')
        self.assertEqual(self.scale(revisits=6)['level'], 'notice')
        self.assertEqual(self.scale(seconds=35, changes=6)['level'], 'review')

    def test_missing_model_data_or_unfinished_survey_never_gets_positive_trust(self):
        quality = self.quality()
        quality['model_result'] = {'computed': False, 'reason': 'Нет замера'}
        scale = response_quality.trust_scale(quality, complete=True, question_count=26)
        self.assertEqual(scale['level'], 'incomplete')
        self.assertEqual(scale['summary'], 'Нет замера')
        self.assertEqual(response_quality.trust_scale(self.quality(), complete=False, question_count=26)['level'], 'incomplete')

    def test_saved_model_observations_take_priority_over_late_events(self):
        quality = self.quality()
        quality.update(difference=3, changes=40, revisits=50)
        self.assertEqual(response_quality.trust_scale(quality, complete=True, question_count=26)['level'], 'ordinary')
