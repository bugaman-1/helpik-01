"""Sitec's two synthetic models and separate observed process indicators.

No diagnoses, automatic clinical actions or suppression of safety alerts.
Completed results are immutable Assessment snapshots. Historical assessments
without a snapshot may be computed for display without writing on GET.
"""
import json
import math
import uuid
from functools import lru_cache
from pathlib import Path

from django.db import transaction
from quiz.models import Assessment
from . import paradata
from .sitec_inference import probability

SCHEMA = 'sitec-v1'
NAMESPACE = uuid.UUID('92d0a315-770e-4520-b272-899e67244e41')
BAD_TIMING = {'sequence_gap', 'nonmonotonic_clock', 'multiple_streams_no_cross_stream_sum',
              'missing_interval_start', 'unclosed_visible_interval', 'open_interval',
              'telemetry_disabled', 'no_events'}
QUESTION_CODES = [f'BDI{i:02}' for i in range(1, 6)] + [f'STAI{i:02}' for i in range(1, 21)]


@lru_cache(maxsize=1)
def bundle():
    data = json.loads((Path(__file__).resolve().parents[1] / 'ml/sitec-v1.json').read_text(encoding='utf-8'))
    if data['schema'] != 'sitec-logistic-v1':
        raise ValueError('Unsupported sitec model format')
    return data


def applies(run):
    from .checkin import CODE
    return bool(run and run.questionnaire_version_id and run.questionnaire_version.questionnaire.code == CODE)


def extract(run, data):
    from .questionnaires import current_answers
    from .response_quality import value
    definition = run.questionnaire_version.content.definition
    expected = {q['code']: (q['prompt'], q['options']) for q in data['questionnaire']}
    actual = {q['code']: (q['text'], [o['label'] for o in q['options']]) for q in definition['questions']}
    if expected != actual:
        return None, {}, 'Версия вопросов отличается от версии модели.'
    answers = current_answers(run)
    record = {code: value(answers, code) for code in QUESTION_CODES}
    control, paired = value(answers, 'CHECK01'), value(answers, 'STAI03')
    events = list(run.interactions.order_by('stream_id', 'sequence'))
    summary = paradata.summarize(events, enabled=run.telemetry_enabled)
    changes, previous = 0, {}
    for revision in run.revisions.order_by('received_at', 'pk'):
        new = (revision.state, revision.value)
        if revision.question_code in previous and previous[revision.question_code] != new:
            changes += 1
        previous[revision.question_code] = new
    observations = {
        'changes': max(changes, sum(e.kind == 'answer_changed' for e in events)),
        'revisits': sum(e.kind == 'question_revisited' for e in events),
        'pauses': sum(e.kind == 'visibility_hidden' for e in events),
        'control_difference': abs(control - paired) if control is not None and paired is not None else None,
        'quality_flags': summary['quality_flags'],
    }
    if run.status != 'submitted':
        return None, observations, 'Расчёт появится после завершения опроса.'
    if None in record.values() or control is None:
        return None, observations, 'Недостаточно ответов для модели.'
    timed = {q['question_code']: q for q in summary['questions']}
    codes = QUESTION_CODES + ['CHECK01']
    if (BAD_TIMING.intersection(summary['quality_flags']) or any(
            code not in timed or timed[code]['first_choice_ms'] is None or timed[code]['visible_ms'] is None
            for code in codes)):
        return None, observations, 'Недостаточно непрерывных замеров времени. Ответы и баллы сохранены.'
    # The training generator defines duration = avg_sec_per_answer * 26,
    # including CHECK01. Only observed active time is used, never wall time.
    duration = sum(timed[code]['visible_ms'] for code in codes) / 1000
    answers_list = [record[code] for code in QUESTION_CODES]
    adjacent = sum(a == b for a, b in zip(answers_list, answers_list[1:]))
    frequencies = [answers_list.count(i) / 25 for i in range(4)]
    record.update(duration_sec=duration, avg_sec_per_answer=duration / 26,
                  back_navigation_count=observations['revisits'], consistent_answers_count=adjacent,
                  option_entropy=-sum(p * math.log(p) for p in frequencies if p) / math.log(4),
                  unique_answer_count=len(set(answers_list)), adjacent_repeat_count=adjacent,
                  speed_out_of_range=int(duration / 26 < 1.5 or duration / 26 > 20))
    observations.update(duration_seconds=round(duration, 1), average_seconds=round(duration / 26, 2))
    return record, observations, ''


def calculate(run):
    if not applies(run):
        return None, {}
    try:
        data = bundle()
        record, observations, reason = extract(run, data)
        result = {'status': 'insufficient_data' if record is None else 'computed',
                  'version': data['version'], 'observations': observations, 'reason': reason}
        snapshot = {'features': record, 'observations': observations,
                    'questionnaire_version_id': run.questionnaire_version_id,
                    'answer_generation': run.answer_generation, 'sources': data['sources']}
        if record is None:
            return result, snapshot
        attention = data['models']['baseline']
        reliability = data['models']['reliability']
        p_attention, p_unreliable = probability(attention, record), probability(reliability, record)
        needs_attention = bool(record['speed_out_of_range'] or p_attention >= attention['threshold'])
        is_unreliable = p_unreliable >= reliability['threshold']
        control_flag = observations['control_difference'] >= 2
        result.update(attention_probability=p_attention, reliability_probability=p_unreliable,
                      needs_attention=needs_attention, is_unreliable=is_unreliable,
                      attention_threshold=attention['threshold'], reliability_threshold=reliability['threshold'],
                      speed_alert=bool(record['speed_out_of_range']), control_flag=control_flag,
                      review_recommended=needs_attention or is_unreliable or control_flag)
        return result, snapshot
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        # A broken/missing model must never prevent saving a patient's answers.
        return {'status': 'unavailable', 'reason': 'Модель временно недоступна. Ответы и баллы сохранены.'}, {}


@transaction.atomic
def record_completed(run):
    if not applies(run) or run.status != 'submitted':
        return None
    existing = Assessment.objects.filter(session=run, schema_version=SCHEMA).first()
    if existing:
        return existing
    result, snapshot = calculate(run)
    assessment, _ = Assessment.objects.get_or_create(
        idempotency_key=uuid.uuid5(NAMESPACE, str(run.pk)),
        defaults={'patient_id': run.patient_id, 'session': run, 'schema_version': SCHEMA,
                  'algorithm_version': f"sitec-{result.get('version', 'unavailable')}",
                  'input_snapshot': snapshot, 'result': result, 'status': result['status']})
    return assessment


def describe(run):
    if not applies(run):
        return None
    saved = Assessment.objects.filter(session=run, schema_version=SCHEMA).order_by('created_at').first()
    result = saved.result if saved else calculate(run)[0]
    presentation = {**result, 'saved': bool(saved), 'computed': result['status'] == 'computed'}
    if presentation['computed']:
        # The second model predicts an ANOMALY, so higher reliability is its
        # complement. Preserve both original outputs in the immutable snapshot.
        presentation.update(attention_percent=100 * result['attention_probability'],
                            trust_probability=1 - result['reliability_probability'],
                            trust_percent=100 * (1 - result['reliability_probability']))
    return presentation
