"""Optional, bounded UI events. Client clocks are never clinical evidence."""
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from quiz.models import InteractionEvent
from .questionnaires import own_session, uid, integer, invalid
from .contracts import DomainError

KINDS = {"question_visible", "option_selected", "answer_changed", "question_left",
         "question_revisited", "visibility_hidden", "visibility_visible", "page_resume"}
MAX_EVENTS, MAX_BATCH, PER_MINUTE, MAX_STREAMS = 2000, 32, 120, 8


@transaction.atomic
def consent(patient, session_id, enabled):
    if type(enabled) is not bool:
        invalid()
    run = own_session(patient, session_id, lock=True)
    run.telemetry_enabled = enabled
    run.save(update_fields=["telemetry_enabled"])
    return run


@transaction.atomic
def ingest(patient, session_id, events):
    run = own_session(patient, session_id, lock=True)
    if not run.telemetry_enabled:
        raise DomainError("telemetry_disabled")
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_BATCH:
        invalid()
    now = timezone.now()
    count = run.interactions.count()
    recent = run.interactions.filter(received_at__gte=now - timedelta(minutes=1)).count()
    streams = set(run.interactions.values_list("stream_id", flat=True).distinct())
    codes = {q["code"] for q in run.questionnaire_version.content.definition["questions"]}
    accepted = []
    for item in events:
        if not isinstance(item, dict) or set(item) != {"event_id", "stream_id", "sequence", "kind", "question_code", "revision_id", "client_at", "elapsed_ms", "save_latency_ms"}:
            invalid()
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS or item["question_code"] not in codes | {""}:
            invalid()
        if item["kind"] in {"question_visible", "option_selected", "answer_changed", "question_left", "question_revisited"} and not item["question_code"]:
            invalid()
        sequence = integer(item["sequence"], 100000)
        if sequence < 1:
            invalid()
        integer(item["elapsed_ms"], 7 * 86400000)
        if item["save_latency_ms"] is not None:
            integer(item["save_latency_ms"], 86400000)
        if not isinstance(item["client_at"], str):
            invalid()
        client_at = parse_datetime(item["client_at"])
        if client_at is None or not timezone.is_aware(client_at):
            invalid()
        revision_id = item["revision_id"]
        if revision_id is not None:
            integer(revision_id, 2**63-1)
            if not run.revisions.filter(pk=revision_id, question_code=item["question_code"]).exists():
                invalid()
        fields = dict(stream_id=uid(item["stream_id"]), sequence=sequence, kind=item["kind"],
            question_code=item["question_code"], revision_id=revision_id, client_at=client_at,
            elapsed_ms=item["elapsed_ms"], save_latency_ms=item["save_latency_ms"])
        event_id = uid(item["event_id"])
        old = run.interactions.filter(event_id=event_id).first()
        if old:
            if any(getattr(old, k) != v for k, v in fields.items()):
                raise DomainError("idempotency_conflict")
        else:
            if run.interactions.filter(stream_id=fields["stream_id"], sequence=sequence).exists():
                raise DomainError("idempotency_conflict")
            streams.add(fields["stream_id"])
            if count >= MAX_EVENTS or recent >= PER_MINUTE or len(streams) > MAX_STREAMS:
                raise DomainError("throttled")
            InteractionEvent.objects.create(session=run, event_id=event_id, **fields)
            count += 1
            recent += 1
        accepted.append(str(event_id))
    return accepted


def summarize(events, *, enabled):
    """Return observed closed intervals only; never fill missing tails with zero."""
    events = list(events)
    flags = {"client_clock_untrusted", "unobserved_tail_possible"}
    if not enabled:
        flags.add("telemetry_disabled")
    if not events:
        flags.add("no_events")
    streams, questions = {}, {}
    for event in events:
        streams.setdefault(str(event.stream_id), []).append(event)
    if len(streams) > 1:
        flags.add("multiple_streams_no_cross_stream_sum")
    for stream_id, rows in streams.items():
        rows.sort(key=lambda e: e.sequence)
        prev_seq, prev_time, visible, hidden_at = 0, None, None, None
        first_visible = {}
        for e in rows:
            if e.sequence != prev_seq + 1:
                flags.add("sequence_gap")
                visible, hidden_at = None, None
                first_visible.clear()
            if prev_time is not None and e.elapsed_ms < prev_time:
                flags.add("nonmonotonic_clock")
                visible, hidden_at = None, None
                first_visible.clear()
            # Client wall time is diagnostic only, not used for intervals.
            if abs((e.received_at - e.client_at).total_seconds()) > 300:
                flags.add("clock_skew_or_delayed_delivery")
            prev_seq, prev_time = e.sequence, e.elapsed_ms
            key = (stream_id, e.question_code)
            if e.question_code:
                item = questions.setdefault(key, {"stream_id": stream_id, "question_code": e.question_code,
                    "first_choice_ms": None, "visible_ms": None, "background_ms": None, "save_latency_ms": None})
                if e.save_latency_ms is not None:
                    item["save_latency_ms"] = e.save_latency_ms
            if e.kind in {"question_visible", "question_revisited", "visibility_visible"}:
                if e.question_code:
                    if hidden_at and hidden_at[0] == e.question_code:
                        duration = e.elapsed_ms - hidden_at[1]
                        if duration >= 0:
                            item["background_ms"] = (item["background_ms"] or 0) + duration
                    if visible is not None:
                        flags.add("unclosed_visible_interval")
                    hidden_at = None
                    visible = (e.question_code, e.elapsed_ms)
                    first_visible.setdefault(e.question_code, e.elapsed_ms)
            elif e.kind in {"option_selected", "answer_changed"} and e.question_code in first_visible:
                if item["first_choice_ms"] is None:
                    # Count visible time only; a hidden tab is not response time.
                    if visible and visible[0] == e.question_code:
                        item["first_choice_ms"] = (item["visible_ms"] or 0) + max(0, e.elapsed_ms - visible[1])
            elif e.kind in {"question_left", "visibility_hidden"}:
                if visible and visible[0] == e.question_code:
                    duration = e.elapsed_ms - visible[1]
                    if duration >= 0:
                        item["visible_ms"] = (item["visible_ms"] or 0) + duration
                else:
                    flags.add("missing_interval_start")
                visible = None
                if e.kind == "visibility_hidden":
                    hidden_at = (e.question_code, e.elapsed_ms)
        if visible or hidden_at:
            flags.add("open_interval")
    return {"method": "paradata-v1", "quality_flags": sorted(flags), "questions": list(questions.values()),
            "interpretation": "Client estimates of closed intervals; visibility is not attention; no clinical inference."}
