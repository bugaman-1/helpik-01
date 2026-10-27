"""PHQ-9/GAD-7 structure with editorial Russian wording, not a validated translation.

Separate standard sums and reference bands; adapted wording is a prototype.
No diagnosis or prediction of suicide risk. Previous snapshots are retained.
"""
import uuid
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from quiz.models import Profile, Questionnaire, QuestionnaireVersion, ContentVersion, SurveySession
from quiz.services import questionnaires as engine
from quiz.services.contracts import DomainError

CODE = "phq9-gad7-adapted"
TITLE = "Самочувствие за две недели"
SOURCE = "PHQ-9 / GAD-7, Kroenke, Spitzer, Williams, Löwe. https://uhs.fsu.edu/sites/g/files/upcbnu1651/files/docs/PHQ-9%20and%20GAD-7%20Form_a.pdf . Редакционная русская адаптация для прототипа; не валидированный перевод."
SCORING_SOURCE = "https://www.phqscreeners.com/images/sites/g/files/g10016261/f/201412/instructions.pdf"
CONTROL_CODE = "CHECK01"
CONTROL_PAIR = "GAD02"


def severity(score, scale):
    if score is None:
        return None
    bands = [(0, "Минимальная"), (5, "Лёгкая"), (10, "Умеренная"), (15, "Умеренно тяжёлая"), (20, "Тяжёлая")] if scale == "phq" else [(0, "Минимальная"), (5, "Лёгкая"), (10, "Умеренная"), (15, "Выраженная")]
    lower, label = next((lo, name) for lo, name in reversed(bands) if score >= lo)
    return {"label": label, "level": "high" if score >= 15 else "review" if score >= 10 else "low", "lower": lower}


def definition():
    phq = [
        "Вам было трудно проявлять интерес к делам или получать от них удовольствие?",
        "Вы чувствовали подавленность, грусть или ощущение безнадёжности?",
        "Вам было трудно заснуть, сон прерывался или вы спали слишком много?",
        "Вы чувствовали усталость или нехватку сил?",
        "У вас был плохой аппетит или вы ели слишком много?",
        "Вы плохо думали о себе — чувствовали себя неудачником или считали, что подвели себя или близких?",
        "Вам было трудно сосредоточиться, например на чтении или просмотре передач?",
        "Вы двигались или говорили так медленно, что это могли заметить другие? Или, наоборот, были настолько беспокойны, что двигались гораздо больше обычного?",
        "Возникали мысли, что лучше было бы не жить, или мысли о том, чтобы причинить себе вред?",
    ]
    gad = [
        "Вы чувствовали нервозность, тревогу или внутреннее напряжение?",
        "Вам было трудно остановить или контролировать беспокойство?",
        "Вы слишком сильно беспокоились о разных вещах?",
        "Вам было трудно расслабиться?",
        "Вы чувствовали такое беспокойство, что было трудно усидеть на месте?",
        "Вы легко раздражались или сердились?",
        "Вы чувствовали страх, будто может случиться что-то ужасное?",
    ]
    rows = [(f"PHQ{n:02}", text) for n, text in enumerate(phq, 1)] + [(f"GAD{n:02}", text) for n, text in enumerate(gad, 1)]
    # Additional consistency item is never part of either clinical sum.
    rows.append((CONTROL_CODE, "Тревожные мысли возвращались, и вам было трудно перестать беспокоиться?"))
    options = ["Ни разу", "Несколько дней", "Более половины дней", "Почти каждый день"]
    return engine.validate_definition({"schema": "survey-v2", "instruction": "Вспомните последние две недели. Как часто вас беспокоило следующее? Здесь нет правильных или неправильных ответов — выбирайте то, что ближе к вашему опыту.",
        "recall_window": "Последние две недели", "source": SOURCE, "completeness": "all_required",
        "questions": [{"code": code, "text": text, "required": True,
                       "options": [{"code": f"{code}.{n}", "label": label} for n, label in enumerate(options)],
                       "allow_not_applicable": False, "applicable_when": None, "safety_relevant": code == "PHQ09"} for code, text in rows]})


@transaction.atomic
def prepare():
    if not settings.DEMO_SURVEY_ENABLED:
        raise DomainError("clinical_publication_disabled")
    questionnaire, _ = Questionnaire.objects.get_or_create(code=CODE, defaults={"title": TITLE})
    Questionnaire.objects.select_for_update().get(pk=questionnaire.pk)
    data = definition()
    versions = QuestionnaireVersion.objects.filter(questionnaire=questionnaire).select_related("content").order_by("-number")
    for version in versions:
        if version.content.definition == data and version.status == "demo":
            return version
    latest = versions.first()
    number = latest.number + 1 if latest else 1
    content = ContentVersion.objects.create(code="survey-" + CODE, version=number, kind="questionnaire", definition=data, source_snapshot={"source": SOURCE})
    return QuestionnaireVersion.objects.create(questionnaire=questionnaire, content=content, number=number, status="demo")


@transaction.atomic
def start(patient):
    if not engine.has_role(patient, "patient"):
        raise DomainError("forbidden")
    Profile.objects.select_for_update().get(user=patient)
    existing = SurveySession.objects.filter(patient=patient, questionnaire_version__questionnaire__code=CODE).filter(status__in=["draft", "in_progress"]).order_by("-started_at").first()
    if existing:
        return existing
    # Repeated clicks return today's completed assessment, instead of duplicating it.
    existing = SurveySession.objects.filter(patient=patient, questionnaire_version__questionnaire__code=CODE, status="submitted", started_at__date=timezone.localdate()).first()
    if existing:
        return existing
    now = timezone.now()
    assignment = engine._assign(patient, patient, prepare(), key=uuid.uuid4(), timezone_name=settings.TIME_ZONE,
        available_from=now, available_until=now + timedelta(days=1), observation_start=now - timedelta(days=14),
        observation_end=now, window_code="self-screening", unscheduled_limit=0)
    run = engine.start(patient, assignment.pk, uuid.uuid4())
    run.telemetry_enabled = True
    run.save(update_fields=["telemetry_enabled"])
    return run


def results(run):
    from . import checkin
    if run and run.questionnaire_version.questionnaire.code == checkin.CODE:
        return checkin.results(run)
    if not run or run.questionnaire_version.questionnaire.code != CODE:
        return None
    answers = engine.current_answers(run)
    totals = {}
    for prefix, count in (("PHQ", 9), ("GAD", 7)):
        values = []
        for n in range(1, count + 1):
            answer = answers.get(f"{prefix}{n:02}")
            if answer and answer.state == "known" and answer.value in {f"{prefix}{n:02}.{v}" for v in range(4)}:
                values.append(int(answer.value[-1]))
        totals[prefix.lower()] = sum(values) if len(values) == count and run.status == "submitted" else None
    # Retain a safety response even if the patient later changes it.
    totals["safety"] = run.revisions.filter(question_code="PHQ09", state="known", value__in=["PHQ09.1", "PHQ09.2", "PHQ09.3"]).exists()
    totals["phq_band"] = severity(totals["phq"], "phq")
    totals["gad_band"] = severity(totals["gad"], "gad")
    totals["source"] = SCORING_SOURCE
    return totals
