"""MVP-2 persistence. No inference, dose selection or background processing."""
import uuid
from datetime import date, datetime, time, timedelta
from django.db import transaction
from django.utils import timezone
from quiz.models import ClinicalAction, PrescriptionVersion, MedicationIntake
from .access import require_patient_access, has_role
from .contracts import DomainError


def latest_plan(patient):
    return PrescriptionVersion.objects.filter(patient=patient).order_by("-version").first()


def append_once(model, *, key, **fields):
    old = model.objects.filter(key=key).first()
    if old:
        if any(getattr(old, field) != value for field, value in fields.items()):
            raise DomainError("idempotency_conflict")
        return old
    return model.objects.create(key=key, **fields)


@transaction.atomic
def save_action(actor, patient, data, *, kind, stop_id=None):
    require_patient_access(actor, patient.pk, clinical_write=True)
    from quiz.models import Profile
    Profile.objects.select_for_update().get(user=patient)
    old = ClinicalAction.objects.filter(idempotency_key=data["key"]).first()
    # A retry refers to exactly one confirmed action, even after a later plan change.
    if old:
        if old.patient_id != patient.pk or old.clinician_id != actor.pk or old.kind != kind or old.reason != data["reason"] or old.expected_plan_version != data["expected_version"]:
            raise DomainError("idempotency_conflict")
        if kind == "prescription":
            plan = PrescriptionVersion.objects.get(action=old)
            if plan.definition["request"] != prescription_request(data):
                raise DomainError("idempotency_conflict")
        if kind == "stop" and PrescriptionVersion.objects.get(action=old).definition["stopped"] != str(stop_id):
            raise DomainError("idempotency_conflict")
        return old
    current = latest_plan(patient)
    version = current.version if current else 0
    if version != data["expected_version"]:
        raise DomainError("stale_version")
    if current and current.definition.get("schema") != "medicines-v1":
        raise DomainError("unsupported_plan")
    items = list(current.definition.get("medicines", [])) if current else []
    definition = {"schema": "medicines-v1", "medicines": items}
    if kind == "prescription":
        from .medication_review import enforce_review
        definition["interaction_review"] = enforce_review(patient, data)
        med = prescription_request(data)
        old_id = med.pop("medication_id")
        if old_id and not any(m["id"] == old_id for m in items):
            raise DomainError("invalid_input")
        items[:] = [m for m in items if m["id"] != old_id]
        items.append({**med, "id": str(uuid.uuid4())})
        definition["request"] = prescription_request(data)
    elif kind == "stop":
        if not any(m["id"] == str(stop_id) for m in items):
            raise DomainError("invalid_input")
        items[:] = [m for m in items if m["id"] != str(stop_id)]
        definition["stopped"] = str(stop_id)
    action = ClinicalAction.objects.create(patient=patient, clinician=actor, proposal_id=None,
        proposal_version=None, expected_plan_version=version, kind=kind, decision="confirmed",
        reason=data["reason"], idempotency_key=data["key"])
    if kind in {"prescription", "stop"}:
        PrescriptionVersion.objects.create(patient=patient, version=version+1, action=action, definition=definition)
    return action


def prescription_request(data):
    return {"medication_id": str(data.get("medication_id") or ""), "name": data["name"],
        "dose": str(data["dose"]), "unit": data["unit"], "times": data["times"],
        "start": data["start"].isoformat(), "end": data["end"].isoformat(), "note": data["note"]}


def schedule(patient, day):
    """Use the version in effect at each scheduled time; never rewrite past slots."""
    versions = list(PrescriptionVersion.objects.filter(patient=patient).order_by("created_at", "version"))
    rows = []
    for index, plan in enumerate(versions):
        until = versions[index+1].created_at if index+1 < len(versions) else None
        for med in plan.definition.get("medicines", []):
            if not date.fromisoformat(med["start"]) <= day <= date.fromisoformat(med["end"]):
                continue
            for slot in med["times"]:
                due = timezone.make_aware(datetime.combine(day, time.fromisoformat(slot)))
                if due < plan.created_at or (until and due >= until):
                    continue
                rows.append({"prescription": plan.pk, "medication_id": med["id"], "medicine": med,
                    "due_at": due, "status": "", "status_label": "Нет отметки", "can_mark": due <= timezone.now()})
    marks = {(m.prescription_id, str(m.medication_id), m.due_at): m for m in MedicationIntake.objects.filter(prescription__patient=patient, due_at__date=day)}
    for row in rows:
        mark = marks.get((row["prescription"], row["medication_id"], row["due_at"]))
        if mark:
            row.update(status=mark.status, status_label=mark.get_status_display())
    return sorted(rows, key=lambda r: r["due_at"])


@transaction.atomic
def mark_intake(actor, data):
    if not has_role(actor, "patient"):
        raise DomainError("forbidden")
    if not PrescriptionVersion.objects.filter(pk=data["prescription"], patient=actor).exists():
        raise DomainError("forbidden")
    day = timezone.localtime(data["due_at"]).date()
    rows = schedule(actor, day)
    if not any(r["prescription"] == data["prescription"] and r["medication_id"] == str(data["medication_id"]) and r["due_at"] == data["due_at"] and r["can_mark"] for r in rows):
        raise DomainError("invalid_input")
    return MedicationIntake.objects.update_or_create(prescription_id=data["prescription"], medication_id=data["medication_id"], due_at=data["due_at"], defaults={"status": data["status"]})
