"""Small explicit screening rules. No automatic clinical actions."""
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from quiz.models import Alert, CareRelationship, SurveySession
from .access import require_patient_access
from .contracts import DomainError

RULE_VERSION = "screening-alerts-v1"


def doctors_for(patient_id):
    return CareRelationship.objects.filter(patient_id=patient_id, status="active",
        doctor__is_active=True, doctor__profile__verification_status="approved").select_related("doctor").order_by("-is_primary", "pk")


def active_for(patient_id):
    return Alert.objects.filter(patient_id=patient_id).exclude(status="closed").order_by("-created_at", "-pk")


def card_groups(rows):
    """Summarise repeated signals without changing their status or evidence."""
    priority = {'urgent': 0, 'high': 1, 'review': 2}
    ordered = sorted(rows, key=lambda row: (
        priority.get(row.evidence_snapshot.get('priority'), 3), -row.created_at.timestamp(), -row.pk))
    groups = {}
    for row in ordered:
        data = row.evidence_snapshot
        group_key = data.get('rule') or data.get('title') or str(row.pk)
        if group_key not in groups:
            groups[group_key] = {'alert': row, 'count': 0, 'priority': data.get('priority', 'review')}
        groups[group_key]['count'] += 1
    return list(groups.values())


@transaction.atomic
def evaluate(run):
    from .screening import results
    scores = results(run)
    if scores is None:
        return []
    relationships = list(doctors_for(run.patient_id))
    if not relationships:
        return []
    rules = []
    is_checkin = scores.get('kind') == 'checkin'
    if is_checkin:
        from .checkin import change_from_baseline
        if scores['safety']:
            urgent = scores['safety_value'] >= 2
            rules.append(('safety', 'urgent' if urgent else 'review', 'Нужна проверка безопасности',
                'Отмечено желание причинить себе вред. Не откладывайте контакт с пациентом и очную оценку безопасности.' if urgent else
                'Отмечались мысли о самоубийстве без намерения действовать. Свяжитесь с пациентом и уточните безопасность.'))
        if scores['complete']:
            change = change_from_baseline(run, scores['trend_score'])
            if scores['anxiety'] >= 46:
                rules.append(('anxiety', 'high', 'Высокая тревога', f'Текущая тревога: {scores["anxiety"]}/80. Уточните состояние и план наблюдения.'))
            if change['delta'] is not None and change['delta'] >= 4:
                rules.append(('change', 'high', 'Самочувствие заметно ухудшилось',
                    f'Четыре пункта самочувствия: {scores["trend_score"]}/12, на {change["delta"]:g} выше личной базы {change["baseline"]:g}. Нужна оценка врача.'))
    elif scores["safety"]:
        rules.append(("safety", "urgent", "Нужна проверка безопасности", "Отмечались мысли о смерти или самоповреждении. Свяжитесь с пациентом и уточните его текущее состояние."))
    if not is_checkin and scores["phq"] is not None and scores["gad"] is not None and max(scores["phq"], scores["gad"]) >= 10:
        priority = "high" if max(scores["phq"], scores["gad"]) >= 15 else "review"
        rules.append(("scores", priority, "Нужна оценка врача", f'PHQ-9: {scores["phq"]}/27; GAD-7: {scores["gad"]}/21. Обсудите симптомы и необходимость консультации.'))
    created = []
    for rule, priority, title, message in rules:
        safety_code = 'BDI04' if is_checkin else 'PHQ09'
        evidence = run.revisions.filter(question_code=safety_code, state="known", value__in=[f'{safety_code}.{i}' for i in range(1, 4)]).order_by("-pk").first() if rule == "safety" else None
        alert, new = Alert.objects.get_or_create(episode_key=f"{RULE_VERSION}:{run.pk}:{rule}", defaults={
            "patient_id": run.patient_id, "responsible": relationships[0].doctor, "evidence": evidence,
            "evidence_snapshot": {"rule_version": RULE_VERSION, "rule": rule, "session_id": str(run.pk),
                "priority": priority, "title": title, "message": message, "phq": scores.get("phq"), "gad": scores.get("gad"),
                "depression": scores.get('depression'), "anxiety": scores.get('anxiety'),
                "wording": "adapted-prototype"}})
        if not new and rule == 'safety' and priority == 'urgent' and alert.evidence_snapshot.get('priority') != 'urgent':
            alert.evidence_snapshot = {**alert.evidence_snapshot, 'priority': priority, 'message': message}
            alert.evidence = evidence
            alert.status = 'open'
            alert.save(update_fields=['evidence_snapshot', 'evidence', 'status'])
            new = True
        # A new positive response after the doctor's review is a new event,
        # while retries and later corrections to zero never duplicate alerts.
        review_at = parse_datetime(alert.evidence_snapshot.get("review", {}).get("at", ""))
        if not new and rule == "safety" and alert.status != "open" and review_at and evidence.received_at > review_at:
            alert.status = "open"
            alert.evidence = evidence
            alert.evidence_snapshot = {**alert.evidence_snapshot, "reopened_at": timezone.now().isoformat()}
            alert.save(update_fields=["status", "evidence", "evidence_snapshot"])
            new = True
        if new:
            created.append(alert)
            from .notifications import deliver_alert
            transaction.on_commit(lambda alert_id=alert.pk: deliver_alert(alert_id), robust=True)
    return created


def sync_patient(patient_id):
    from .checkin import CODE
    # Used on a newly established relationship and by an explicit rollout command.
    for run in SurveySession.objects.filter(patient_id=patient_id, questionnaire_version__questionnaire__code__in=[CODE, "phq9-gad7-adapted"]).select_related("questionnaire_version__questionnaire").order_by("-started_at")[:30]:
        evaluate(run)


@transaction.atomic
def resolve(actor, alert_id, status, note=""):
    alert = Alert.objects.select_for_update().get(pk=alert_id)
    require_patient_access(actor, alert.patient_id, clinical_write=True)
    if status not in {"acknowledged", "closed"} or alert.status == "closed":
        raise DomainError("invalid_input")
    if status == "closed" and (not note.strip() or len(note) > 500):
        raise DomainError("reason_required")
    alert.status = status
    alert.evidence_snapshot = {**alert.evidence_snapshot, "review": {"by": actor.pk, "at": timezone.now().isoformat(), "note": note.strip()[:500]}}
    alert.save(update_fields=["status", "evidence_snapshot"])
    return alert
