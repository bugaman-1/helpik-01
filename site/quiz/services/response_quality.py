"""Exploratory process indicators, not a lie detector or a validated scale."""
from statistics import median
from math import ceil
from quiz.models import SurveySession
from . import paradata
from .questionnaires import current_answers
from .screening import CONTROL_CODE, CONTROL_PAIR, CODE

MIN_TIMED = 8
MIN_HISTORY = 3
DEFAULT_FAST_SECONDS = 1.5
DEFAULT_SLOW_SECONDS = 30
BAD_CLOCK_FLAGS = {"sequence_gap", "nonmonotonic_clock", "multiple_streams_no_cross_stream_sum"}


def timing(run):
    events = list(run.interactions.order_by("stream_id", "sequence"))
    summary = paradata.summarize(events, enabled=run.telemetry_enabled)
    usable = run.telemetry_enabled and not BAD_CLOCK_FLAGS.intersection(summary["quality_flags"])
    times = [row["first_choice_ms"] / 1000 for row in summary["questions"]
             if row["first_choice_ms"] is not None and row["question_code"] != CONTROL_CODE] if usable else []
    return {"median": median(times) if len(times) >= MIN_TIMED else None,
            "timed_count": len(times), "times": times, "events": events, "summary": summary}


def value(answers, code):
    answer = answers.get(code)
    if answer and answer.state == "known" and answer.value in {f"{code}.{i}" for i in range(4)}:
        return int(answer.value[-1])
    return None


def trust_scale(quality, *, complete, question_count):
    """Ordinal review indicator; never invent an adjusted ML probability.

    Model anomaly, speed, control discrepancy, frequent edits/navigation are
    separate signals. Referral/attention probability never lowers trust.
    """
    model = quality['model_result']
    computed = bool(model and model['computed'])
    observations = model.get('observations', {}) if computed else {}
    seconds = observations.get('average_seconds') if computed else quality['median_seconds']
    # Use the original speed decision, not the rounded displayed average.
    speed_alert = model['speed_alert'] if computed else quality['tempo_level'] == 'notice'
    difference = observations.get('control_difference', quality['difference'])
    changes = observations.get('changes', quality['changes'])
    revisits = observations.get('revisits', quality['revisits'])
    frequent_threshold = max(3, ceil(question_count * .2))
    frequent = changes >= frequent_threshold or revisits >= frequent_threshold
    control_flag = difference is not None and difference >= 2
    pattern_flag = computed and model['is_unreliable']
    signals = []
    if pattern_flag:
        signals.append({'code': 'pattern', 'title': 'Необычный паттерн ответов',
                        'description': 'Модель отметила особенности заполнения. Уточните ответы при разговоре.'})
    if speed_alert:
        fast = seconds is not None and (seconds <= 1.5 if computed else seconds < quality['fast_seconds'])
        signals.append({'code': 'speed', 'title': 'Очень быстрое прохождение' if fast else 'Длительное прохождение',
                        'description': 'Возможны поспешный выбор или невнимательное чтение.' if fast else
                            'Возможны обдумывание, отвлечения или затруднения. По времени нельзя установить неискренность.'})
    if control_flag:
        signals.append({'code': 'control', 'title': 'Расхождение контрольных ответов',
                        'description': 'Близкие вопросы различаются на две или три позиции. Уточните понимание формулировок.'})
    if frequent:
        signals.append({'code': 'revisions', 'title': 'Частые возвраты или исправления',
                        'description': 'Много повторных просмотров или замен ответа. Уточните, что вызывало сомнения.'})
    enough = complete and (computed if model else quality['timing_available'] and difference is not None)
    if not enough:
        level, label = 'incomplete', 'Недостаточно данных'
        summary = model['reason'] if model and model.get('reason') else 'Для оценки нужны замеры времени и контрольная пара завершённого опроса.'
    elif pattern_flag or control_flag or len(signals) >= 2:
        level, label = 'review', 'Низкое доверие'
        summary = 'Есть основания проверить ответы перед интерпретацией результата.'
    elif signals:
        level, label = 'notice', 'Доверие снижено'
        summary = 'Есть особенности прохождения, которые стоит уточнить.'
    else:
        level, label = 'ordinary', 'Без выраженных сомнений'
        summary = 'По доступным данным выраженных особенностей прохождения не обнаружено.'
    return {'method': 'response-trust-v1', 'level': level, 'label': label, 'summary': summary,
            'signals': signals, 'frequent_threshold': frequent_threshold, 'changes': changes, 'revisits': revisits,
            'difference': difference, 'seconds': seconds, 'average': computed}


