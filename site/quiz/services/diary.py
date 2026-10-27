"""Local author diary; immutable snapshots use the existing survey-v2 engine.

Editorial period: today, except D07 (previous night). No scoring/publication.
"""
import sqlite3
import uuid
from datetime import datetime
from django.conf import settings
from django.db import connections, transaction
from quiz.models import Questionnaire, QuestionnaireVersion, ContentVersion
from quiz.services.questionnaires import validate_definition
from quiz.services.contracts import DomainError

CODE = "author-diary"
TITLE = "Дневник самочувствия"


def definition():
    # Wording and options transcribed in docs/clinical/content-register.md.
    common = ["совсем нет", "немного", "умеренно", "сильно"]
    rows = [
        ("D01", "Мне было трудно испытывать удовольствие от того, что обычно радует (еда, общение, хобби)", common),
        ("D02", "Я чувствовал себя подавленным, угнетённым или безнадёжным", common),
        ("D03", "Я чувствовал себя никчёмным или виноватым перед близкими", common),
        ("D04", "Я чувствовал нервозность, напряжение или был «на взводе»", common),
        ("D05", "Мне было трудно остановить или контролировать беспокойные мысли", common),
        ("D06", "Я ощущал мышечное напряжение, дрожь или учащённое сердцебиение без физической причины", common),
        ("D07", "Этой ночью я спал(а)...", ["нормально/достаточно", "немного труднее заснуть/просыпался, но выспался", "выраженные трудности (долго засыпал, частые пробуждения)", "почти не спал / спал слишком много"]),
        ("D08", "Мой аппетит изменился (снизился или чрезмерно повысился)", ["нет", "незначительно", "заметно", "резко (пропуск приёмов пищи или переедание)"]),
        ("D09", "Я чувствовал упадок сил, который не проходил после отдыха", ["нет", "небольшая усталость", "выраженная слабость", "не мог встать с постели/делать простые дела"]),
        ("D10", "Приём лекарств", ["принял все дозы по схеме", "пропустил 1 дозу", "пропустил 2+ доз", "не принимал / сам изменил дозу"]),
        ("D11", "Эмоциональное перенапряжение на работе/учёбе", ["не было", "лёгкий стресс", "сильный стресс (конфликт, дедлайн)", "перегрузка, ощущение «не справляюсь»"]),
        ("D12", "Конфликты или напряжение в отношениях (семья, друзья)", ["нет", "незначительное", "заметная ссора", "серьёзный конфликт / изоляция"]),
        ("D13", "У меня возникали мысли о том, что мне лучше умереть или причинить себе вред", ["нет", "мимолётные мысли, но без планов", "периодические мысли, есть намерение", "есть конкретный план или подготовка"]),
        ("A01", "Я чувствовал(а) необычный прилив энергии или активности (больше, чем обычно для меня)", ["нет", "немного", "заметно", "сильно"]),
        ("A02", "Мне требовалось меньше сна, чем обычно, и я чувствовал(а) себя отдохнувшим(ей)", ["спал(а) как обычно", "немного меньше", "значительно меньше (на 2+ часа)", "почти не спал(а), но полон(на) сил"]),
        ("A03", "Мои мысли скакали, или я говорил(а) быстрее/больше обычного", ["нет", "слегка", "заметно", "сильно, трудно остановиться"]),
        ("A04", "Я был(а) необычно раздражительным(ой) или вспыльчивым(ой)", ["нет", "немного", "заметно", "сильно (ссоры, конфликты)"]),
    ]
    return validate_definition({"schema": "survey-v2", "instruction": "Отвечайте за сегодняшний день. Вопрос о сне относится к прошедшей ночи. Выберите один ответ на каждый вопрос; затем заполните дополнительный блок.",
        "recall_window": "За сегодняшний день; D07 — прошедшая ночь.",
        "source": "опрос-1; docs/clinical/content-register.md, author-diary-transcription-1. Редакция периода для локального MVP; D10/D11: добавлено «Не применимо». Авторский дневник, клиническая валидация не заявляется.",
        "completeness": "all_required", "questions": [
            {"code": code, "text": text, "required": True,
             "options": [{"code": f"{code}.{n}", "label": f"{n} — {label}"} for n, label in enumerate(options)],
             "allow_not_applicable": code in {"D10", "D11"}, "applicable_when": None,
             "safety_relevant": code == "D13"} for code, text, options in rows]})


def backup_database():
    """Online SQLite backup before any local preparation writes, including UI."""
    database = connections["default"].settings_dict["NAME"]
    if str(database).startswith("file:memory") or str(database) == ":memory:":
        return None
    folder = settings.BASE_DIR.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"before-diary-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3"
    with sqlite3.connect(str(database)) as source, sqlite3.connect(str(path)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Database backup integrity check failed")
    return path


@transaction.atomic
def prepare():
    if not settings.DEMO_SURVEY_ENABLED:
        raise DomainError("clinical_publication_disabled")
    content_definition = definition()
    questionnaire, _ = Questionnaire.objects.get_or_create(code=CODE, defaults={"title": TITLE})
    versions = QuestionnaireVersion.objects.filter(questionnaire=questionnaire).select_related("content").order_by("-number")
    for version in versions:
        if version.content.definition == content_definition and version.status == "demo":
            return version
    last = versions.first()
    number = last.number + 1 if last else 1
    content = ContentVersion.objects.create(code="survey-" + CODE, version=number, kind="questionnaire",
        definition=content_definition, source_snapshot={"source": content_definition["source"]})
    return QuestionnaireVersion.objects.create(questionnaire=questionnaire, number=number, content=content, status="demo")
