"""One descriptive, question-based projection for patients and their doctors."""
import calendar
from collections import Counter
from datetime import date, timedelta
from urllib.parse import urlencode
from django.urls import reverse
from django.utils import timezone
from quiz.models import SurveySession
from quiz import ui_demo


def selected_date(request):
    try:
        value = date.fromisoformat(request.GET.get("date", ""))
        return value if 1901 <= value.year <= 2098 else timezone.localdate()
    except ValueError:
        return timezone.localdate()


def real_entries(patient, request):
    selected = selected_date(request)
    first = selected.replace(day=1)-timedelta(days=7)
    last = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])+timedelta(days=7)
    sessions = SurveySession.objects.filter(patient=patient, questionnaire_version__isnull=False,
        started_at__date__range=(first, last)).select_related("questionnaire_version__questionnaire", "questionnaire_version__content").prefetch_related("revisions")
    result = []
    labels = {"draft": "Не начат", "in_progress": "Заполняется", "submitted": "Завершён", "abandoned": "Прекращён"}
    for run in sessions:
        answers = {}
        for answer in sorted(run.revisions.all(), key=lambda a: a.revision):
            answers[answer.question_code] = answer
        values, parameters, states, raw = {}, {}, {}, []
        for q in run.questionnaire_version.content.definition["questions"]:
            key = f"{run.questionnaire_version_id}:{q['code']}"
            title = q["text"]
            answer = answers.get(q["code"])
            state = answer.state if answer else "missing"
            value = next((o["label"] for o in q["options"] if answer and o["code"] == answer.value), None) if state == "known" else None
            label = value or {"not_applicable": "Не применимо", "missing": "Нет ответа"}.get(state, "Нет данных")
            parameters[key], values[key], states[key] = title, label, state
            raw.append({"question": q["text"], "label": label, "revision": answer.revision if answer else None})
        at = timezone.localtime(run.started_at)
        from .screening import results
        scores = results(run)
        diary = None
        if run.status == "submitted" and run.questionnaire_version.questionnaire.code == "author-diary":
            diary = {label: next((row["label"] for row, q in zip(raw, run.questionnaire_version.content.definition["questions"]) if q["code"] == code), "Нет ответа") for label, code in [("Подавленность", "D02"), ("Напряжение", "D04"), ("Сон", "D07")]}
        result.append({"scores": scores if scores and (scores.get('complete') or scores.get('phq') is not None) else None, "diary": diary,
            "version_id": run.questionnaire_version_id,
            "id": str(run.pk), "day": at.date(), "time": at.strftime("%H:%M:%S"), "status": labels.get(run.status, run.status),
            "title": run.questionnaire_version.questionnaire.title, "detail_url": reverse("survey_detail", args=[run.pk]),
            "values": values, "parameters": parameters, "states": states, "raw": raw})
    for record in patient.survey_records.filter(created_at__date__range=(first, last)):
        at = timezone.localtime(record.created_at)
        result.append({"id": f"legacy-{record.pk}", "day": at.date(), "time": at.strftime("%H:%M:%S"), "status": "Архивная запись",
            "title": "Прежний опрос", "legacy": True,
            "legacy_values": f"Положительных: {record.positive_count} · нейтральных: {record.neutral_count} · отрицательных: {record.negative_count}"})
    return sorted(result, key=lambda e: (e["day"], e["time"]), reverse=True)


