"""Reconciled medication history and a bounded, versioned interaction review."""
import json
import uuid
from datetime import date, timedelta
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac, constant_time_compare
from quiz.models import ReportedMedication, PrescriptionVersion, Profile
from .access import require_patient_access
from .contracts import DomainError
from . import drug_catalog as catalog

RANK = {"blocked": 3, "warning": 2, "note": 1, "unknown": 0}
LABELS = {"blocked": "Противопоказанное сочетание", "warning": "Требует внимания", "note": "Есть сведения в источниках", "unknown": "Недостаточно информации"}


def latest_reported(patient):
    result = {}
    for row in ReportedMedication.objects.filter(patient=patient).order_by("entry_id", "revision"):
        result[row.entry_id] = row
    return sorted(result.values(), key=lambda r: (r.created_at, r.pk), reverse=True)


@transaction.atomic
def save_reported(actor, patient, data):
    require_patient_access(actor, patient.pk, clinical_write=True)
    # Serialise corrections for this patient on every DB backend.
    Profile.objects.select_for_update().get(user=patient)
    fields = {k: data[k] for k in ("name", "regimen", "start", "end", "status", "source", "note")}
    entry_id = data["entry_id"]
    old = ReportedMedication.objects.filter(key=data["key"]).first()
    if old:
        if old.patient_id != patient.pk or old.author_id != actor.pk or old.entry_id != entry_id or old.revision != data["expected_revision"] + 1 or any(getattr(old, k) != v for k, v in fields.items()):
            raise DomainError("idempotency_conflict")
        return old
    current = ReportedMedication.objects.filter(patient=patient, entry_id=entry_id).order_by("-revision").first()
    # An entry UUID must never be moved to another patient.
    if ReportedMedication.objects.filter(entry_id=entry_id).exclude(patient=patient).exists():
        raise DomainError("forbidden")
    if (current.revision if current else 0) != data["expected_revision"]:
        raise DomainError("stale_medication")
    if fields["status"] not in {"taking", "stopped"} or (fields["status"] == "stopped" and not fields["end"]) or (fields["start"] and fields["end"] and fields["start"] > fields["end"]):
        raise DomainError("invalid_input")
    resolved = catalog.resolve(fields["name"])
    return ReportedMedication.objects.create(patient=patient, author=actor, entry_id=entry_id,
        revision=data["expected_revision"]+1, key=data["key"], catalog_code=resolved["code"] if resolved else "", **fields)


def medication_context(patient, exclude=""):
    reported = latest_reported(patient)
    plan = PrescriptionVersion.objects.filter(patient=patient).order_by("-version").first()
    result = [{"id": str(r.entry_id), "revision": r.revision, "name": r.name, "code": r.catalog_code,
        "start": r.start.isoformat() if r.start else None, "end": r.end.isoformat() if r.end else None,
        "status": r.status, "origin": "reported"} for r in reported]
    for med in plan.definition.get("medicines", []) if plan else []:
        resolved = catalog.resolve(med["name"])
        result.append({"id": med["id"], "revision": plan.version, "name": med["name"],
            "code": resolved["code"] if resolved else "", "start": med["start"], "end": med["end"],
            "status": "taking", "origin": "prescription", "editing": str(med["id"]) == str(exclude or "")})
    # Preserve removed MAOIs/sertraline for source-specific washout intervals.
    if plan:
        current_ids = {m["id"] for m in plan.definition.get("medicines", [])}
        previous = {}
        for version in PrescriptionVersion.objects.filter(patient=patient).order_by("version"):
            present = {m["id"] for m in version.definition.get("medicines", [])}
            for med_id, med in previous.items():
                if med_id not in present and med_id not in current_ids and med_id != str(exclude or ""):
                    original = catalog.resolve(med["name"])
                    if original and ("mao" in original["tags"] or original['code'] == 'sertraline') and not any(x["id"] == med_id for x in result):
                        stopped = min(date.fromisoformat(med["end"]), timezone.localdate(version.created_at))
                        result.append({"id": med_id, "revision": version.version, "name": med["name"], "code": original["code"],
                            "start": med["start"], "end": stopped.isoformat(), "status": "stopped", "origin": "prescription"})
            previous = {m["id"]: m for m in version.definition.get("medicines", [])}
    return {"medicines": result, "plan_version": plan.version if plan else 0}