def describe(run):
    from . import checkin
    if not run or run.questionnaire_version.questionnaire.code not in {CODE, checkin.CODE}:
        return None
    data = timing(run)
    answers = current_answers(run)
    pair = checkin.CONTROL_PAIR if run.questionnaire_version.questionnaire.code == checkin.CODE else CONTROL_PAIR
    a, b = value(answers, pair), value(answers, CONTROL_CODE)
    difference = abs(a - b) if a is not None and b is not None else None
    history = []
    if data["median"] is not None:
        candidates = SurveySession.objects.filter(patient_id=run.patient_id,
            questionnaire_version_id=run.questionnaire_version_id, status="submitted",
            completed_at__lt=run.started_at).exclude(pk=run.pk).order_by("-completed_at")[:5]
        for previous in candidates:
            old = timing(previous)["median"]
            if old is not None:
                history.append(old)
    baseline = median(history) if len(history) >= MIN_HISTORY else None
    fast = max(0.5, baseline * 0.4) if baseline is not None else DEFAULT_FAST_SECONDS
    slow = max(30, baseline * 2.5) if baseline is not None else DEFAULT_SLOW_SECONDS
    markers, notes = [], []
    if data["median"] is not None:
        if data["median"] < fast:
            markers.append("Быстрый выбор ответов")
            notes.append("Темп заметно быстрее личной истории или пробной границы. Возможны знакомство с вопросами и поспешное заполнение.")
        elif data["median"] > slow:
            markers.append("Длительный выбор ответов")
            notes.append("Темп заметно медленнее. Причиной могут быть обдумывание, затруднения чтения или самочувствие; намерение угодить врачу из этого не следует.")
    if difference is not None and difference >= 2:
        markers.append("Расхождение близких вопросов")
        notes.append("Близкие вопросы различаются на две или три позиции. Уточните понимание вопроса.")
    changes = sum(e.kind == "answer_changed" for e in data["events"])
    saved_changes, previous_values = 0, {}
    for revision in run.revisions.order_by("received_at", "pk"):
        new = (revision.state, revision.value)
        if revision.question_code in previous_values and previous_values[revision.question_code] != new:
            saved_changes += 1
        previous_values[revision.question_code] = new
    changes = max(changes, saved_changes)
    revisits = sum(e.kind == "question_revisited" for e in data["events"])
    hidden = sum(e.kind == "visibility_hidden" for e in data["events"])
    if changes or revisits:
        notes.append("Возвраты и исправления показаны как наблюдения. Изменение ответа само по себе не снижает качество и не доказывает ложь.")
    enough = data["median"] is not None and difference is not None and run.status == "submitted"
    count = len(markers)
    from .sitec import describe as model_result
    quality = {"method": "response-quality-v1", "markers": markers, "notes": notes,
        "model_result": model_result(run),
        "level": "incomplete" if not enough else "review" if count >= 2 else "notice" if count else "ordinary",
        "label": "Мало данных" if not enough else "Обсудить ответы" if count >= 2 else "Есть особенности" if count else "Без особенностей",
        "tempo_label": "Нет замера" if data['median'] is None else "Быстрее обычного" if data['median'] < fast else "Медленнее обычного" if data['median'] > slow else "Ровный темп",
        "tempo_level": "incomplete" if data['median'] is None else "notice" if data['median'] < fast or data['median'] > slow else "ordinary",
        "consistency_label": "Нет пары" if difference is None else "Ответы близки" if difference < 2 else "Есть расхождение",
        "consistency_level": "incomplete" if difference is None else "notice" if difference >= 2 else "ordinary",
        "marker_count": count, "median_seconds": round(data["median"], 1) if data["median"] is not None else None,
        "timed_count": data["timed_count"], "changes": changes, "revisits": revisits, "hidden": hidden,
        "difference": difference, "control_present": CONTROL_CODE in {q["code"] for q in run.questionnaire_version.content.definition["questions"]},
        "baseline_seconds": round(baseline, 1) if baseline is not None else None,
        "baseline_count": len(history), "fast_seconds": round(fast, 1), "slow_seconds": round(slow, 1),
        "timing_available": data["median"] is not None, "telemetry_enabled": run.telemetry_enabled,
        "quality_flags": data["summary"]["quality_flags"]}
    quality['trust'] = trust_scale(quality, complete=run.status == 'submitted',
                                   question_count=len(run.questionnaire_version.content.definition['questions']))
    return quality