def calendar_context(request, entries, base_url, demo=False):
    today, selected = timezone.localdate(), selected_date(request)
    period = request.GET.get("period", "month")
    if period not in {"month", "week", "day", "session"}:
        period = "month"
    parameters = dict(ui_demo.PARAMETERS) if demo else {}
    for entry in entries:
        parameters.update(entry.get("parameters", {}))
    parameter = request.GET.get("parameter")
    if parameter not in parameters:
        parameter = next(iter(parameters), "")
    def link(**changes):
        data = {k: request.GET[k] for k in ("state", "patient", "entry") if k in request.GET}
        data.update(period=period, date=selected.isoformat(), parameter=parameter)
        data.update(changes)
        return base_url + "?" + urlencode(data) + "#dynamics-calendar"
    if period == "month":
        first = selected.replace(day=1)
        last = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
        grid_first = first-timedelta(days=first.weekday())
        grid_last = last+timedelta(days=6-last.weekday())
        days = [grid_first+timedelta(days=i) for i in range((grid_last-grid_first).days+1)]
        previous, following = first-timedelta(days=1), last+timedelta(days=1)
        heading = selected.strftime("%m.%Y")
    elif period == "week":
        first = selected-timedelta(days=selected.weekday())
        last = first+timedelta(days=6)
        days = [first+timedelta(days=i) for i in range(7)]
        previous, following = selected-timedelta(days=7), selected+timedelta(days=7)
        heading = f"{first:%d.%m} — {last:%d.%m.%Y}"
    else:
        first = last = selected
        days = [selected]
        previous, following = selected-timedelta(days=1), selected+timedelta(days=1)
        heading = f"{selected:%d.%m.%Y}"
    visible = [e for e in entries if first <= e["day"] <= last]
    chosen = next((e for e in visible if str(e["id"]) == request.GET.get("entry")), None)
    if period == "session":
        chosen = chosen or next(iter(visible), None)
        visible = [chosen] if chosen else []
    for entry in visible:
        entry["label"] = entry.get("values", {}).get(parameter, "Этот вопрос не задавался")
        entry["url"] = link(period="session", date=entry["day"].isoformat(), entry=entry["id"])
    relevant = [e for e in visible if parameter in e.get("states", {})]
    known = [e for e in relevant if e["states"][parameter] == "known"]
    counts = Counter(e["values"][parameter] for e in known)
    recorded = {e["day"] for e in entries}
    return {"score_charts": score_charts(visible), "period": period, "parameter": parameter, "parameter_label": parameters.get(parameter, "Вопросов пока нет"),
        "parameter_options": [{"code": code, "title": title} for code, title in parameters.items()], "selected_date": selected,
        "period_links": [{"title": title, "active": period == code, "url": link(period=code)} for code, title in [("month", "Месяц"), ("week", "Неделя"), ("day", "День"), ("session", "Запись")]],
        "parameter_links": [{"title": title, "active": parameter == code, "url": link(parameter=code)} for code, title in parameters.items()],
        "calendar_heading": heading, "previous_url": link(date=previous.isoformat()), "next_url": link(date=following.isoformat()),
        "calendar_days": [{"number": d.day, "url": link(period="day", date=d.isoformat()), "has_entry": d in recorded,
            "muted": d.month != selected.month, "today": d == today, "label": f"{d:%d.%m.%Y}: " + ("есть записи" if d in recorded else "нет записей")} for d in days],
        "entries": visible, "score_entries": [e for e in reversed(visible) if e.get("scores") or e.get("diary")], "selected_entry": chosen, "weekdays": ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"], "data_demo": demo,
        "observation_count": len(known), "observation_days": len({e["day"] for e in known}),
        "missing_count": sum(e["states"][parameter] == "missing" for e in relevant),
        "not_applicable_count": sum(e["states"][parameter] == "not_applicable" for e in relevant),
        "not_asked_count": len([e for e in visible if not e.get("legacy")])-len(relevant),
        "frequencies": [{"label": label, "count": n} for label, n in counts.items()]}


def score_charts(entries):
    """Separate charts by instrument version. Position observations by real time."""
    daily = [e for e in reversed(entries) if (e.get('scores') or {}).get('kind') == 'checkin']
    charts = []
    for version in dict.fromkeys(e['version_id'] for e in daily):
        rows = [e for e in daily if e['version_id'] == version]
        moments = [e['day'].toordinal() + sum(int(v) * k for v, k in zip(e['time'].split(':'), [3600, 60, 1])) / 86400 for e in rows]
        span = moments[-1] - moments[0]
        for key, title, low, high, color in [('depression', 'Самочувствие', 0, 15, 'mood'), ('anxiety', 'Текущая тревога', 20, 80, 'anxiety')]:
            points = [{'x': round(26 + (moment - moments[0]) / span * 268, 1) if span else 160,
                'y': round(94 - (e['scores'][key] - low) / (high - low) * 72, 1),
                'label_y': round(84 - (e['scores'][key] - low) / (high - low) * 72, 1),
                'value': e['scores'][key], 'date': e['day'].strftime('%d.%m')} for e, moment in zip(rows, moments)]
            charts.append({'title': title, 'key': key, 'min': low, 'max': high, 'color': color,
                'points': points, 'line': ' '.join(f"{p['x']},{p['y']}" for p in points),
                'latest': points[-1]['value'], 'count': len(points), 'start': points[0]['date'], 'end': points[-1]['date'],
                'description': '; '.join(f"{p['date']}: {p['value']}" for p in points)})
    return charts