def review(name, start, end, context):
    candidate = catalog.resolve(name)
    findings, unknown, uncertainty = [], [], []

    def unknown_pair(med, other):
        unknown.append(med['name'])
        gap = catalog.SEARCHED_GAPS.get(frozenset((candidate['code'], other['code']))) if candidate and other else None
        uncertainty.append({'medicine': med['name'],
            'comment': gap['comment'] if gap else ('Название одного из препаратов не распознано. Недостаточно информации для проверки.' if not candidate or not other else 'Для этой пары нет подтверждённого правила в текущем наборе. Недостаточно информации; вывод о совместимости не сделан.'),
            'checked_at': gap['checked_at'] if gap else None,
            'sources': [catalog.SOURCES[s] for s in gap['sources']] if gap else []})
    for med in context["medicines"]:
        left = date.fromisoformat(med["start"]) if med["start"] else date.min
        right = date.fromisoformat(med["end"]) if med["end"] else date.max
        overlap = start <= right and end >= left
        other = catalog.BY_CODE.get(med["code"])
        if med.get("editing"):
            if candidate and other and candidate["code"] == other["code"]:
                continue
            # A replacement must not erase a dose already taken or its interval.
            right = min(right, timezone.localdate())
            overlap = start <= right and end >= left
        if right < left:
            continue
        intervals = [w for w in catalog.WASHOUT_RULES if candidate and other and med['end']
            and w['candidate'] in candidate['tags'] and w['previous'] in other['tags']
            and right < start and (start-right).days <= w['days']]
        if not overlap and not intervals:
            continue
        if not candidate or not other:
            unknown_pair(med, other)
            continue
        if overlap:
            rules = catalog.pair_rules(candidate, other)
        else:
            rules = []
            for interval in intervals:
                original = next(r for r in catalog.RULES if r['id'] == interval['rule'])
                rules.append({**original, 'level': interval['level'], '_interval': interval,
                              'detail': interval.get('detail', original['detail'])})
        if candidate["code"] == other["code"] and overlap:
            findings.append({"id": "duplicate:"+med["id"], "level": "warning", "medicine": med["name"],
                "message": "Этот препарат уже есть в списке", "detail": "Проверьте, не дублируется ли приём. Если это изменение действующего назначения, откройте его редактирование.", "sources": [], "alternatives": []})
        if not rules and candidate["code"] != other["code"]:
            unknown_pair(med, other)
        for rule in rules:
            detail = rule["detail"]
            if rule.get('_interval'):
                interval = rule['_interval']
                detail += f" Последний день приёма: {right:%d.%m.%Y}."
                if interval['level'] == 'blocked':
                    detail += f" При учёте {interval['days']} полных календарных дней — не раньше {right+timedelta(days=interval['days']+1):%d.%m.%Y}. Остальные ограничения проверяются отдельно."
            source_ids = list(rule["source_ids"])
            if rule["id"] == "aviandr-mao" and (candidate["code"] == "linezolid" or other["code"] == "linezolid"):
                source_ids.append("linezolid")
            findings.append({"id": rule["id"]+":"+med["id"], "level": rule["level"], "medicine": med["name"],
                "message": rule["message"], "detail": detail, "sources": [catalog.SOURCES[s] for s in source_ids],
                "alternatives": list(rule["alternatives"]), 'checked_at': '27.10.2026'})
    level = max((f["level"] for f in findings), key=lambda l: RANK[l], default="unknown")
    findings.sort(key=lambda f: -RANK[f["level"]])
    summary = (findings[0]["medicine"]+" — "+findings[0]["message"]) if findings else "Недостаточно информации для вывода о совместимости"
    payload = {"version": catalog.VERSION, "candidate": candidate["code"] if candidate else catalog.normalize(name),
               "start": start.isoformat(), "end": end.isoformat(), "context": context}
    token = salted_hmac("medication-review", json.dumps(payload, ensure_ascii=False, sort_keys=True)).hexdigest()
    return {"level": level, "label": LABELS[level], "summary": summary, "findings": findings,
        "unknown": sorted(set(unknown)), "uncertainty": uncertainty, "recognized": candidate["name"] if candidate else None,
        "code": candidate["code"] if candidate else "", "needs_ack": any(f["level"] == "warning" for f in findings),
        "token": token, "catalog_version": catalog.VERSION, "start": start.isoformat(), "end": end.isoformat(),
        "coverage": "Проверены только внесённые лекарства и отдельные сочетания из источников. Не все сочетания и интервалы отмены покрыты правилами. Аллергии, заболевания и прочие противопоказания здесь не оцениваются."}


def picker(patient, name="", start=None, end=None, exclude=""):
    start = start or timezone.localdate()
    end = end or start
    context = medication_context(patient, exclude)
    selected = review(name, start, end, context)
    options = []
    for drug in catalog.CATALOG:
        check = review(drug["name"], start, end, context)
        options.append({"code": drug["code"], "name": drug["name"], "group": drug["group"], "aliases": list(drug["aliases"]),
            "level": check["level"], "label": check["label"], "summary": check["summary"]})
    alternatives = sorted({code for finding in selected["findings"] for code in finding["alternatives"]})
    return {"selected": selected, "options": options, "alternatives": [o for o in options if o["code"] in alternatives]}


def enforce_review(patient, data):
    check = review(data["name"], data["start"], data["end"], medication_context(patient, data.get("medication_id")))
    if check["level"] == "blocked":
        raise DomainError("medication_contraindicated")
    if check["needs_ack"]:
        if not data.get("interaction_ack"):
            raise DomainError("medication_review_required")
        if not constant_time_compare(str(data.get("interaction_token", "")), check["token"]):
            raise DomainError("medication_context_changed")
    return {**check, "acknowledged": bool(data.get("interaction_ack"))}
